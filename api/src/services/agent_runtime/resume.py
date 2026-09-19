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

from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.services.agent_runtime import run_store
from src.services.agent_runtime import types as rt
from src.services.agent_runtime.checkpoint_codec import decode_messages
from src.services.agent_runtime.tool_invocations import (
    ReclaimReport,
    list_invocations,
    reclaim_in_flight,
)


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

    ``completed`` maps provider tool-call ID to the stored result string.
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
    completed = {
        inv.provider_tool_call_id: (
            (inv.result or {}).get("text", "")
            if isinstance(inv.result, dict)
            else str(inv.result)
        )
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
    starts: dict[int, datetime] = {}
    for entry in entries:
        data = entry.data or {}
        if entry.kind in (rt.JOURNAL_RESUME, rt.JOURNAL_LEASE_RECOVERY):
            attempt = data.get("attempt")
            if isinstance(attempt, int) and attempt not in starts:
                created = entry.created_at
                if created is not None:
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    starts[attempt] = created
    ordered = sorted(starts)
    total = 0.0
    for index, attempt in enumerate(ordered):
        if attempt >= current_attempt:
            break
        begin = starts[attempt]
        end = starts[ordered[index + 1]] if index + 1 < len(ordered) else None
        if end is not None:
            total += max(0.0, (end - begin).total_seconds())
    return total
