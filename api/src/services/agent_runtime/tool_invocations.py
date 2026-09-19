"""Durable tool invocations: planned/running/completed/failed/uncertain.

Each tool call gets one deterministic engine operation ID, exposed to
workflow/system tools as an idempotency/reconciliation key. A committed
result is never executed twice: on reclaim, completed invocations replay
their stored result, planned-but-unstarted calls execute, and calls left
``running`` reconcile through a registered hook or fail closed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.agent_runs import AgentRun, AgentToolInvocation
from src.services.agent_runtime import types as rt

logger = logging.getLogger(__name__)

SAFE_RETRY_TOOLS = frozenset({"search_knowledge"})
"""Read-only tools that are safe to re-execute after an uncertain boundary."""


def durable_operation_id(run_id: str, provider_tool_call_id: str) -> str:
    """Deterministic engine operation ID for one tool call.

    Regenerating the same inputs after a restart yields the same ID, so a
    second worker naturally deduplicates against the committed invocation.
    """
    return uuid5(NAMESPACE_URL, f"agent-tool:{run_id}:{provider_tool_call_id}").hex


@dataclass(frozen=True)
class ReconcileDecision:
    """Outcome of a tool-specific reconciliation hook."""

    action: Literal["retry", "recovered", "unrecoverable"]
    result: Any | None = None
    reason: str | None = None

    @staticmethod
    def retry() -> "ReconcileDecision":
        return ReconcileDecision(action="retry")

    @staticmethod
    def recovered(result: Any) -> "ReconcileDecision":
        return ReconcileDecision(action="recovered", result=result)

    @staticmethod
    def unrecoverable(reason: str) -> "ReconcileDecision":
        return ReconcileDecision(action="unrecoverable", reason=reason)


ReconcileHook = Any
"""``async (invocation_data: dict) -> ReconcileDecision``."""


class ReconciliationRegistry:
    """Tool-specific reconciliation hooks keyed by snapshotted tool identity."""

    def __init__(self) -> None:
        self._hooks: dict[str, ReconcileHook] = {}

    def register(self, tool_name: str, hook: ReconcileHook) -> None:
        self._hooks[tool_name] = hook

    def hook_for(self, tool_name: str) -> ReconcileHook | None:
        return self._hooks.get(tool_name)


RECONCILIATION_REGISTRY = ReconciliationRegistry()
"""Process-wide registry. Tool owners register idempotency/reconcile hooks."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    if not run.lease_token or run.lease_token != lease_token:
        raise rt.LeaseMismatchError(
            f"AgentRun {run.id}: stale or missing lease token"
        )


async def get_invocation(
    session: AsyncSession, run_id: UUID, provider_tool_call_id: str
) -> AgentToolInvocation | None:
    return (
        await session.execute(
            select(AgentToolInvocation).where(
                AgentToolInvocation.run_id == run_id,
                AgentToolInvocation.provider_tool_call_id == provider_tool_call_id,
            )
        )
    ).scalar_one_or_none()


async def list_invocations(
    session: AsyncSession, run_id: UUID
) -> list[AgentToolInvocation]:
    """Return every durable invocation for a run, oldest first."""
    return list(
        (
            await session.execute(
                select(AgentToolInvocation)
                .where(AgentToolInvocation.run_id == run_id)
                .order_by(AgentToolInvocation.started_at)
            )
        ).scalars().all()
    )


async def plan_invocation(
    session: AsyncSession,
    *,
    run_id: UUID,
    lease_token: str,
    provider_tool_call_id: str,
    tool_name: str,
    tool_version: str | None = None,
    tool_schema: dict | None = None,
    arguments: dict | None = None,
    commit: bool = True,
) -> AgentToolInvocation:
    """Persist a ``planned`` invocation; return the existing one on re-plan."""
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    existing = await get_invocation(session, run_id, provider_tool_call_id)
    if existing is not None:
        return existing
    operation_id = durable_operation_id(str(run_id), provider_tool_call_id)
    invocation = AgentToolInvocation(
        operation_id=operation_id,
        run_id=run_id,
        provider_tool_call_id=provider_tool_call_id,
        tool_name=tool_name,
        tool_version=tool_version,
        tool_schema=tool_schema,
        arguments=arguments,
        state="planned",
        idempotency_key=f"{run_id}:{operation_id}",
    )
    session.add(invocation)
    await session.flush()
    if commit:
        await session.commit()
    return invocation


async def mark_running(
    session: AsyncSession,
    *,
    run_id: UUID,
    lease_token: str,
    provider_tool_call_id: str,
    commit: bool = True,
) -> AgentToolInvocation:
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    invocation = await get_invocation(session, run_id, provider_tool_call_id)
    if invocation is None:
        raise rt.RunStoreError(
            f"No planned invocation for tool call {provider_tool_call_id}"
        )
    invocation.state = "running"
    invocation.started_at = _now()
    await session.flush()
    if commit:
        await session.commit()
    return invocation


async def complete_invocation(
    session: AsyncSession,
    *,
    run_id: UUID,
    lease_token: str,
    provider_tool_call_id: str,
    result: Any,
    commit: bool = True,
) -> AgentToolInvocation:
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    invocation = await get_invocation(session, run_id, provider_tool_call_id)
    if invocation is None:
        raise rt.RunStoreError(
            f"No planned invocation for tool call {provider_tool_call_id}"
        )
    invocation.state = "completed"
    invocation.result = result if isinstance(result, dict) else {"text": str(result)}
    invocation.completed_at = _now()
    await session.flush()
    if commit:
        await session.commit()
    return invocation


async def fail_invocation(
    session: AsyncSession,
    *,
    run_id: UUID,
    lease_token: str,
    provider_tool_call_id: str,
    error: str,
    commit: bool = True,
) -> AgentToolInvocation:
    run = await _locked_run(session, run_id)
    _require_lease(run, lease_token)
    invocation = await get_invocation(session, run_id, provider_tool_call_id)
    if invocation is None:
        raise rt.RunStoreError(
            f"No planned invocation for tool call {provider_tool_call_id}"
        )
    invocation.state = "failed"
    invocation.error = error
    invocation.completed_at = _now()
    await session.flush()
    if commit:
        await session.commit()
    return invocation


@dataclass
class ReclaimReport:
    """Result of reconciling a run's in-flight invocations after reclaim."""

    to_execute: list[AgentToolInvocation]
    recovered: list[AgentToolInvocation]
    unrecoverable_reason: str | None = None


async def reclaim_in_flight(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    lease_token: str,
    *,
    registry: ReconciliationRegistry = RECONCILIATION_REGISTRY,
) -> ReclaimReport:
    """Reconcile planned/running invocations left by a lost worker.

    - ``planned`` (never started): execute.
    - ``running`` on a safe-retry tool: reset to ``planned`` for execution.
    - ``running`` with a registered hook: ``recovered`` (persisted),
      ``retry`` (reset to planned), or ``unrecoverable`` (fail closed).
    - ``running`` otherwise: ``uncertain``; the caller must move the run to
      ``recovery_required`` with the returned reason.
    """
    report = ReclaimReport(to_execute=[], recovered=[])
    async with session_factory() as session:
        run = await _locked_run(session, run_id)
        _require_lease(run, lease_token)
        pending = (
            await session.execute(
                select(AgentToolInvocation).where(
                    AgentToolInvocation.run_id == run_id,
                    AgentToolInvocation.state.in_(("planned", "running")),
                )
            )
        ).scalars().all()
        for invocation in pending:
            if invocation.state == "planned":
                report.to_execute.append(invocation)
                continue
            # Left running when the lease expired: outcome unknown.
            invocation.state = "uncertain"
            await session.flush()
            tool_call_id = invocation.provider_tool_call_id or ""
            if invocation.tool_name in SAFE_RETRY_TOOLS:
                invocation.state = "planned"
                invocation.started_at = None
                await session.flush()
                report.to_execute.append(invocation)
                continue
            hook = registry.hook_for(invocation.tool_name)
            if hook is None:
                report.unrecoverable_reason = (
                    f"Tool '{invocation.tool_name}' (call {tool_call_id}) was "
                    "in flight when the worker was lost and has no "
                    "reconciliation hook; automatic replay is prohibited."
                )
                continue
            decision: ReconcileDecision = await hook(
                {
                    "operation_id": invocation.operation_id,
                    "run_id": str(run_id),
                    "tool_name": invocation.tool_name,
                    "tool_call_id": tool_call_id,
                    "arguments": invocation.arguments,
                    "idempotency_key": invocation.idempotency_key,
                }
            )
            if decision.action == "recovered":
                invocation.state = "completed"
                result = decision.result
                invocation.result = (
                    result if isinstance(result, dict) else {"text": str(result)}
                )
                invocation.completed_at = _now()
                invocation.reconciliation = {"hook": "recovered"}
                await session.flush()
                report.recovered.append(invocation)
            elif decision.action == "retry":
                invocation.state = "planned"
                invocation.started_at = None
                invocation.reconciliation = {"hook": "retry"}
                await session.flush()
                report.to_execute.append(invocation)
            else:
                invocation.reconciliation = {
                    "hook": "unrecoverable",
                    "reason": decision.reason,
                }
                await session.flush()
                report.unrecoverable_reason = decision.reason or (
                    f"Tool '{invocation.tool_name}' reconciliation refused replay."
                )
        await session.commit()
    return report
