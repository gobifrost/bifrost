"""
Supervised service claim loop (Slice 3, worker-pull per §18.4).

Runs inside the worker container next to the RabbitMQ consumers. Each tick:

1. ``expire_leases`` — fail attempts whose owner stopped renewing and apply
   restart decisions (lease expiry is the worker-loss backstop).
2. Beat every locally owned attempt — renew the DB lease, drain the child's
   ``ready()`` report, mirror ``stop_requested`` to the child (Redis stop key
   + SIGTERM), and rotate the service credential.
3. Claim eligible services while service slots remain — the DB lease (fenced,
   partial-unique) owns the lifetime; RabbitMQ carries no assignment.

The scheduler leader only advances durable eligibility (already encoded in
``restart_eligible_at``/``blocked_reason`` at completion time); there is no
scheduler-to-worker assignment protocol.

Completions arrive from the pool via ``on_service_result`` and map child
outcomes to ``complete_attempt`` reasons. Stale-child results are rejected
by lease fencing (``StaleLeaseError``) and dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, cast
from uuid import UUID

from src.core.cache.keys import (
    service_ready_key,
    service_stop_key,
    service_token_key,
)

logger = logging.getLogger(__name__)


@dataclass
class OwnedAttempt:
    """One attempt this worker claimed and currently supervises."""

    attempt_id: UUID
    service_id: UUID
    lease_token: str
    stop_mirrored: bool = False


@dataclass
class ServiceClaimLoop:
    """Worker-pull claim loop for supervised services."""

    worker_id: str
    pool: object = None  # ProcessPoolManager (typed loosely: import cycle)
    claim_interval_seconds: float = 5.0
    beat_interval_seconds: float = 10.0
    lease_ttl_seconds: int = 60
    token_lifetime_seconds: int = 900
    # Bounded wait for in-flight terminal results during shutdown handover.
    # The DB lease (not this wait) owns the lifetime: anything still live
    # expires and restarts elsewhere.
    handover_wait_seconds: float = 5.0

    _owned: dict[str, OwnedAttempt] = field(default_factory=dict)
    _running: bool = False
    _task: asyncio.Task[None] | None = None
    _last_beat: float = 0.0
    # Staged flush cursors (attempt_id -> last stream ID), written to Redis
    # only after the tick's commit — never before (see service_log_flush).
    _pending_cursors: dict[str, str] = field(default_factory=dict)
    # Attempt IDs whose cursors the completion path has taken over (pop +
    # clear). A store in flight for a taken-over attempt must drop its
    # staged write instead of resurrecting the cleared key: the attempt is
    # terminal, so nothing will ever read it again. Guarded by
    # ``_cursor_lock`` against the completion path (same loop, separate
    # task). Stale-lease drops do NOT join this set — their staged cursor
    # is still the freshest resume point for the next owner.
    _completed_cursors: set[str] = field(default_factory=set)
    _cursor_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # -- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        """Begin ticking in the background (pool must already be started)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="service-claim-loop")

    async def stop(self) -> None:
        """Stop claiming and hand owned attempts over gracefully.

        Mirrors stop to every owned child (concurrently), waits for their
        terminal results up to one beat window, then drops ownership. The DB
        lease (not this method) owns the lifetime: anything still live
        expires and restarts elsewhere.
        """
        self._running = False
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                # Killed without handover (owner-death path): nothing to wait for.
                pass
            self._task = None
        await self._handover_owned()

    # -- tick ----------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception as e:
                logger.exception("service claim tick failed: %s", e)
            await asyncio.sleep(self.claim_interval_seconds)

    async def tick(self) -> None:
        """One claim-loop iteration: expire, beat owned, claim fill."""
        from src.core.database import get_db_context

        async with get_db_context() as db:
            from src.services import service_lifecycle as lifecycle

            expired = await lifecycle.expire_leases(db)
            if expired:
                logger.warning("expired %d service lease(s)", expired)
            await db.commit()

            await self._beat_owned(db)
            await db.commit()
            await self._store_cursors()

        await self._claim_available()

    # -- owned-attempt supervision -------------------------------------

    async def _beat_owned(self, db) -> None:
        """Renew leases, drain ready reports, mirror stops, rotate tokens."""
        import time as _time

        now = _time.monotonic()
        if now - self._last_beat < self.beat_interval_seconds:
            return
        self._last_beat = now

        from src.models.orm.services import ServiceDefinition
        from src.services import service_lifecycle as lifecycle

        for attempt_key in list(self._owned):
            owned = self._owned[attempt_key]
            try:
                attempt = await lifecycle.heartbeat_attempt(
                    db,
                    attempt_id=owned.attempt_id,
                    lease_token=owned.lease_token,
                    lease_ttl_seconds=self.lease_ttl_seconds,
                )
            except lifecycle.StaleLeaseError:
                logger.warning(
                    "lost ownership of attempt %s; dropping", attempt_key[:8]
                )
                await self._drop_ownership(owned)
                continue

            definition = await db.get(ServiceDefinition, owned.service_id)
            if await self._enforce_startup_grace(db, owned, attempt, definition):
                continue
            await self._drain_ready(db, owned)
            await self._mirror_stop(db, owned, attempt)
            await self._report_memory(owned)
            await self._rotate_token(db, owned, definition)
            await self._flush_logs(db, owned)

    async def _enforce_startup_grace(self, db, owned, attempt, definition) -> bool:
        """Fail attempts that never report ready within their grace.

        A live-but-never-ready child would otherwise renew its lease forever
        and squat a service slot. A grace breach completes as a restartable
        failure so the normal policy/backoff applies. Returns True when the
        attempt was failed (ownership dropped, beat done).
        """
        from datetime import datetime, timezone

        from src.services import service_lifecycle as lifecycle

        if attempt.state != "starting" or definition is None:
            return False
        started_at = attempt.started_at
        if started_at is None:
            return False
        grace = definition.startup_grace_seconds or 60
        now = datetime.now(timezone.utc)
        started = started_at if started_at.tzinfo else started_at.replace(
            tzinfo=timezone.utc
        )
        if (now - started).total_seconds() <= grace:
            return False
        logger.warning(
            "attempt %s never reported ready within %ss; failing",
            owned.attempt_id,
            grace,
        )
        try:
            await lifecycle.complete_attempt(
                db,
                attempt_id=owned.attempt_id,
                lease_token=owned.lease_token,
                reason="failed",
                exit_code=1,
                exit_reason="startup_grace_exceeded",
                error=f"Service did not report ready within {grace}s.",
            )
        except lifecycle.StaleLeaseError:
            # Lost the race: another owner already moved the attempt, so
            # our completion was fenced and there is nothing to roll back.
            pass
        await self._drop_ownership(owned)
        return True

    async def _drain_ready(self, db, owned: OwnedAttempt) -> None:
        """Consume the child's ready() report into the attempt row."""
        from src.core.cache import get_redis
        from src.services import service_lifecycle as lifecycle

        try:
            async with get_redis() as r:
                flag = await r.get(service_ready_key(str(owned.attempt_id)))
                if not flag:
                    return
                await r.delete(service_ready_key(str(owned.attempt_id)))
        except Exception as e:
            logger.debug("ready drain failed for %s: %s", owned.attempt_id, e)
            return
        try:
            # Same session as the tick: a second session would block on the
            # tick's uncommitted heartbeat row lock.
            await lifecycle.mark_attempt_ready(
                db,
                attempt_id=owned.attempt_id,
                lease_token=owned.lease_token,
            )
        except lifecycle.StaleLeaseError:
            await self._drop_ownership(owned)
        except Exception as e:
            logger.warning(
                "mark_attempt_ready failed for %s: %s", owned.attempt_id, e
            )

    async def _mirror_stop(self, db, owned: OwnedAttempt, attempt) -> None:
        """Mirror durable stop_requested to the child exactly once."""
        if attempt.stop_requested_at is None or owned.stop_mirrored:
            return
        owned.stop_mirrored = True
        try:
            from src.core.cache import get_redis

            async with get_redis() as r:
                await r.setex(
                    service_stop_key(str(owned.attempt_id)), 300, "1"
                )
        except Exception as e:
            logger.debug("stop mirror failed for %s: %s", owned.attempt_id, e)
        if self.pool is not None:
            try:
                await self.pool.stop_service_child(str(owned.attempt_id))
            except Exception as e:
                logger.warning(
                    "stop_service_child failed for %s: %s",
                    owned.attempt_id,
                    e,
                )

    async def _report_memory(self, owned: OwnedAttempt) -> None:
        """Publish one owned attempt's memory into the pool registration hash.

        The API serves live memory from these hashes (same precedent as
        worker packages); entries carry their own timestamp so readers can
        bound staleness. Removed on drop/completion; the hash itself expires
        with the pool registration.
        """
        if self.pool is None:
            return
        try:
            memory_mb = self.pool.get_service_memory_mb(str(owned.attempt_id))
        except Exception as e:
            logger.debug("service memory read failed for %s: %s", owned.attempt_id, e)
            return
        if memory_mb is None:
            return
        try:
            from datetime import datetime, timezone

            from src.core.cache import get_redis

            async with get_redis() as r:
                key = f"bifrost:pool:{self.worker_id}"
                raw = await cast(
                    Awaitable[str | None], r.hget(key, "services")
                )
                try:
                    entries = json.loads(raw) if raw else {}
                except (ValueError, TypeError):
                    entries = {}
                entries[str(owned.attempt_id)] = {
                    "memory_mb": memory_mb,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                await r.hset(key, "services", json.dumps(entries))  # type: ignore[misc]
        except Exception as e:
            logger.debug(
                "service memory publish failed for %s: %s", owned.attempt_id, e
            )

    async def _flush_logs(self, db, owned: OwnedAttempt) -> None:
        """Drain one owned attempt's stream tail (staging the cursor).

        Rows join the tick's transaction; the cursor is staged in
        ``_pending_cursors`` and written by ``_store_cursors`` only after
        the tick commits — a failed tick replays cleanly with no
        duplicates and no gaps.
        """
        from src.services import service_log_flush

        try:
            _, last_id = await service_log_flush.flush_attempt_logs(
                db, owned.service_id, owned.attempt_id
            )
            if last_id is not None:
                self._pending_cursors[str(owned.attempt_id)] = last_id
        except Exception as e:
            logger.debug(
                "service log beat flush failed for %s: %s",
                owned.attempt_id,
                e,
            )

    async def _store_cursors(self) -> None:
        """Persist staged flush cursors after the tick committed."""
        if not self._pending_cursors:
            return
        from src.services import service_log_flush

        pending, self._pending_cursors = self._pending_cursors, {}
        # Terminal attempt IDs are never re-staged, so a takeover entry
        # for anything outside this batch is unobservable — prune it
        # instead of letting the set grow with every completion.
        self._completed_cursors &= set(pending)
        for attempt_id, last_id in pending.items():
            async with self._cursor_lock:
                if attempt_id in self._completed_cursors:
                    # Completion took cursor ownership (and cleared the
                    # key) after this tick staged: drop the stale write
                    # instead of resurrecting it. The lock makes the
                    # check-and-store atomic against the completion's
                    # take-over-and-clear, so neither interleave leaves a
                    # stale key behind.
                    continue
                try:
                    stored = await service_log_flush.store_log_cursor(
                        UUID(attempt_id), last_id
                    )
                except Exception as e:
                    stored = False
                    logger.debug(
                        "service log cursor store failed for %s: %s",
                        attempt_id,
                        e,
                    )
            if not stored:
                # Redis was unreachable: retry next tick. The rows are
                # already committed, so a crash before the retry replays
                # a bounded duplicate tail (at-least-once).
                self._pending_cursors[attempt_id] = last_id

    async def _clear_reported_memory(self, attempt_id: str) -> None:
        """Remove one attempt from the published memory map."""
        if self.pool is None:
            return
        try:
            from src.core.cache import get_redis

            async with get_redis() as r:
                key = f"bifrost:pool:{self.worker_id}"
                raw = await cast(
                    Awaitable[str | None], r.hget(key, "services")
                )
                if not raw:
                    return
                try:
                    entries = json.loads(raw)
                except (ValueError, TypeError):
                    return
                if entries.pop(str(attempt_id), None) is not None:
                    await r.hset(key, "services", json.dumps(entries))  # type: ignore[misc]
        except Exception as e:
            logger.debug(
                "service memory cleanup failed for %s: %s", attempt_id, e
            )

    async def _rotate_token(self, db, owned: OwnedAttempt, definition=None) -> None:
        """Mint a fresh service credential and hand it to the child."""
        from src.core.cache import get_redis
        from src.core.security import mint_service_token
        from src.models.orm.services import ServiceDefinition

        if definition is None:
            definition = await db.get(ServiceDefinition, owned.service_id)
        if definition is None or not definition.organization_id:
            return
        token, expires_at = mint_service_token(
            service_id=str(owned.service_id),
            attempt_id=str(owned.attempt_id),
            organization_id=str(definition.organization_id),
            solution_id=(
                str(definition.solution_id)
                if definition.solution_id
                else None
            ),
            global_repo_access=False,
            lifetime_seconds=self.token_lifetime_seconds,
        )
        try:
            async with get_redis() as r:
                await r.setex(
                    service_token_key(str(owned.attempt_id)),
                    self.token_lifetime_seconds + 300,
                    json.dumps({"token": token, "expires_at": expires_at}),
                )
        except Exception as e:
            logger.debug(
                "token rotation failed for %s: %s", owned.attempt_id, e
            )

    async def _drop_ownership(self, owned: OwnedAttempt) -> None:
        """Forget an attempt and revoke its rotation channel."""
        self._owned.pop(str(owned.attempt_id), None)
        await self._clear_reported_memory(str(owned.attempt_id))
        try:
            from src.core.cache import get_redis

            async with get_redis() as r:
                await r.delete(service_token_key(str(owned.attempt_id)))
        except Exception as e:
            logger.debug(
                "token key cleanup failed for %s: %s", owned.attempt_id, e
            )
        if self.pool is not None:
            try:
                await self.pool.stop_service_child(str(owned.attempt_id))
            except Exception:
                # Best-effort child stop during ownership drop: the DB
                # lease expiry (not this signal) owns the lifetime.
                pass

    # -- claiming ------------------------------------------------------

    def _service_capacity(self) -> int:
        if self.pool is None:
            return 0
        return max(
            0,
            self.pool.max_service_workers
            - len(self.pool.service_processes),
        )

    async def _claim_available(self) -> None:
        """Claim eligible services while local slots remain (worker-pull).

        The durable claim commits BEFORE the child forks: a fast outcome
        (load failure, instant return) completes through ``handle_service_result``
        in its own session, which can only see committed attempts. A claim
        whose child then fails to launch completes without failure
        accounting (``requested``/``route_failed``) so the service relaunches
        promptly instead of leaking a childless live attempt.
        """
        from src.core.database import get_db_context
        from src.services import service_lifecycle as lifecycle

        while self._service_capacity() > 0:
            async with get_db_context() as db:
                try:
                    attempt = await lifecycle.claim_eligible_service(
                        db,
                        worker_id=self.worker_id,
                        lease_ttl_seconds=self.lease_ttl_seconds,
                    )
                    if attempt is None:
                        await db.rollback()
                        return
                    # Durable before fork: completions must observe this row.
                    await db.commit()
                    # Read back inside the session: commit expires attributes.
                    attempt_id, lease_token, service_id = (
                        attempt.id,
                        attempt.lease_token,
                        attempt.service_id,
                    )
                except lifecycle.ServiceClaimConflict:
                    await db.rollback()
                    return
                except Exception as e:
                    logger.warning("service claim failed: %s", e)
                    await db.rollback()
                    return
            try:
                async with get_db_context() as db:
                    # Re-attach by id: the claim above committed in a closed
                    # session, so the ORM object is detached here.
                    from src.models.orm.services import ServiceAttempt

                    fresh = await db.get(ServiceAttempt, attempt_id)
                    if fresh is None:
                        return
                    await self._launch_claimed(db, fresh)
                    await db.commit()
            except lifecycle.ServiceClaimConflict:
                return
            except Exception as e:
                logger.warning(
                    "service launch failed for %s: %s", attempt_id, e
                )
                await self._complete_route_failure(
                    attempt_id, lease_token, service_id
                )
                return

    async def _complete_route_failure(
        self, attempt_id, lease_token: str, service_id
    ) -> None:
        """Complete a committed claim whose child never launched."""
        from src.core.database import get_db_context
        from src.services import service_lifecycle as lifecycle

        try:
            async with get_db_context() as db:
                try:
                    await lifecycle.complete_attempt(
                        db,
                        attempt_id=attempt_id,
                        lease_token=lease_token,
                        reason="requested",
                        exit_code=1,
                        exit_reason="route_failed",
                        error="Owning worker could not fork the child.",
                    )
                except lifecycle.StaleLeaseError:
                    await db.rollback()
                    return
                await db.commit()
        except Exception as e:
            logger.warning(
                "route-failure completion failed for %s: %s", attempt_id, e
            )

    async def _launch_claimed(self, db, attempt) -> None:
        """Route one claimed attempt to a service child (savepoint scope)."""
        from src.core.cache import get_redis
        from src.core.security import mint_service_token
        from src.models.orm.services import ServiceDefinition
        from src.repositories.organizations import OrganizationRepository
        from src.services.execution.service import (
            WorkflowNotFoundError,
            get_workflow_for_execution,
        )

        definition = await db.get(ServiceDefinition, attempt.service_id)
        if definition is None:
            raise RuntimeError(f"definition {attempt.service_id} vanished")
        if not definition.organization_id:
            # D3: services are always org-scoped; org-less definitions are
            # never eligible (claim_eligible_service filters them).
            raise RuntimeError(
                f"definition {attempt.service_id} has no organization"
            )

        try:
            workflow_data = await get_workflow_for_execution(
                str(definition.workflow_id), db=db
            )
        except WorkflowNotFoundError as e:
            raise RuntimeError(f"service source gone: {e}") from e
        if workflow_data.get("type") != "service":
            raise RuntimeError(
                f"source {definition.workflow_id} is not a service"
            )

        org = await OrganizationRepository(db).get_with_cache(
            str(definition.organization_id)
        )
        org_data = (
            {
                "id": str(org.id),
                "name": org.name,
                "is_active": org.is_active,
                "is_provider": org.is_provider,
            }
            if org
            else None
        )
        short_id = str(definition.id).replace("-", "")[:12]
        token, expires_at = mint_service_token(
            service_id=str(definition.id),
            attempt_id=str(attempt.id),
            organization_id=str(definition.organization_id),
            solution_id=(
                str(definition.solution_id)
                if definition.solution_id
                else None
            ),
            global_repo_access=bool(
                workflow_data.get("can_access_global_repo", False)
            ),
            lifetime_seconds=self.token_lifetime_seconds,
        )
        context = {
            "execution_id": str(attempt.id),
            "name": workflow_data["name"],
            "function_name": workflow_data["function_name"],
            "file_path": workflow_data["path"],
            "content_hash": definition.current_revision,
            "caller": {
                "user_id": "00000000-0000-0000-0000-000000000001",
                "email": f"service-{short_id}@bifrost.internal",
                "name": f"service-{short_id}",
            },
            "organization": org_data,
            "timeout_seconds": 0,
            "solution_id": workflow_data.get("solution_id"),
            "solution_global_repo_access": bool(
                workflow_data.get("can_access_global_repo", False)
            ),
            "service": {
                "service_id": str(definition.id),
                "attempt_id": str(attempt.id),
                "lease_token": attempt.lease_token,
                "revision": attempt.revision,
                "graceful_shutdown_seconds": definition.graceful_shutdown_seconds,
                "startup_grace_seconds": definition.startup_grace_seconds,
                "token": token,
                "token_expires_at": expires_at,
            },
        }
        await self.pool.route_service(
            service_id=str(definition.id),
            attempt_id=str(attempt.id),
            lease_token=attempt.lease_token,
            context=context,
            graceful_shutdown_seconds=definition.graceful_shutdown_seconds,
        )
        async with get_redis() as r:
            await r.setex(
                service_token_key(str(attempt.id)),
                self.token_lifetime_seconds + 300,
                json.dumps({"token": token, "expires_at": expires_at}),
            )
        self._owned[str(attempt.id)] = OwnedAttempt(
            attempt_id=attempt.id,
            service_id=definition.id,
            lease_token=attempt.lease_token,
        )
        logger.info(
            "worker %s launched service %s (attempt %s)",
            self.worker_id,
            definition.id,
            attempt.id,
        )

    # -- completions ---------------------------------------------------

    async def handle_service_result(self, envelope: dict) -> None:
        """Complete the attempt for one child outcome (pool callback)."""
        from src.core.database import get_db_context
        from src.services import service_lifecycle as lifecycle

        service_ref = envelope.get("service") or {}
        attempt_id = service_ref.get("attempt_id") or envelope.get("attempt_id")
        lease_token = service_ref.get("lease_token", "")
        if not attempt_id:
            logger.error("service result without attempt identity: %s", envelope)
            return
        try:
            attempt_uuid = UUID(str(attempt_id))
        except ValueError:
            logger.error("service result with bad attempt id: %r", attempt_id)
            return

        committed = False
        try:
            async with get_db_context() as db:
                from src.models.orm.services import ServiceAttempt

                attempt = await db.get(ServiceAttempt, attempt_uuid)
                if attempt is None:
                    return
                reason, exit_reason, error = self._completion_for(
                    envelope, attempt
                )
                try:
                    await lifecycle.complete_attempt(
                        db,
                        attempt_id=attempt_uuid,
                        lease_token=lease_token,
                        reason=reason,
                        exit_code=0 if reason != "failed" else 1,
                        exit_reason=exit_reason,
                        error=error,
                    )
                except lifecycle.StaleLeaseError:
                    logger.warning(
                        "late result for superseded attempt %s; dropping",
                        attempt_id,
                    )
                    await db.rollback()
                    return
                # Final drain so the tail (<1 beat old) persists with the
                # terminal attempt instead of waiting for the next beat.
                # No cursor staging here: the cursor is cleared below once
                # this transaction commits.
                from src.services import service_log_flush

                try:
                    await service_log_flush.flush_attempt_logs(
                        db, attempt.service_id, attempt_uuid
                    )
                except Exception as e:
                    logger.debug(
                        "service log final drain failed for %s: %s",
                        attempt_id,
                        e,
                    )
                await db.commit()
                committed = True
        except Exception as e:
            logger.exception(
                "service completion failed for %s: %s", attempt_id, e
            )
            return
        finally:
            owned = self._owned.pop(str(attempt_id), None)
            await self._clear_reported_memory(str(attempt_id))
            # Drop any cursor staged by an earlier beat for this attempt:
            # the completion path owns the cursor from here.
            self._pending_cursors.pop(str(attempt_id), None)
            if committed:
                # Clear only after a successful commit. A failed commit
                # leaves rows rolled back and the cursor untouched, so a
                # later owner replays them instead of skipping.
                try:
                    from src.services import service_log_flush as _slf

                    async with self._cursor_lock:
                        # Take cursor ownership before clearing so an in-flight
                        # _store_cursors drops its staged write instead of
                        # resurrecting the key (see _store_cursors).
                        self._completed_cursors.add(str(attempt_id))
                        await _slf.clear_log_cursor(attempt_uuid)
                except Exception:
                    # Best-effort cursor cleanup after commit: rows are
                    # durable, and a leftover key expires via TTL.
                    pass
            if owned is not None:
                try:
                    from src.core.cache import get_redis

                    async with get_redis() as r:
                        await r.delete(
                            service_token_key(str(attempt_id)),
                            service_stop_key(str(attempt_id)),
                            service_ready_key(str(attempt_id)),
                        )
                except Exception:
                    # Best-effort credential-channel cleanup: leaked keys
                    # expire via TTL and carry no authority without the
                    # DB lease.
                    pass

    @staticmethod
    def _completion_for(envelope: dict, attempt) -> tuple[str, str | None, str | None]:
        """Map a child outcome envelope to a complete_attempt decision."""
        if envelope.get("recycled"):
            return (
                "requested",
                envelope.get("recycle_reason") or "recycled",
                None,
            )
        if attempt.stop_requested_at is not None:
            return (
                "requested",
                envelope.get("exit_reason") or "stop_requested",
                None,
            )
        if envelope.get("success"):
            return "clean_return", "clean_return", None
        error = envelope.get("error") or "service attempt failed"
        exit_reason = envelope.get("exit_reason") or envelope.get("error_type")
        return "failed", exit_reason, error

    # -- shutdown handover ---------------------------------------------

    async def _handover_owned(self) -> None:
        """Ask owned children to stop; completions reschedule elsewhere."""
        if not self._owned:
            return
        from src.core.cache import get_redis

        owned = list(self._owned.values())
        try:
            async with get_redis() as r:
                for item in owned:
                    try:
                        await r.setex(
                            service_stop_key(str(item.attempt_id)), 300, "1"
                        )
                    except Exception:
                        # Best-effort stop hint per child: an unnotified
                        # child keeps running until its lease expires and
                        # restarts elsewhere.
                        pass
        except Exception:
            # Redis unreachable during handover: children stop via lease
            # expiry and completions still reschedule through the DB.
            pass
        if self.pool is not None:
            await asyncio.gather(*(
                self.pool.stop_service_child(str(item.attempt_id))
                for item in owned
            ), return_exceptions=True)
        # Bounded wait for terminal results to land through
        # handle_service_result before the worker closes its DB.
        import time as _time

        deadline = _time.monotonic() + self.handover_wait_seconds
        while self._owned and _time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        self._owned.clear()
