"""
Service lifecycle domain.

Durable control plane for supervised @service executables: definitions,
desired state, fenced leases, restart/backoff decisions, and crash-loop
protection. All state transitions are plain row mutations the caller commits;
this module never commits, so routers and the future reconciler own their
transactions.

Fencing: every attempt carries a unique lease token. Heartbeat, completion,
and stop operations present the token and raise StaleLeaseError when the
attempt is no longer the live owner (reassigned after lease expiry or
network partition).

Restart accounting uses a rolling window over terminal attempts: a sustained
healthy run sheds old penalties. Rapid terminal cycling (any outcome that
schedules a restart) counts toward crash-loop protection.
"""

from __future__ import annotations

import logging
import random
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.models.orm.services import _SERVICE_LIVE_STATES, ServiceAttempt, ServiceDefinition, ServiceLog
from src.models.orm.workflows import Workflow

logger = logging.getLogger(__name__)

STARTUP_POLICIES = ("automatic", "manual")
RESTART_POLICIES = ("always", "on_failure", "never")
DESIRED_STATES = ("running", "stopped")
BLOCKED_REASONS = ("policy", "crash_loop", "disabled")
TERMINAL_ATTEMPT_STATES = ("stopped", "failed")


class ServiceNotFoundError(Exception):
    """Service definition does not exist."""


class ServiceConflictError(Exception):
    """Transition conflicts with current state (maps to HTTP 409)."""


class ServiceValidationError(ValueError):
    """Invalid policy value or transition argument (maps to HTTP 400)."""


class StaleLeaseError(Exception):
    """Attempt is not the live owner (superseded or terminal)."""


class ServiceClaimConflict(Exception):
    """Lost a claim race after serialization (retry on next tick)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_lease_token() -> str:
    return secrets.token_hex(32)


# ==================== Definitions ====================


async def get_definition(db: AsyncSession, service_id: UUID) -> ServiceDefinition | None:
    """Load a service definition by ID."""
    return await db.get(ServiceDefinition, service_id)


async def get_definition_for_workflow(
    db: AsyncSession, workflow_id: UUID
) -> ServiceDefinition | None:
    """Load the definition linked to a workflow row, if any."""
    result = await db.execute(
        select(ServiceDefinition).where(ServiceDefinition.workflow_id == workflow_id)
    )
    return result.scalar_one_or_none()


async def ensure_definition_for_workflow(
    db: AsyncSession,
    workflow: Workflow,
    *,
    created_by: str,
) -> ServiceDefinition:
    """Get-or-create the definition for a service workflow row.

    Existing operator policy is never reset: only missing definitions are
    created (enabled, automatic startup, running desire, always restart).
    """
    existing = await get_definition_for_workflow(db, workflow.id)
    if existing is not None:
        return existing
    definition = ServiceDefinition(
        id=uuid4(),
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        solution_id=workflow.solution_id,
        enabled=True,
        startup_policy="automatic",
        restart_policy="always",
        desired_state="running",
        created_by=created_by,
    )
    db.add(definition)
    await db.flush()
    logger.info(
        "Created service definition for workflow %s (%s)",
        workflow.name,
        definition.id,
    )
    return definition


async def sync_definition_for_registration(
    db: AsyncSession,
    workflow: Workflow,
    *,
    created_by: str,
) -> ServiceDefinition | None:
    """Reconcile the definition with a (re-)registered workflow row.

    Service rows get an ensured definition. Rows converting away from service
    keep their history but are parked (disabled + stopped) so a stale desired
    state can never launch removed code.
    """
    if workflow.type == "service":
        return await ensure_definition_for_workflow(db, workflow, created_by=created_by)
    definition = await get_definition_for_workflow(db, workflow.id)
    if definition is not None:
        await park_definition(
            db, definition, reason=f"source converted to type={workflow.type}"
        )
    return definition


async def park_definition(
    db: AsyncSession, definition: ServiceDefinition, *, reason: str = "source removed or converted"
) -> ServiceDefinition:
    """Park a definition whose source is gone or converted: disabled and
    stopped with history retained. The live attempt (if any) is asked to stop
    under lock so a concurrent completion cannot resurrect it."""
    definition.enabled = False
    definition.desired_state = "stopped"
    await _request_attempt_stop(db, definition.id)
    definition.updated_at = _now()
    await db.flush()
    logger.info("Parked service definition %s (%s)", definition.id, reason)
    return definition


async def park_definitions_for_workflows(
    db: AsyncSession,
    workflow_ids: list[UUID],
    *,
    reason: str,
) -> int:
    """Park definitions linked to the given workflow rows.

    Called by every removal path (deactivation, orphan cleanup, file delete,
    type conversion) so a live attempt is asked to stop promptly and desired
    state can never relaunch removed code. The claim join on
    (type='service', is_active) remains the ultimate launch guard.
    Returns the number of definitions parked.
    """
    if not workflow_ids:
        return 0
    result = await db.execute(
        select(ServiceDefinition).where(
            ServiceDefinition.workflow_id.in_(workflow_ids)
        )
    )
    parked = 0
    for definition in result.scalars().all():
        await park_definition(db, definition, reason=reason)
        parked += 1
    return parked


async def note_source_revision(
    db: AsyncSession, definition: ServiceDefinition, revision: str | None
) -> ServiceDefinition:
    """Record the content revision observed by the indexer.

    Claims pin this value onto attempts; Slice 3 compares it at launch and
    restarts stale attempts. Only writes on change to keep file-save cheap.
    """
    if definition.current_revision != revision:
        definition.current_revision = revision
        definition.updated_at = _now()
        await db.flush()
    return definition


async def list_definitions(
    db: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ServiceDefinition], int]:
    """List definitions newest-first with a total count."""
    total = await db.scalar(select(func.count(ServiceDefinition.id))) or 0
    result = await db.execute(
        select(ServiceDefinition)
        .options(joinedload(ServiceDefinition.workflow))
        .order_by(ServiceDefinition.created_at.desc(), ServiceDefinition.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.unique().scalars().all()), total


async def update_policy(
    db: AsyncSession,
    definition: ServiceDefinition,
    **fields: object,
) -> ServiceDefinition:
    """Apply validated policy fields. Unknown fields are rejected."""
    allowed = {
        "startup_policy": STARTUP_POLICIES,
        "restart_policy": RESTART_POLICIES,
    }
    ranged = {
        "graceful_shutdown_seconds": (0, 3600),
        "startup_grace_seconds": (1, 3600),
        "restart_backoff_initial_seconds": (0, 300),
        "restart_backoff_max_seconds": (0, 3600),
        "crash_loop_max_restarts": (1, 100),
        "crash_loop_window_seconds": (60, 86400),
    }
    for key, value in fields.items():
        if value is None:
            continue
        if key in allowed:
            if value not in allowed[key]:
                raise ServiceValidationError(
                    f"Invalid {key}: {value!r}. Must be one of {list(allowed[key])}."
                )
            setattr(definition, key, value)
        elif key in ranged:
            low, high = ranged[key]
            if not isinstance(value, int) or not (low <= value <= high):
                raise ServiceValidationError(
                    f"Invalid {key}: {value!r}. Must be an integer in [{low}, {high}]."
                )
            setattr(definition, key, value)
        else:
            raise ServiceValidationError(f"Unknown policy field: {key!r}.")
    definition.updated_at = _now()
    await db.flush()
    return definition


# ==================== Desired state ====================


async def set_enabled(
    db: AsyncSession, definition: ServiceDefinition, enabled: bool
) -> ServiceDefinition:
    """Flip the operator switch. Disabling also requests a stop of the live attempt."""
    definition.enabled = enabled
    if not enabled:
        await _request_attempt_stop(db, definition.id)
    else:
        if definition.blocked_reason == "disabled":
            definition.blocked_reason = None
            definition.restart_eligible_at = None
    definition.updated_at = _now()
    await db.flush()
    return definition


async def start_service(db: AsyncSession, definition: ServiceDefinition) -> ServiceDefinition:
    """Request running. Clears launch suppression; the claim loop picks it up."""
    if not definition.enabled:
        raise ServiceConflictError(
            "Service is disabled. Enable it before starting."
        )
    definition.desired_state = "running"
    definition.blocked_reason = None
    definition.restart_eligible_at = None
    definition.updated_at = _now()
    await db.flush()
    return definition


async def stop_service(db: AsyncSession, definition: ServiceDefinition) -> ServiceDefinition:
    """Request stopped. Durable desire is stored first so the outcome is never
    mistaken for an unexpected exit."""
    definition.desired_state = "stopped"
    await _request_attempt_stop(db, definition.id)
    definition.updated_at = _now()
    await db.flush()
    return definition


async def restart_service(db: AsyncSession, definition: ServiceDefinition) -> ServiceDefinition:
    """Rolling restart: stay desired-running, clear suppression, and ask the
    live attempt (if any) to stop. Its completion reschedules promptly."""
    if not definition.enabled:
        raise ServiceConflictError(
            "Service is disabled. Enable it before restarting."
        )
    definition.desired_state = "running"
    definition.blocked_reason = None
    definition.restart_eligible_at = None
    await _request_attempt_stop(db, definition.id)
    definition.updated_at = _now()
    await db.flush()
    return definition


# ==================== Attempts ====================


async def get_live_attempt(
    db: AsyncSession, service_id: UUID
) -> ServiceAttempt | None:
    """The single live attempt for a service, if any (unlocked read)."""
    result = await db.execute(
        select(ServiceAttempt)
        .where(
            ServiceAttempt.service_id == service_id,
            ServiceAttempt.state.in_(_SERVICE_LIVE_STATES),
        )
        .order_by(ServiceAttempt.created_at.desc())
    )
    return result.scalars().first()


async def get_live_attempt_for_update(
    db: AsyncSession, service_id: UUID
) -> ServiceAttempt | None:
    """Locked variant for control-plane mutations (stop/disable/restart/park).

    Locking prevents mutating an attempt that completes concurrently: the
    loser of the race sees the terminal state and skips the mutation.
    """
    result = await db.execute(
        select(ServiceAttempt)
        .where(
            ServiceAttempt.service_id == service_id,
            ServiceAttempt.state.in_(_SERVICE_LIVE_STATES),
        )
        .order_by(ServiceAttempt.created_at.desc())
        .with_for_update()
    )
    return result.scalars().first()


async def _request_attempt_stop(
    db: AsyncSession, service_id: UUID
) -> ServiceAttempt | None:
    """Flag the live attempt for graceful stop, if it is still live."""
    live = await get_live_attempt_for_update(db, service_id)
    if live is not None and live.stop_requested_at is None:
        live.stop_requested_at = _now()
    return live


async def list_attempts(
    db: AsyncSession,
    service_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ServiceAttempt], int]:
    """Attempts newest-first with a total count."""
    total = (
        await db.scalar(
            select(func.count(ServiceAttempt.id)).where(
                ServiceAttempt.service_id == service_id
            )
        )
        or 0
    )
    result = await db.execute(
        select(ServiceAttempt)
        .where(ServiceAttempt.service_id == service_id)
        .order_by(ServiceAttempt.created_at.desc(), ServiceAttempt.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), total


async def get_last_terminal_attempt(
    db: AsyncSession, service_id: UUID
) -> ServiceAttempt | None:
    """Newest terminal attempt for last-exit display, if any."""
    result = await db.execute(
        select(ServiceAttempt)
        .where(
            ServiceAttempt.service_id == service_id,
            ServiceAttempt.state.in_(TERMINAL_ATTEMPT_STATES),
        )
        .order_by(ServiceAttempt.created_at.desc(), ServiceAttempt.id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def batch_list_embedding(
    db: AsyncSession, service_ids: list[UUID]
) -> tuple[
    dict[UUID, ServiceAttempt], dict[UUID, ServiceAttempt], dict[UUID, int]
]:
    """Batch the list endpoint's per-row observed-state lookups.

    Three queries total regardless of page size (replacing the 3N
    fan-out): live attempts, newest terminal attempt per service, and
    attempt counts. Returns ``(live_by_service, last_terminal_by_service,
    count_by_service)``; services with no matching rows are absent from
    each map. Ordering matches the single-row helpers (newest first).
    """
    live_by_service: dict[UUID, ServiceAttempt] = {}
    last_terminal_by_service: dict[UUID, ServiceAttempt] = {}
    count_by_service: dict[UUID, int] = {}
    if not service_ids:
        return live_by_service, last_terminal_by_service, count_by_service

    live_result = await db.execute(
        select(ServiceAttempt)
        .where(
            ServiceAttempt.service_id.in_(service_ids),
            ServiceAttempt.state.in_(_SERVICE_LIVE_STATES),
        )
        .order_by(ServiceAttempt.created_at.desc())
    )
    for attempt in live_result.scalars().all():
        # At most one live attempt per service (partial unique index);
        # newest-first order keeps the same row get_live_attempt returns.
        live_by_service.setdefault(attempt.service_id, attempt)

    ranked = (
        select(
            ServiceAttempt.id.label("attempt_id"),
            func.row_number()
            .over(
                partition_by=ServiceAttempt.service_id,
                order_by=(
                    ServiceAttempt.created_at.desc(),
                    ServiceAttempt.id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(
            ServiceAttempt.service_id.in_(service_ids),
            ServiceAttempt.state.in_(TERMINAL_ATTEMPT_STATES),
        )
        .subquery()
    )
    terminal_result = await db.execute(
        select(ServiceAttempt)
        .join(ranked, ServiceAttempt.id == ranked.c.attempt_id)
        .where(ranked.c.rn == 1)
    )
    for attempt in terminal_result.scalars().all():
        last_terminal_by_service[attempt.service_id] = attempt

    count_result = await db.execute(
        select(
            ServiceAttempt.service_id,
            func.count(ServiceAttempt.id),
        )
        .where(ServiceAttempt.service_id.in_(service_ids))
        .group_by(ServiceAttempt.service_id)
    )
    for service_id, count in count_result.all():
        count_by_service[service_id] = int(count or 0)
    return live_by_service, last_terminal_by_service, count_by_service


async def list_service_logs(
    db: AsyncSession,
    service_id: UUID,
    *,
    attempt_id: UUID | None = None,
    levels: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 200,
    cursor: tuple[datetime, int] | None = None,
    newest_first: bool = False,
) -> tuple[list[ServiceLog], int, str | None]:
    """Trailing service logs with keyset paging (established pattern).

    Same contract as ExecutionLogRepository.list_logs: optional attempt
    scope, level allowlist (uppercased), timestamp window, and a stable
    ``(timestamp, id)`` keyset cursor that never replays or skips rows
    when new lines arrive mid-pagination. Total counts the same filters
    (for UI "showing N of M" copy). Returns (rows, total,
    next_continuation_token).
    """
    from src.repositories.execution_logs import encode_execution_log_cursor

    filters = [ServiceLog.service_id == service_id]
    if attempt_id is not None:
        filters.append(ServiceLog.attempt_id == attempt_id)
    if levels:
        filters.append(
            ServiceLog.level.in_([lvl.upper() for lvl in levels])
        )
    if start is not None:
        filters.append(ServiceLog.timestamp >= start)
    if end is not None:
        filters.append(ServiceLog.timestamp <= end)

    # Total counts the user's filters (for "showing N of M"), never the
    # keyset cursor.
    total = await db.scalar(select(func.count(ServiceLog.id)).where(*filters)) or 0
    if cursor is not None:
        cursor_timestamp, cursor_id = cursor
        if newest_first:
            filters.append(
                or_(
                    ServiceLog.timestamp < cursor_timestamp,
                    and_(
                        ServiceLog.timestamp == cursor_timestamp,
                        ServiceLog.id < cursor_id,
                    ),
                )
            )
        else:
            filters.append(
                or_(
                    ServiceLog.timestamp > cursor_timestamp,
                    and_(
                        ServiceLog.timestamp == cursor_timestamp,
                        ServiceLog.id > cursor_id,
                    ),
                )
            )

    ordering = (
        (ServiceLog.timestamp.desc(), ServiceLog.id.desc())
        if newest_first
        else (ServiceLog.timestamp.asc(), ServiceLog.id.asc())
    )
    result = await db.execute(
        select(ServiceLog)
        .where(*filters)
        .order_by(*ordering)
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    next_token = (
        encode_execution_log_cursor(rows[-1].timestamp, rows[-1].id)
        if has_more and rows
        else None
    )
    return rows, total, next_token


async def count_restarts_in_window(
    db: AsyncSession,
    service_id: UUID,
    *,
    window_seconds: int,
    now: datetime | None = None,
) -> int:
    """Terminal attempts whose run ended inside the rolling window.

    Counted by terminal time (stopped_at), not creation: a long-running
    service that fails now counts now.
    """
    now = now or _now()
    cutoff = now - timedelta(seconds=window_seconds)
    return (
        await db.scalar(
            select(func.count(ServiceAttempt.id)).where(
                ServiceAttempt.service_id == service_id,
                ServiceAttempt.state.in_(TERMINAL_ATTEMPT_STATES),
                ServiceAttempt.stopped_at.is_not(None),
                ServiceAttempt.stopped_at >= cutoff,
            )
        )
        or 0
    )


def _backoff_delay_seconds(
    *,
    initial: int,
    maximum: int,
    restarts_in_window: int,
) -> float:
    """Exponential backoff with full jitter, bounded by the policy cap."""
    delay = min(initial * (2 ** max(restarts_in_window - 1, 0)), maximum)
    return delay / 2 + random.uniform(0, delay / 2) if delay > 0 else 0.0


async def claim_eligible_service(
    db: AsyncSession,
    *,
    worker_id: str,
    lease_ttl_seconds: int = 60,
    now: datetime | None = None,
) -> ServiceAttempt | None:
    """Atomically claim one eligible service for this worker.

    Eligibility: enabled, desired running, no suppression, backoff elapsed,
    and no live attempt. The definition row is locked SKIP LOCKED so workers
    never block each other; the partial unique index backstops races.
    Returns None when nothing is eligible. The caller commits.
    """
    now = now or _now()
    live_exists = (
        select(ServiceAttempt.id)
        .where(
            ServiceAttempt.service_id == ServiceDefinition.id,
            ServiceAttempt.state.in_(_SERVICE_LIVE_STATES),
        )
        .exists()
    )
    result = await db.execute(
        select(ServiceDefinition)
        # Launch safety: the source row must still exist, still be a service,
        # and still be active. Indexer type-changes, deactivation, and removal
        # all converge here even if their own parking calls are missed.
        # Services are always org-scoped (service tokens require org_id):
        # org-less definitions are never eligible.
        .join(Workflow, Workflow.id == ServiceDefinition.workflow_id)
        .where(
            Workflow.type == "service",
            Workflow.is_active.is_(True),
            ServiceDefinition.enabled.is_(True),
            ServiceDefinition.organization_id.is_not(None),
            ServiceDefinition.desired_state == "running",
            ServiceDefinition.blocked_reason.is_(None),
            (
                ServiceDefinition.restart_eligible_at.is_(None)
                | (ServiceDefinition.restart_eligible_at <= now)
            ),
            ~live_exists,
        )
        .order_by(
            ServiceDefinition.restart_eligible_at.asc().nulls_first(),
            ServiceDefinition.updated_at.asc(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    definition = result.scalar_one_or_none()
    if definition is None:
        return None

    last_number = await db.scalar(
        select(func.max(ServiceAttempt.restart_number)).where(
            ServiceAttempt.service_id == definition.id
        )
    )
    attempt = ServiceAttempt(
        id=uuid4(),
        service_id=definition.id,
        revision=definition.current_revision,
        worker_id=worker_id,
        lease_token=_new_lease_token(),
        lease_expires_at=now + timedelta(seconds=lease_ttl_seconds),
        state="starting",
        started_at=now,
        heartbeat_at=now,
        restart_number=(last_number + 1) if last_number is not None else 0,
    )
    db.add(attempt)
    try:
        await db.flush()
    except IntegrityError as e:
        raise ServiceClaimConflict(
            f"Lost claim race for service {definition.id}"
        ) from e
    logger.info(
        "Worker %s claimed service %s (attempt %s)",
        worker_id,
        definition.id,
        attempt.id,
    )
    return attempt


async def _locked_live_attempt(
    db: AsyncSession, attempt_id: UUID, lease_token: str, now: datetime
) -> ServiceAttempt:
    """Load a live attempt under lock, enforcing lease ownership and time.

    An owner whose lease expired (but has not been swept yet) is stale: it
    must not renew, report, or complete. Expiry is enforced here as well as
    in expire_leases so a stale owner can never resurrect its lease.
    """
    result = await db.execute(
        select(ServiceAttempt)
        .where(ServiceAttempt.id == attempt_id)
        .with_for_update()
    )
    attempt = result.scalar_one_or_none()
    if (
        attempt is None
        or attempt.state not in _SERVICE_LIVE_STATES
        or attempt.lease_token != lease_token
        or attempt.lease_expires_at <= now
    ):
        raise StaleLeaseError(f"Attempt {attempt_id} is not the live owner.")
    return attempt


async def heartbeat_attempt(
    db: AsyncSession,
    *,
    attempt_id: UUID,
    lease_token: str,
    lease_ttl_seconds: int = 60,
    now: datetime | None = None,
) -> ServiceAttempt:
    """Renew a live attempt lease. Rejects superseded or terminal attempts."""
    now = now or _now()
    attempt = await _locked_live_attempt(db, attempt_id, lease_token, now)
    attempt.heartbeat_at = now
    attempt.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
    await db.flush()
    return attempt


async def mark_attempt_ready(
    db: AsyncSession,
    *,
    attempt_id: UUID,
    lease_token: str,
    now: datetime | None = None,
) -> ServiceAttempt:
    """Record service.ready() for a live attempt (starting → running)."""
    now = now or _now()
    attempt = await _locked_live_attempt(db, attempt_id, lease_token, now)
    if attempt.state == "starting":
        attempt.state = "running"
    attempt.ready_at = attempt.ready_at or now
    attempt.heartbeat_at = now
    await db.flush()
    return attempt


async def complete_attempt(
    db: AsyncSession,
    *,
    attempt_id: UUID,
    lease_token: str,
    reason: str,
    exit_code: int | None = None,
    exit_reason: str | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> ServiceDefinition:
    """Record a terminal attempt and apply the restart decision.

    reason: 'requested' (stop/restart was asked), 'clean_return' (function
    returned), or 'failed' (exception, crash, or lost worker).
    """
    if reason not in ("requested", "clean_return", "failed"):
        raise ServiceValidationError(
            f"Invalid completion reason: {reason!r}. "
            "Must be 'requested', 'clean_return', or 'failed'."
        )
    now = now or _now()
    attempt = await _locked_live_attempt(db, attempt_id, lease_token, now)
    attempt.state = "stopped" if reason in ("requested", "clean_return") else "failed"
    attempt.stopped_at = now
    attempt.exit_code = exit_code
    attempt.exit_reason = exit_reason
    attempt.error = error

    result = await db.execute(
        select(ServiceDefinition)
        .where(ServiceDefinition.id == attempt.service_id)
        .with_for_update()
    )
    definition = result.scalar_one()
    await _apply_restart_decision(db, definition, attempt, reason, now)
    definition.updated_at = now
    await db.flush()
    return definition


async def _apply_restart_decision(
    db: AsyncSession,
    definition: ServiceDefinition,
    attempt: ServiceAttempt,
    reason: str,
    now: datetime,
) -> None:
    """Decide the next launch after a terminal attempt (definition locked)."""
    if definition.desired_state != "running":
        # User-requested stop (or parked definition): desired gate suppresses.
        return
    if reason == "requested":
        # Rolling restart: come back promptly without failure accounting.
        definition.restart_eligible_at = now + timedelta(
            seconds=_backoff_delay_seconds(
                initial=definition.restart_backoff_initial_seconds,
                maximum=definition.restart_backoff_max_seconds,
                restarts_in_window=1,
            )
        )
        return
    if reason == "clean_return" and definition.restart_policy != "always":
        definition.blocked_reason = "policy"
        definition.restart_eligible_at = None
        return
    if reason == "failed" and definition.restart_policy == "never":
        definition.blocked_reason = "policy"
        definition.restart_eligible_at = None
        return
    # A restart is due: count terminal attempts in the rolling window. The
    # attempt that just ended is already terminal, so the count includes it.
    restarts = await count_restarts_in_window(
        db,
        definition.id,
        window_seconds=definition.crash_loop_window_seconds,
        now=now,
    )
    if restarts >= definition.crash_loop_max_restarts:
        definition.blocked_reason = "crash_loop"
        definition.restart_eligible_at = None
        logger.warning(
            "Service %s entered crash-loop (%s restarts in %ss)",
            definition.id,
            restarts,
            definition.crash_loop_window_seconds,
        )
        return
    definition.restart_eligible_at = now + timedelta(
        seconds=_backoff_delay_seconds(
            initial=definition.restart_backoff_initial_seconds,
            maximum=definition.restart_backoff_max_seconds,
            restarts_in_window=restarts,
        )
    )


async def expire_leases(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Fail live attempts whose lease expired and apply restart decisions.

    The Slice 3 claim loop calls this before claiming; tests and operators
    can invoke it directly. Returns the number of expired attempts.
    """
    now = now or _now()
    result = await db.execute(
        select(ServiceAttempt)
        .where(
            ServiceAttempt.state.in_(_SERVICE_LIVE_STATES),
            ServiceAttempt.lease_expires_at <= now,
        )
        .order_by(ServiceAttempt.lease_expires_at.asc())
        .with_for_update(skip_locked=True)
    )
    expired = list(result.scalars().all())
    for attempt in expired:
        attempt.state = "failed"
        attempt.stopped_at = now
        attempt.exit_reason = "lease_expired"
        attempt.error = "Owning worker stopped renewing the lease."
        locked = await db.execute(
            select(ServiceDefinition)
            .where(ServiceDefinition.id == attempt.service_id)
            .with_for_update()
        )
        definition = locked.scalar_one()
        await _apply_restart_decision(db, definition, attempt, "failed", now)
        definition.updated_at = now
    await db.flush()
    if expired:
        logger.warning("Expired %d service lease(s)", len(expired))
    return len(expired)


def describe_observed_state(
    definition: ServiceDefinition,
    live_attempt: ServiceAttempt | None,
) -> str:
    """Derive UI-facing state from desired + definition + live attempt."""
    if live_attempt is None:
        if definition.desired_state != "running":
            return "stopped"
        if definition.blocked_reason == "crash_loop":
            return "crash_loop"
        if definition.blocked_reason is not None:
            return "stopped"
        return "restarting"
    if live_attempt.stop_requested_at is not None:
        return "stopping"
    if live_attempt.state == "running":
        return "running"
    return "starting"
