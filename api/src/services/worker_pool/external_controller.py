"""External resource adapter for canonical physical-worker capacity."""

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import and_, select, update

from shared.models import WorkerCapacityProfile, WorkerCapacityReservation
from src.config import Settings
from src.core.database import get_db_context
from src.models.orm.execution_workers import ExecutionWorker
from src.services.kubernetes_execution import KubernetesPolicy
from src.services.worker_pool.controller import (
    RECONCILE_SECONDS, SWEEP_SECONDS, LiveControllers, WorkerCapacityController, _now, _withdraw_live,
)
from src.services.worker_pool.external_client import CapacityProtocolError, ExternalCapacityClient
from src.services.worker_pool.external_policy import validate_external_policy
from src.services.worker_pool.liveness import silent_executor
from src.services.worker_pool.recovery import announce_lost_runs, end_runs_of_lost_worker
from src.services.worker_pool.sizing import JobSizing
from src.worker.remote.protocol import WORKER_DRAIN_GRACE_SECONDS
from src.services.work_dispatch import (
    DISPATCH_WAKE_FAMILIES,
    WAKE_PLATFORM_JOB,
    WAKE_SERVICE,
    listen_for_pool_wake,
    wait_for_next_pass,
)

logger = logging.getLogger(__name__)

RECONCILE_STATE_CATEGORY = "worker_capacity"
RECONCILE_STATE_KEY = "external_reconcile_state"
#: At most one status write per interval; errors always win over the throttle
#: when the outcome changes, so a fresh failure is never hidden behind it.
RECONCILE_STATE_MIN_INTERVAL_SECONDS = 30.0


async def record_external_reconcile_state(
    *, ok: bool, error: str | None, scope: str | None,
) -> None:
    """Persist the last external reconcile pass outcome for the status card.

    A single global row: last-writer-wins across API replicas is fine for a
    display signal. Failures must never break the loop that reports them.
    """
    from src.models.orm.config import SystemConfig

    try:
        async with get_db_context() as db:
            row = await db.scalar(select(SystemConfig).where(
                SystemConfig.category == RECONCILE_STATE_CATEGORY,
                SystemConfig.key == RECONCILE_STATE_KEY,
                SystemConfig.organization_id.is_(None),
            ))
            payload = {
                "finished_at": _now().isoformat(),
                "ok": ok,
                "error": error,
                "scope": scope,
            }
            if row is None:
                db.add(SystemConfig(
                    category=RECONCILE_STATE_CATEGORY,
                    key=RECONCILE_STATE_KEY,
                    value_json=payload,
                    organization_id=None,
                    created_by="external-worker-pool",
                    updated_by="external-worker-pool",
                ))
            else:
                row.value_json = payload
                row.updated_by = "external-worker-pool"
            await db.commit()
    except Exception:
        logger.exception("External reconcile state could not be recorded")


def _reconcile_error_message(exc: BaseException) -> str:
    if isinstance(exc, CapacityProtocolError):
        # Usually the policy-vs-capabilities mismatch, whose detail names the
        # offending bound. It carries no secrets: both sides are operator config.
        return str(exc)[:200] or "Capacity provider rejected the configuration"
    if isinstance(exc, httpx.HTTPError):
        return "Capacity provider is unreachable"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


class ExternalWorkerPoolController(WorkerCapacityController):
    def __init__(self, client: ExternalCapacityClient, settings: Settings, profile: WorkerCapacityProfile) -> None:
        super().__init__(settings, worker_form="external")
        self.client = client
        self.profile = profile
        self.version = profile.worker_version
        self.live = LiveControllers(ids=frozenset({self.id}), versions=frozenset({self.version}))
        self.cleanup_states = ("lost", "retired")

    def _memory_environment(self, generation: int) -> dict[str, Any]:
        return {**super()._memory_environment(generation), "scope": self.profile.scope}

    def _managed_scope(self) -> Any:
        return and_(ExecutionWorker.form == "external", ExecutionWorker.capacity_scope == self.profile.scope)

    def _cleanup_scope(self) -> Any:
        # An outage must not turn an unconfirmed physical deletion into an orphan.
        return and_(
            ExecutionWorker.state.in_(self.cleanup_states),
            ExecutionWorker.capacity_resource_deleted_at.is_(None),
        )

    def _binding_fields(self, worker_id: UUID) -> dict[str, Any]:
        return {"capacity_scope": self.profile.scope, "version": self.profile.worker_version}

    def _sizing(self, policy: KubernetesPolicy, workers: Any) -> JobSizing:
        try:
            validate_external_policy(
                policy, self.profile, max_lifetime_seconds=self.settings.pool_max_lifetime_seconds,
            )
        except ValueError as exc:
            raise CapacityProtocolError(f"Worker policy differs from provider capabilities: {exc}") from exc
        return super()._sizing(policy, workers)

    async def reconcile(self) -> None:
        current = await self.client.profile()
        if current.scope != self.profile.scope:
            raise CapacityProtocolError("Capacity provider scope changed; drain the original provider first")
        self.profile = current
        self.version = current.worker_version
        await super().reconcile()

    async def reconcile_worker(self, worker_id: UUID, policy: KubernetesPolicy) -> None:
        self._sizing(policy, [])
        # Provider I/O never holds a DB connection or worker row lock. UUID
        # addressing and conditional commits make concurrent reconcilers safe.
        async with get_db_context() as db:
            worker = await db.scalar(select(ExecutionWorker).where(
                ExecutionWorker.id == worker_id, self._managed_scope(),
            ))
            if worker is None:
                return
            state, uid, limit = worker.state, worker.capacity_resource_uid, worker.memory_limit_bytes
            version = worker.version
            created_at = worker.created_at
            deleted_at = worker.capacity_resource_deleted_at
        if state == "reserved" and version != self.profile.worker_version:
            # A provider rollout cannot relabel an already reserved incarnation.
            # Fence it before cleanup; queued demand may reserve a fresh UUID.
            await self._record_loss(worker_id, uid, "provider_version_changed", False)
            return
        if state in self.cleanup_states and deleted_at is not None:
            return
        resource = await self.client.get(worker_id, self.profile.scope)
        if uid is not None and resource is not None and resource.resource_uid != uid:
            raise CapacityProtocolError("Provider resource UID changed for an existing incarnation")
        if state in self.cleanup_states:
            if resource is None and uid is None:
                # Another controller may still prepare this unbound incarnation.
                # Absence does not fence that delayed mutation; retain cleanup.
                return
            if resource is not None and resource.state != "deleted":
                await self.client.remove(worker_id, self.profile.scope, resource.resource_uid)
            # GET absence/a deleted tombstone or a validated DELETE response confirms
            # cleanup. Persist only for this still-terminal, unchanged incarnation.
            async with get_db_context() as db:
                await db.execute(update(ExecutionWorker).where(
                    ExecutionWorker.id == worker_id, self._managed_scope(),
                    ExecutionWorker.state.in_(self.cleanup_states),
                    ExecutionWorker.capacity_resource_uid == uid,
                    ExecutionWorker.capacity_resource_deleted_at.is_(None),
                ).values(capacity_resource_deleted_at=_now()))
                await db.commit()
            return
        if resource is not None and resource.worker_version != version:
            raise CapacityProtocolError("Provider resource build changed for an existing incarnation")
        if state == "reserved":
            created = resource is None
            if resource is None:
                if limit is None:
                    raise CapacityProtocolError("Reserved worker has no immutable memory limit")
                resource = await self.client.prepare(WorkerCapacityReservation(
                    worker_id=worker_id, worker_version=version,
                    memory_limit_bytes=limit,
                    max_lifetime_seconds=self.settings.pool_max_lifetime_seconds,
                    drain_grace_seconds=WORKER_DRAIN_GRACE_SECONDS,
                ), self.profile.scope)
            if resource.state != "prepared":
                async with get_db_context() as db:
                    fresh = await db.scalar(select(ExecutionWorker.state).where(ExecutionWorker.id == worker_id))
                if fresh != "reserved":
                    return
                reason = "provider_started_before_binding" if resource.state in ("starting", "running") else "provider_resource_stopped"
                await self._record_loss(worker_id, uid, reason, False)
                return
            async with get_db_context() as db:
                bound = await db.scalar(update(ExecutionWorker).where(
                    ExecutionWorker.id == worker_id, self._managed_scope(),
                    ExecutionWorker.state == "reserved", ExecutionWorker.capacity_resource_uid.is_(None),
                ).values(
                    capacity_resource_uid=resource.resource_uid, state="starting",
                    launched_by=self.id if created else None,
                ).returning(ExecutionWorker.id))
                await db.commit()
            if bound is not None:
                await self.client.start(worker_id, self.profile.scope, resource.resource_uid)
            return
        if state not in ("starting", "ready", "draining"):
            return
        reason = None
        if resource is None or resource.state in ("deleted", "stopped"):
            if (
                resource is None
                and state == "starting"
                and _now() - created_at < timedelta(seconds=self.settings.pool_provisioning_grace_seconds)
            ):
                # Provider reads are eventually consistent: a GET absence for
                # a just-started incarnation is not proof it never
                # provisioned. Explicit stopped/deleted reports still fence
                # at once, and start_timeout remains the outer bound.
                return
            reason = "provider_resource_stopped"
        elif state == "starting":
            if _now() - created_at >= timedelta(seconds=self.settings.pool_start_timeout_seconds):
                reason = "start_timeout"
            elif resource.state == "prepared":
                assert uid is not None
                await self.client.start(worker_id, self.profile.scope, uid)
                return
        elif resource.state == "prepared":
            reason = "provider_resource_regressed"
        elif self.reachability.steady():
            reason = "heartbeat_lost"
        if reason is not None:
            await self._record_loss(worker_id, uid, reason, resource is not None and resource.out_of_memory)

    async def _record_loss(self, worker_id: UUID, uid: str | None, reason: str, out_of_memory: bool) -> None:
        async with get_db_context() as db:
            worker = await db.scalar(select(ExecutionWorker).where(
                ExecutionWorker.id == worker_id, self._managed_scope(),
                ExecutionWorker.capacity_resource_uid == uid,
                ExecutionWorker.state.in_(("reserved", "starting", "ready", "draining")),
            ).with_for_update())
            if worker is None:
                return
            if reason == "heartbeat_lost" and not await db.scalar(select(ExecutionWorker.id).where(
                ExecutionWorker.id == worker_id, silent_executor(),
            )):
                return
            if reason == "start_timeout" and worker.state != "starting":
                return
            self._lose(worker, "out_of_memory" if out_of_memory else reason, _now())
            ended = await end_runs_of_lost_worker(db, worker.id, out_of_memory=out_of_memory)
            await db.commit()
        await announce_lost_runs(ended)


async def external_worker_pool_loop(stop_event: asyncio.Event, settings: Settings) -> None:
    """Run only when explicitly selected; Kubernetes is never a failure fallback."""
    assert settings.worker_capacity_url and settings.worker_capacity_token
    async with httpx.AsyncClient() as http:
        client = ExternalCapacityClient(
            settings.worker_capacity_url, settings.worker_capacity_token.get_secret_value(), http=http,
        )
        controller: ExternalWorkerPoolController | None = None
        last_reported: tuple[bool, str | None, str | None] | None = None
        last_reported_at = 0.0
        wake = asyncio.Event()
        listener = asyncio.create_task(
            listen_for_pool_wake(
                settings.redis_url,
                {
                    **{family: wake for family in DISPATCH_WAKE_FAMILIES},
                    WAKE_PLATFORM_JOB: wake,
                    WAKE_SERVICE: wake,
                },
            )
        )
        try:
            while not stop_event.is_set():
                started = time.monotonic()
                outcome: tuple[bool, str | None, str | None] | None = None
                try:
                    if controller is None:
                        controller = ExternalWorkerPoolController(client, settings, await client.profile())
                    if await controller.reachability.check():
                        await controller.reconcile()
                        outcome = (True, None, controller.profile.scope)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("External worker capacity reconciliation failed")
                    scope = controller.profile.scope if controller is not None else None
                    outcome = (False, _reconcile_error_message(exc), scope)
                if outcome is not None:
                    now = time.monotonic()
                    if (
                        outcome != last_reported
                        or now - last_reported_at >= RECONCILE_STATE_MIN_INTERVAL_SECONDS
                    ):
                        ok, error, scope = outcome
                        await record_external_reconcile_state(ok=ok, error=error, scope=scope)
                        last_reported, last_reported_at = outcome, now
                await wait_for_next_pass(
                    stop_event, wake, since=started, spacing=RECONCILE_SECONDS, sweep=SWEEP_SECONDS,
                )
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            if controller is not None:
                await _withdraw_live(controller.id)
