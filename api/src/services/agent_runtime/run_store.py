"""Fenced AgentRun state store: lifecycle, leases, checkpoints, journal.

Every operation is a narrow transaction against PostgreSQL, the authority
for whether a run can continue. Writers prove ownership with the current
``lease_token``; stale workers cannot checkpoint, journal, or terminalize.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.secret_string import redact_secrets
from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJournalEntry,
    AgentRunStep,
)
from src.services.agent_runtime import types as rt

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_lease_token() -> str:
    return uuid4().hex


async def _locked_run(session: AsyncSession, run_id: UUID) -> AgentRun:
    run = (
        await session.execute(
            select(AgentRun)
            .where(AgentRun.id == run_id)
            .with_for_update(of=AgentRun)
        )
    ).scalar_one_or_none()
    if run is None:
        raise rt.RunNotFoundError(f"AgentRun {run_id} not found")
    return run


def _require_lease(run: AgentRun, lease_token: str | None) -> None:
    """Fencing: a writer must present the run's current lease token."""
    if not run.lease_token or run.lease_token != lease_token:
        raise rt.LeaseMismatchError(
            f"AgentRun {run.id}: stale or missing lease token"
        )


def _check_transition(run: AgentRun, target: str) -> None:
    allowed = rt.TRANSITIONS.get(run.status, frozenset())
    if target not in allowed:
        raise rt.InvalidTransitionError(
            f"AgentRun {run.id}: {run.status} -> {target} is not allowed"
        )


async def _next_journal_sequence(session: AsyncSession, run_id: UUID) -> int:
    current = (
        await session.execute(
            select(func.max(AgentRunJournalEntry.sequence)).where(
                AgentRunJournalEntry.run_id == run_id
            )
        )
    ).scalar()
    return int(current or 0) + 1


async def claim_run(
    session: AsyncSession,
    run_id: UUID,
    owner: str,
    *,
    lease_ttl_seconds: int = rt.DEFAULT_LEASE_TTL_SECONDS,
) -> AgentRun:
    """Atomically claim a queued run or an expired resumable run.

    Claims ``queued`` rows without a lease, ``running`` rows whose lease
    expired (including woken runs with no lease), and increments ``attempt``.
    Exactly one worker wins; concurrent claimants get ``RunNotClaimableError``.
    """
    now = _now()
    run = await _locked_run(session, run_id)
    claimable = False
    if run.status == rt.QUEUED and not run.lease_token:
        claimable = True
    elif run.status == rt.RUNNING and (
        not run.lease_token
        or (run.lease_expires_at is not None and run.lease_expires_at <= now)
    ):
        claimable = True
    if not claimable:
        raise rt.RunNotClaimableError(
            f"AgentRun {run_id} is {run.status} with a live lease; not claimable"
        )
    reclaimed = run.status == rt.RUNNING and run.attempt > 0
    run.status = rt.RUNNING
    run.lease_owner = owner
    run.lease_token = _new_lease_token()
    run.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
    run.last_progress_at = now
    run.attempt = (run.attempt or 0) + 1
    if run.started_at is None:
        run.started_at = now
    await session.flush()
    await append_journal(
        session,
        run_id,
        run.lease_token,
        rt.JOURNAL_LEASE_RECOVERY if reclaimed else rt.JOURNAL_RESUME,
        {"owner": owner, "attempt": run.attempt, "reclaimed": reclaimed},
    )
    await session.commit()
    logger.info(
        "Claimed agent run %s (attempt %s, owner %s)", run_id, run.attempt, owner
    )
    return run


async def renew_lease(
    session: AsyncSession,
    run_id: UUID,
    lease_token: str,
    *,
    lease_ttl_seconds: int = rt.DEFAULT_LEASE_TTL_SECONDS,
) -> AgentRun:
    """Renew a live lease. Stale tokens are rejected without side effects."""
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    now = _now()
    run.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
    run.last_progress_at = now
    await session.commit()
    return run


async def append_journal(
    session: AsyncSession,
    run_id: UUID,
    lease_token: str | None,
    kind: str,
    data: dict[str, Any] | None = None,
    *,
    provider_invocation_id: str | None = None,
    checkpoint_sequence: int | None = None,
    secrets: set[str] | None = None,
    commit: bool = True,
) -> AgentRunJournalEntry:
    """Append one journal entry. Lease-fenced; secrets redacted pre-write."""
    if kind not in rt.JOURNAL_EVENT_KINDS:
        raise rt.RunStoreError(f"Unknown journal event kind: {kind!r}")
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    entry = AgentRunJournalEntry(
        run_id=run_id,
        sequence=await _next_journal_sequence(session, run_id),
        kind=kind,
        data=redact_secrets(dict(data or {}), secrets or set()),
        provider_invocation_id=provider_invocation_id,
        checkpoint_sequence=(
            checkpoint_sequence
            if checkpoint_sequence is not None
            else run.checkpoint_sequence
        ),
    )
    session.add(entry)
    run.last_progress_at = _now()
    await session.flush()
    if commit:
        await session.commit()
    return entry


async def commit_checkpoint(
    session: AsyncSession,
    run_id: UUID,
    lease_token: str,
    state: dict[str, Any],
    *,
    format_version: int = 1,
    journal_kind: str = rt.JOURNAL_CHECKPOINT,
    journal_data: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    iterations_used: int | None = None,
    tokens_used: int | None = None,
    secrets: set[str] | None = None,
) -> AgentRunCheckpoint:
    """Atomically append checkpoint + journal boundary, advance the run.

    Projects compatible ``AgentRunStep`` rows and updates counters and
    last-progress in the same transaction.
    """
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    now = _now()
    next_sequence = (run.checkpoint_sequence or 0) + 1
    checkpoint = AgentRunCheckpoint(
        run_id=run_id,
        sequence=next_sequence,
        format_version=format_version,
        state=redact_secrets(dict(state), secrets or set()),
        attempt=run.attempt or 0,
        lease_token=lease_token,
    )
    session.add(checkpoint)
    run.checkpoint_sequence = next_sequence
    if iterations_used is not None:
        run.iterations_used = iterations_used
    if tokens_used is not None:
        run.tokens_used = tokens_used
    run.last_progress_at = now
    # Journal boundary stays in the same transaction (no intermediate commit).
    await append_journal(
        session,
        run_id,
        lease_token,
        journal_kind,
        journal_data,
        checkpoint_sequence=next_sequence,
        secrets=secrets,
        commit=False,
    )
    for step in steps or []:
        session.add(
            AgentRunStep(
                run_id=run_id,
                step_number=step["step_number"],
                type=step["type"],
                content=redact_secrets(step.get("content"), secrets or set()),
                tokens_used=step.get("tokens_used"),
                duration_ms=step.get("duration_ms"),
            )
        )
    await session.commit()
    return checkpoint


async def transition_waiting(
    session: AsyncSession,
    run_id: UUID,
    lease_token: str,
    target: str,
    *,
    wake_at: datetime | None = None,
    journal_data: dict[str, Any] | None = None,
    secrets: set[str] | None = None,
) -> AgentRun:
    """Move a running run to a durable inactive state; releases the lease."""
    if target not in rt.INACTIVE_WAIT_STATUSES:
        raise rt.RunStoreError(f"Not a waiting state: {target!r}")
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    _check_transition(run, target)
    run.status = target
    run.wake_at = wake_at if target == rt.SLEEPING else None
    run.lease_owner = None
    run.lease_token = None
    run.lease_expires_at = None
    run.last_progress_at = _now()
    # Direct insert: this transaction holds the row lock and just released
    # the lease it owned, so the normal lease fence (which re-locks the row)
    # cannot be used for this boundary entry.
    entry = AgentRunJournalEntry(
        run_id=run_id,
        sequence=await _next_journal_sequence(session, run_id),
        kind=rt.JOURNAL_WAIT,
        data=redact_secrets(
            {"target": target, **(journal_data or {})}, secrets or set()
        ),
        checkpoint_sequence=run.checkpoint_sequence,
    )
    session.add(entry)
    await session.commit()
    return run


async def wake_run(
    session: AsyncSession,
    run_id: UUID,
    *,
    reason: str,
) -> AgentRun:
    """Wake a waiting/sleeping run back to claimable ``running`` (no lease).

    Only the child/timer completion path calls this; it is the matching
    half of ``transition_waiting``.
    """
    run = await _locked_run(session, run_id)
    _check_transition(run, rt.RUNNING)
    run.status = rt.RUNNING
    run.wake_at = None
    run.lease_owner = None
    run.lease_token = None
    run.lease_expires_at = None
    run.last_progress_at = _now()
    entry = AgentRunJournalEntry(
        run_id=run_id,
        sequence=await _next_journal_sequence(session, run_id),
        kind=rt.JOURNAL_RESUME,
        data={"reason": reason},
        checkpoint_sequence=run.checkpoint_sequence,
    )
    session.add(entry)
    await session.commit()
    return run


async def finish_run(
    session: AsyncSession,
    run_id: UUID,
    lease_token: str,
    status: str,
    *,
    output: dict | None = None,
    error: str | None = None,
    iterations_used: int | None = None,
    tokens_used: int | None = None,
    duration_ms: int | None = None,
    llm_model: str | None = None,
    contract_valid: bool | None = None,
    contract_errors: list[str] | None = None,
) -> AgentRun:
    """Terminalize a run; marks completion-event pending in the same txn."""
    if status not in rt.TERMINAL_STATUSES:
        raise rt.RunStoreError(f"Not a terminal status: {status!r}")
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    _check_transition(run, status)
    now = _now()
    run.status = status
    if output is not None:
        run.output = output
    if error is not None:
        run.error = error
    if iterations_used is not None:
        run.iterations_used = iterations_used
    if tokens_used is not None:
        run.tokens_used = tokens_used
    if duration_ms is not None:
        run.duration_ms = duration_ms
    if llm_model is not None:
        run.llm_model = llm_model
    if contract_valid is not None:
        run.contract_valid = contract_valid
    if contract_errors is not None:
        run.contract_errors = contract_errors
    run.completed_at = now
    run.lease_owner = None
    run.lease_token = None
    run.lease_expires_at = None
    run.last_progress_at = now
    run.completion_event_pending_at = now
    entry = AgentRunJournalEntry(
        run_id=run_id,
        sequence=await _next_journal_sequence(session, run_id),
        kind=rt.JOURNAL_COMPLETION,
        data={"status": status},
        checkpoint_sequence=run.checkpoint_sequence,
    )
    session.add(entry)
    await session.commit()
    return run


async def mark_recovery_required(
    session: AsyncSession,
    run_id: UUID,
    lease_token: str,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> AgentRun:
    """Move a run to ``recovery_required``: automatic replay is prohibited.

    Used when an external side effect's outcome is unknown and no
    reconciliation hook could prove it did not occur.
    """
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    _check_transition(run, rt.RECOVERY_REQUIRED)
    run.status = rt.RECOVERY_REQUIRED
    run.error = reason
    run.lease_owner = None
    run.lease_token = None
    run.lease_expires_at = None
    run.last_progress_at = _now()
    entry = AgentRunJournalEntry(
        run_id=run_id,
        sequence=await _next_journal_sequence(session, run_id),
        kind=rt.JOURNAL_VALIDATION,
        data={"status": rt.RECOVERY_REQUIRED, "reason": reason, **(evidence or {})},
        checkpoint_sequence=run.checkpoint_sequence,
    )
    session.add(entry)
    await session.commit()
    return run


async def latest_checkpoint(
    session: AsyncSession, run_id: UUID
) -> AgentRunCheckpoint | None:
    """Return the latest committed checkpoint for resume, if any."""
    return (
        await session.execute(
            select(AgentRunCheckpoint)
            .where(AgentRunCheckpoint.run_id == run_id)
            .order_by(AgentRunCheckpoint.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
