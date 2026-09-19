"""Resume planning: deterministic restart from committed boundaries.

After a worker loss, the next owner claims the same AgentRun, reconciles
in-flight tool invocations, and rebuilds the exact next step from the
latest checkpoint:

| Last durable state            | Resume action                              |
|-------------------------------|--------------------------------------------|
| Before model request          | Issue model request                        |
| Model response committed      | Execute pending calls, then model request  |
| Tool planned, not started     | Execute tool                               |
| Tool completed                | Replay stored result, no re-execution      |
| Tool running at lease expiry  | Reconcile or ``recovery_required``         |
| Waiting on children           | Stay inactive (not claimable)              |
| Sleeping                      | Stay inactive until ``wake_at``            |
| Final result committed        | Finalize and publish completion event      |

Provider calls may repeat if the process died before their response was
committed (duplicated provider cost), but a committed tool side effect
never repeats.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.agent_runs import AgentRun, AgentRunJoin, AgentRunJournalEntry
from src.services.agent_runtime import run_store
from src.services.agent_runtime import types as rt
from src.services.agent_runtime.checkpoint_codec import decode_messages
from src.services.agent_runtime.tool_invocations import (
    ReclaimReport,
    list_invocations,
    reclaim_in_flight,
)

logger = logging.getLogger(__name__)


@dataclass
class ResumePlan:
    """Everything the next worker needs to continue the same AgentRun."""

    run_id: UUID
    lease_token: str
    attempt: int
    history: list[ModelMessage] = field(default_factory=list)
    checkpoint_sequence: int = 0
    deferred_results: dict[str, str] = field(default_factory=dict)
    deferred_pending: set[str] = field(default_factory=set)


def append_missing_tool_returns(
    history: list[ModelMessage],
    completed: dict[str, Any],
) -> list[ModelMessage]:
    """Replay stored results for tool calls missing returns in history.

    ``completed`` maps provider tool-call ID to a lossless model-history
    rendering of the stored result.
    Calls already answered in history are untouched; anything else gets a
    synthetic ``ToolReturnPart`` so the resumed model request observes the
    committed outcome instead of re-emitting the call.
    """
    answered: set[str] = set()
    for message in history:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    answered.add(part.tool_call_id)
    pending_calls: list[ToolCallPart] = []
    for message in history:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart) and part.tool_call_id not in answered:
                    pending_calls.append(part)
    missing = [
        call
        for call in pending_calls
        if call.tool_call_id in completed and call.tool_call_id not in answered
    ]
    if not missing:
        return history
    resume_request = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=call.tool_name,
                content=completed[call.tool_call_id],
                tool_call_id=call.tool_call_id,
            )
            for call in missing
        ]
    )
    return [*history, resume_request]


async def prepare_resume(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    lease_token: str,
) -> tuple[ResumePlan | None, ReclaimReport | None, str | None]:
    """Reconcile in-flight work and rebuild resume history.

    Returns ``(plan, report, unrecoverable_reason)``. When the reason is
    set, the caller must move the run to ``recovery_required`` instead of
    executing. ``plan`` is ``None`` only in that case.
    """
    report = await reclaim_in_flight(session_factory, run_id, lease_token)
    if report.unrecoverable_reason is not None:
        return None, report, report.unrecoverable_reason
    from src.services.agent_runtime.delegation import (
        collect_deferred_results,
        deferred_tool_call_ids,
    )
    from src.services.agent_runtime.timers import collect_timer_results

    async with session_factory() as session:
        checkpoint = await run_store.latest_checkpoint(session, run_id)
        history: list[ModelMessage] = (
            decode_messages(checkpoint.state) if checkpoint is not None else []
        )
        sequence = checkpoint.sequence if checkpoint is not None else 0
        invocations = await list_invocations(session, run_id)
        run_row = await session.get(AgentRun, run_id)
        attempt = run_row.attempt if run_row is not None else 0
        deferred_results = await collect_deferred_results(session, run_id)
        deferred_pending = (
            await deferred_tool_call_ids(session, run_id)
        ) - set(deferred_results)
        timer_results, timer_pending = await collect_timer_results(
            session, run_id
        )
        deferred_results.update(timer_results)
        deferred_pending |= timer_pending
    from src.services.agent_runtime.tool_invocations import invocation_result_text

    completed = {
        inv.provider_tool_call_id: invocation_result_text(inv.result)
        for inv in invocations
        if inv.state == "completed" and inv.provider_tool_call_id
    }
    history = append_missing_tool_returns(history, completed)
    return (
        ResumePlan(
            run_id=run_id,
            lease_token=lease_token,
            attempt=attempt or 0,
            history=history,
            checkpoint_sequence=sequence,
            deferred_results=deferred_results,
            deferred_pending=deferred_pending,
        ),
        report,
        None,
    )


async def restore_pending_wait(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    lease_token: str,
    pending_call_ids: set[str],
) -> str | None:
    """Re-park a reclaimed run interrupted between intent and wait commit.

    A deferred intent is durable before its inactive transition.  Recovery
    must therefore restore that inactive state rather than offering an
    unfinished call to the model or failing the run.
    """
    from src.services.agent_runtime.delegation import FANOUT_KIND
    from src.services.agent_runtime.timers import TIMER_KIND

    async with session_factory() as session:
        entries = (
            await session.execute(
                select(AgentRunJournalEntry)
                .where(AgentRunJournalEntry.run_id == run_id)
                .order_by(AgentRunJournalEntry.sequence.desc())
            )
        ).scalars().all()
        for entry in entries:
            data = entry.data or {}
            tool_call_id = str(data.get("tool_call_id") or "")
            if tool_call_id not in pending_call_ids:
                continue
            # Hold the parent lock while checking completion and parking it.
            # A terminal child/join that commits after this check then blocks
            # on that same parent lock and wakes it after the wait commits;
            # one that finished earlier is observed here and replayed instead.
            parent = (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.id == run_id)
                    .with_for_update(of=AgentRun)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if parent is None:
                return None
            run_store.require_lease(parent, lease_token)
            if entry.kind == TIMER_KIND and data.get("wake_at"):
                try:
                    wake_at = datetime.fromisoformat(str(data["wake_at"]))
                    if wake_at.tzinfo is None:
                        wake_at = wake_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
                await run_store.transition_waiting(
                    session,
                    run_id,
                    lease_token,
                    rt.SLEEPING,
                    wake_at=wake_at,
                    journal_data={"tool_call_id": tool_call_id, "recovered": True},
                )
                return rt.SLEEPING
            if entry.kind == FANOUT_KIND:
                try:
                    join_id = UUID(str(data["join_id"]))
                except (KeyError, TypeError, ValueError):
                    return None
                join = await session.get(AgentRunJoin, join_id)
                if join is not None and join.status == "complete":
                    continue
                await run_store.transition_waiting(
                    session,
                    run_id,
                    lease_token,
                    rt.WAITING_CHILDREN,
                    journal_data={"tool_call_id": tool_call_id, "recovered": True},
                )
                return rt.WAITING_CHILDREN
            if entry.kind == rt.JOURNAL_DELEGATION:
                try:
                    child_id = UUID(str(data["child_run_id"]))
                except (KeyError, TypeError, ValueError):
                    return None
                child = await session.get(AgentRun, child_id)
                if child is not None and child.status in rt.TERMINAL_STATUSES:
                    continue
                await run_store.transition_waiting(
                    session,
                    run_id,
                    lease_token,
                    rt.WAITING_CHILD,
                    journal_data={"tool_call_id": tool_call_id, "recovered": True},
                )
                return rt.WAITING_CHILD
    return None


async def active_seconds_used(
    session: AsyncSession, run_id: UUID, current_attempt: int
) -> float:
    """Sum wall-clock seconds spent in attempts before the current one.

    Attempt boundaries come from the journal's claim entries, so inactive
    ``waiting_*``/``sleeping`` periods never count toward ``max_run_timeout``.
    """
    entries = (
        await session.execute(
            select(AgentRunJournalEntry)
            .where(AgentRunJournalEntry.run_id == run_id)
            .order_by(AgentRunJournalEntry.sequence)
        )
    ).scalars().all()
    starts: dict[int, tuple[int, datetime, dict[str, Any]]] = {}
    for entry in entries:
        data = entry.data or {}
        if entry.kind in (rt.JOURNAL_RESUME, rt.JOURNAL_LEASE_RECOVERY):
            attempt = data.get("attempt")
            if isinstance(attempt, int) and attempt not in starts:
                created = entry.created_at
                if created is not None:
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    starts[attempt] = (entry.sequence, created, data)
    ordered = sorted(starts)
    total = 0.0
    for index, attempt in enumerate(ordered):
        if attempt >= current_attempt:
            break
        start_sequence, begin, _start_data = starts[attempt]
        next_claim = (
            starts[ordered[index + 1]] if index + 1 < len(ordered) else None
        )
        end_sequence = next_claim[0] if next_claim is not None else None
        end = next_claim[1] if next_claim is not None else None
        # A wait boundary ends active time. Without one, the next reclaim
        # records the previous worker's final lease expiration; cap there so
        # stopped-cluster downtime cannot consume active timeout.
        if next_claim is not None:
            prior_expiry = next_claim[2].get("prior_lease_expires_at")
            if isinstance(prior_expiry, str):
                try:
                    lease_end = datetime.fromisoformat(prior_expiry)
                    if lease_end.tzinfo is None:
                        lease_end = lease_end.replace(tzinfo=timezone.utc)
                    end = min(end, lease_end) if end is not None else lease_end
                except ValueError:
                    logger.warning(
                        "Ignoring malformed prior lease expiry for run %s attempt %s",
                        run_id,
                        attempt,
                    )
        for entry in entries:
            if entry.sequence <= start_sequence:
                continue
            if end_sequence is not None and entry.sequence >= end_sequence:
                break
            if entry.kind == rt.JOURNAL_WAIT and entry.created_at is not None:
                wait_end = entry.created_at
                if wait_end.tzinfo is None:
                    wait_end = wait_end.replace(tzinfo=timezone.utc)
                end = min(end, wait_end) if end is not None else wait_end
                break
        if end is not None:
            total += max(0.0, (end - begin).total_seconds())
    return total
