"""Durable asynchronous delegation: children as independent AgentRuns.

``delegate_to_<agent>`` tools no longer execute nested runs inline. The tool
raises ``CallDeferred``; the engine admits one independently queued child per
call, checkpoints the parent into ``waiting_child``, and releases the
worker. Child terminalization wakes the parent exactly once, and the resumed
parent consumes the child result through ``DeferredToolResults`` — the same
Pydantic invocation contract pinned in Task 1.

Idempotency: the child UUID is derived deterministically from
``(parent_run_id, tool_call_id)``, and the deferral intent is journaled on
the parent before admission. Re-suspension reuses the existing child, and a
child that finishes before the parent's waiting transaction commits is
discovered (and immediately re-woken) by the suspending worker itself.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent, AgentDelegation
from src.services.agent_runtime import run_store
from src.services.agent_runtime import types as rt

logger = logging.getLogger(__name__)

MAX_DELEGATION_DEPTH = 5
"""Maximum nesting depth for durable delegation trees."""

DELEGATION_KIND = "delegate"
"""Journal intent kind for single-child delegation."""


def delegation_child_id(parent_run_id: UUID, tool_call_id: str) -> UUID:
    """Deterministic child ID so re-suspension reuses one child row."""
    return uuid5(NAMESPACE_URL, f"agent-delegation:{parent_run_id}:{tool_call_id}")


@dataclass(frozen=True)
class DelegationSpec:
    """One parent-selected child invocation."""

    tool_call_id: str
    tool_name: str
    target_agent_id: UUID
    task: str
    output_schema: dict[str, Any] | None = None


def delegation_call_metadata(spec: DelegationSpec) -> dict[str, Any]:
    """Metadata carried on the CallDeferred suspension."""
    return {
        "kind": DELEGATION_KIND,
        "tool_call_id": spec.tool_call_id,
        "tool_name": spec.tool_name,
        "target_agent_id": str(spec.target_agent_id),
        "task": spec.task,
        "output_schema": spec.output_schema,
    }


async def delegation_depth(session: AsyncSession, parent_run_id: UUID) -> int:
    """Count ancestors by walking parent_run_id (bounded by the max depth)."""
    depth = 0
    current: UUID | None = parent_run_id
    while current is not None and depth <= MAX_DELEGATION_DEPTH:
        row = await session.get(AgentRun, current)
        if row is None or row.parent_run_id is None:
            break
        current = row.parent_run_id
        depth += 1
    return depth


async def resolve_delegate_target(
    session: AsyncSession,
    *,
    parent_agent_id: UUID,
    parent_org_id: UUID | None,
    tool_name: str,
    snapshot_delegates: list[dict[str, Any]] | None = None,
) -> Agent:
    """Re-validate a delegation grant inside the suspending transaction.

    The immutable snapshot's delegate list is authoritative when present so
    a resumed run cannot adopt an edited delegation set; otherwise the live
    grant is used.
    """
    from src.services.execution.agent_helpers import agent_delegation_slug

    expected_slug = tool_name
    target: Agent | None = None
    if snapshot_delegates is not None:
        for delegate in snapshot_delegates:
            if agent_delegation_slug(delegate.get("name", "")) == expected_slug:
                target_id = delegate.get("id")
                if target_id is not None:
                    target = await session.get(Agent, UUID(str(target_id)))
                break
        if target is None:
            raise ToolSuspendError(
                f"Delegation target for '{tool_name}' is not in the run's "
                "snapshotted delegation set."
            )
    else:
        result = await session.execute(
            select(Agent)
            .join(
                AgentDelegation,
                AgentDelegation.child_agent_id == Agent.id,
            )
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
            )
            .where(
                AgentDelegation.parent_agent_id == parent_agent_id,
                Agent.is_active.is_(True),
            )
        )
        for candidate in result.scalars().all():
            if agent_delegation_slug(candidate.name) == expected_slug:
                target = candidate
                break
        if target is None:
            raise ToolSuspendError(
                f"Delegation target for '{tool_name}' is not an "
                "active grant of this agent."
            )
    if target.organization_id is not None and target.organization_id != parent_org_id:
        raise ToolSuspendError(
            f"Delegation target '{target.name}' is outside the parent "
            "agent's organization."
        )
    return target


class ToolSuspendError(Exception):
    """A deferred tool cannot suspend (bad grant, depth, auth); fail the run."""


async def _journal_intent(
    session: AsyncSession,
    run_id: UUID,
    tool_call_id: str,
    child_run_id: UUID,
    target: Agent,
    task: str,
) -> None:
    entry = AgentRunJournalEntry(
        run_id=run_id,
        sequence=await run_store.next_journal_sequence(session, run_id),
        kind=rt.JOURNAL_DELEGATION,
        data={
            "tool_call_id": tool_call_id,
            "child_run_id": str(child_run_id),
            "target_agent_id": str(target.id),
            "target_agent_name": target.name,
            "task": task[:2000],
        },
        checkpoint_sequence=None,
    )
    session.add(entry)
    await session.flush()


async def find_delegation_intent(
    session: AsyncSession, run_id: UUID, tool_call_id: str
) -> AgentRunJournalEntry | None:
    """Return the journaled intent for one deferred delegation call, if any."""
    entries = (
        await session.execute(
            select(AgentRunJournalEntry)
            .where(
                AgentRunJournalEntry.run_id == run_id,
                AgentRunJournalEntry.kind == rt.JOURNAL_DELEGATION,
            )
            .order_by(AgentRunJournalEntry.sequence)
        )
    ).scalars().all()
    for entry in entries:
        if (entry.data or {}).get("tool_call_id") == tool_call_id:
            return entry
    return None


async def admit_delegated_child(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    parent_run_id: UUID,
    root_run_id: UUID,
    spec: DelegationSpec,
    org_id: UUID | None,
    caller: dict[str, Any] | None,
    caller_context: dict[str, Any] | None,
    correlation: dict[str, Any] | None,
) -> UUID:
    """Admit one child run idempotently; publish its queue nudge.

    The child UUID is deterministic, so a repeated suspend after a crash
    reuses the admitted row instead of forking a second child. The child's
    own configuration is snapshotted at admission.
    """
    from src.services.execution.agent_run_service import enqueue_agent_run

    child_id = delegation_child_id(parent_run_id, spec.tool_call_id)
    async with session_factory() as session:
        existing = await session.get(AgentRun, child_id)
    if existing is not None:
        if existing.parent_run_id != parent_run_id:
            raise ToolSuspendError(
                "Delegation identity collision: child row belongs to "
                "another parent."
            )
        from src.jobs.rabbitmq import publish_message

        try:
            await publish_message("agent-runs", {"run_id": str(child_id)})
        except Exception:
            logger.warning(
                "Delegation re-nudge failed for %s", child_id, exc_info=True
            )
        return child_id

    child_correlation = dict(correlation or {})
    child_correlation.setdefault("parent_run_id", str(parent_run_id))
    await enqueue_agent_run(
        agent_id=str(spec.target_agent_id),
        trigger_type="delegation",
        input_data={
            "task": spec.task,
            "_delegated_from_run_id": str(parent_run_id),
            "locators": dict(caller_context or {}),
        },
        trigger_source=f"agent-run:{parent_run_id}",
        output_schema=spec.output_schema,
        org_id=str(org_id) if org_id else None,
        caller_user_id=(caller or {}).get("user_id"),
        caller_email=(caller or {}).get("email"),
        caller_name=(caller or {}).get("name"),
        caller_context=caller_context,
        correlation=child_correlation,
        parent_run_id=str(parent_run_id),
        root_run_id=str(root_run_id),
        run_id=str(child_id),
    )
    return child_id


async def suspend_for_deferred_calls(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    parent_run_id: UUID,
    lease_token: str,
    agent: Agent,
    execution_snapshot: dict[str, Any] | None,
    tool_calls: list,
    caller: dict[str, Any] | None,
    caller_context: dict[str, Any] | None,
    correlation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Admit deferred children and park the parent in ``waiting_child``.

    One transaction journals the intent; the child admission is idempotent;
    the waiting transition releases the lease. A child that already finished
    (before this transaction committed) wakes the parent immediately so the
    same parent resumes without stalling.
    """
    from src.services.execution.agent_helpers import find_delegated_agent

    if not tool_calls:
        raise ToolSuspendError("No deferred tool calls to suspend for.")
    specs: list[DelegationSpec] = []
    async with session_factory() as session:
        parent = await session.get(AgentRun, parent_run_id)
        if parent is None:
            raise ToolSuspendError(f"Parent run {parent_run_id} not found.")
        depth = await delegation_depth(session, parent_run_id)
        if depth >= MAX_DELEGATION_DEPTH:
            raise ToolSuspendError(
                f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) exceeded."
            )
        snapshot_delegates = (
            (execution_snapshot or {}).get("delegated_agents")
            if execution_snapshot is not None
            else None
        )
        for call in tool_calls:
            args = call.args_as_dict() if hasattr(call, "args_as_dict") else dict(call.args or {})
            task = args.get("task", "")
            if not task:
                raise ToolSuspendError("No task provided for delegation.")
            live_target = find_delegated_agent(agent, call.tool_name)
            if live_target is None and snapshot_delegates is None:
                raise ToolSuspendError(
                    f"Delegation target for '{call.tool_name}' not found."
                )
            target = await resolve_delegate_target(
                session,
                parent_agent_id=agent.id,
                parent_org_id=agent.organization_id,
                tool_name=call.tool_name,
                snapshot_delegates=snapshot_delegates,
            )
            output_schema = args.get("output_schema")
            if output_schema is not None and not isinstance(output_schema, dict):
                raise ToolSuspendError(
                    "Delegation output_schema must be a JSON object."
                )
            specs.append(
                DelegationSpec(
                    tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    target_agent_id=target.id,
                    task=task,
                    output_schema=output_schema,
                )
            )
        root_run_id = parent.root_run_id or parent_run_id
        org_id = parent.org_id

    child_ids: dict[str, UUID] = {}
    for spec in specs:
        child_ids[spec.tool_call_id] = await admit_delegated_child(
            session_factory,
            parent_run_id=parent_run_id,
            root_run_id=root_run_id,
            spec=spec,
            org_id=org_id,
            caller=caller,
            caller_context=caller_context,
            correlation=correlation,
        )

    # Journal the intent, then park the parent. Separate fenced transactions;
    # admission idempotency + post-wait terminal check close every crash/race
    # window between them.
    async with session_factory() as session:
        locked = (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.id == parent_run_id)
                .with_for_update(of=AgentRun)
            )
        ).scalar_one_or_none()
        if locked is None:
            raise ToolSuspendError(f"Parent run {parent_run_id} not found.")
        run_store.require_lease(locked, lease_token)
        for spec in specs:
            target = await session.get(Agent, spec.target_agent_id)
            assert target is not None
            if await find_delegation_intent(
                session, parent_run_id, spec.tool_call_id
            ) is None:
                await _journal_intent(
                    session,
                    parent_run_id,
                    spec.tool_call_id,
                    child_ids[spec.tool_call_id],
                    target,
                    spec.task,
                )
        await session.commit()

    async with session_factory() as session:
        await run_store.transition_waiting(
            session,
            parent_run_id,
            lease_token,
            rt.WAITING_CHILD,
            journal_data={
                "child_run_ids": [str(child_ids[s.tool_call_id]) for s in specs],
            },
        )

    # A child may have finished before the waiting transaction committed.
    # Wake immediately so the same parent resumes instead of stalling.
    woken = False
    async with session_factory() as session:
        for spec in specs:
            child = await session.get(AgentRun, child_ids[spec.tool_call_id])
            if child is not None and child.status in rt.TERMINAL_STATUSES:
                woken = True
                break
    if woken:
        async with session_factory() as session:
            try:
                await run_store.wake_run(
                    session,
                    parent_run_id,
                    reason="child already terminal at suspend",
                )
            except rt.InvalidTransitionError:
                woken = False
        if woken:
            from src.jobs.rabbitmq import publish_message

            try:
                await publish_message(
                    "agent-runs", {"run_id": str(parent_run_id)}
                )
            except Exception:
                logger.warning(
                    "Parent re-nudge failed for %s", parent_run_id, exc_info=True
                )
    return {
        "status": "suspended",
        "child_run_ids": [str(child_ids[s.tool_call_id]) for s in specs],
        "woken": woken,
    }


def child_result_text(child: AgentRun) -> str:
    """Render a terminal child's outcome as one ordered tool result string."""
    output = child.output
    if child.status == "completed":
        if isinstance(output, dict):
            text = output.get("text", json.dumps(output, default=str))
            return str(text) if text is not None else "Delegation completed."
        if output is None:
            return "Delegation completed with no output."
        return str(output)
    error = child.error or f"Delegation ended with status {child.status}"
    return f"Error: {error}"


async def collect_deferred_results(
    session: AsyncSession, parent_run_id: UUID
) -> dict[str, str]:
    """Build tool_call_id -> result text for terminal deferred children."""
    results: dict[str, str] = {}
    entries = (
        await session.execute(
            select(AgentRunJournalEntry)
            .where(
                AgentRunJournalEntry.run_id == parent_run_id,
                AgentRunJournalEntry.kind == rt.JOURNAL_DELEGATION,
            )
            .order_by(AgentRunJournalEntry.sequence)
        )
    ).scalars().all()
    for entry in entries:
        data = entry.data or {}
        tool_call_id = data.get("tool_call_id")
        child_run_id = data.get("child_run_id")
        if not tool_call_id or not child_run_id or tool_call_id in results:
            continue
        child = await session.get(AgentRun, UUID(str(child_run_id)))
        if child is not None and child.status in rt.TERMINAL_STATUSES:
            results[tool_call_id] = child_result_text(child)
    return results


async def deferred_tool_call_ids(
    session: AsyncSession, parent_run_id: UUID
) -> set[str]:
    """Tool-call IDs the parent suspended on (answered via deferred results)."""
    entries = (
        await session.execute(
            select(AgentRunJournalEntry)
            .where(
                AgentRunJournalEntry.run_id == parent_run_id,
                AgentRunJournalEntry.kind == rt.JOURNAL_DELEGATION,
            )
        )
    ).scalars().all()
    return {
        str((entry.data or {}).get("tool_call_id"))
        for entry in entries
        if (entry.data or {}).get("tool_call_id")
    }


async def wake_parent_for_child(
    session_factory: async_sessionmaker[AsyncSession],
    child_run_id: UUID,
) -> bool:
    """Wake a waiting parent when one of its children terminalizes.

    Idempotent: only the first completion transitions the parent; late
    duplicates find a non-waiting parent and return False. Never raises for
    a parent that is already running, terminal, or gone.
    """
    async with session_factory() as session:
        child = await session.get(AgentRun, child_run_id)
        if child is None or child.parent_run_id is None:
            return False
        if child.status not in rt.TERMINAL_STATUSES:
            return False
        parent = await session.get(AgentRun, child.parent_run_id)
        if parent is None:
            return False
        if parent.status not in (rt.WAITING_CHILD, rt.WAITING_CHILDREN):
            return False
        intent = None
        entries = (
            await session.execute(
                select(AgentRunJournalEntry)
                .where(
                    AgentRunJournalEntry.run_id == parent.id,
                    AgentRunJournalEntry.kind == rt.JOURNAL_DELEGATION,
                )
                .order_by(AgentRunJournalEntry.sequence)
            )
        ).scalars().all()
        for entry in entries:
            if (entry.data or {}).get("child_run_id") == str(child_run_id):
                intent = entry
                break
        if intent is None:
            return False
        try:
            await run_store.wake_run(
                session,
                parent.id,
                reason=f"child {child_run_id} {child.status}",
            )
        except rt.InvalidTransitionError:
            return False
    from src.jobs.rabbitmq import publish_message

    try:
        await publish_message("agent-runs", {"run_id": str(parent.id)})
    except Exception:
        logger.warning(
            "Parent wake nudge failed for %s", parent.id, exc_info=True
        )
    logger.info("Woke parent run %s for terminal child %s", parent.id, child_run_id)
    return True


async def cascade_cancel(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client,
    root_run_id: UUID,
) -> int:
    """Cancel every unfinished descendant of a run; returns rows touched.

    Queued/waiting rows (no worker) terminalize immediately; running rows
    get the Redis cancel flag so their worker stops at the next boundary.
    """
    touched = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AgentRun).where(AgentRun.root_run_id == root_run_id)
            )
        ).scalars().all()
        descendants = [row for row in rows if row.id != root_run_id]
        now = datetime.now(timezone.utc)
        for row in descendants:
            if row.status in rt.TERMINAL_STATUSES:
                continue
            if row.status in ("queued", *rt.INACTIVE_WAIT_STATUSES):
                row.status = "cancelled"
                row.error = f"Cancelled with parent run {root_run_id}"
                row.completed_at = now
                row.lease_owner = None
                row.lease_token = None
                row.lease_expires_at = None
                touched += 1
            elif row.status == "running":
                row.status = "cancelling"
                touched += 1
        await session.commit()
        flags = [
            f"bifrost:agent_run:{row.id}:cancel"
            for row in descendants
            if row.status in ("cancelling", "running")
        ]
    for flag in flags:
        try:
            await redis_client.set(flag, "1", ex=3600)
        except Exception:
            logger.debug("Failed to set cascade cancel flag %s", flag)
    return touched
