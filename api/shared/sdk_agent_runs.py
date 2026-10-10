"""Shared business service for SDK-consumed agent run enqueue/get operations.

Single implementation for both entry points:

- the HTTP handlers
  (``api/src/routers/agent_runs.py::enqueue_agent_run_request`` /
  ``get_agent_run``) serving SDK callers (``api/bifrost/agents.py``
  ``enqueue`` / ``get_run`` — and through them the ``run`` (enqueue +
  wait) and ``wait`` (poll get_run) facades), and
- the same handlers reached by workflow children over the worker-local
  engine socket.

Both paths share the exact agent name lookup, the Solution active
guard, the paused short-circuit, the run queue payload and actor
attribution, the org scope, the run visibility rule, and the
steps/usage/totals detail construction — so HTTP and worker-local results are
identical by construction.

Only the SDK-consumed ``enqueue`` / ``get_run`` operations live here, plus
the ``run_as`` decision (``resolve_agent_run_actor`` /
``resolve_rerun_actor``) that ``/execute`` and rerun share with
``enqueue``. Agent definitions, backfill, summaries, cancel, verdict, flag
conversations, and dry-run keep their router-level logic.

Parent-side only: imports SQLAlchemy models and Redis. A workflow child
never imports this module (it stays DB-free and reaches it over the
engine socket).

The service takes an explicit trusted session, principal, and validated
parameters — never a ``Request``, JWT, or raw child claims. HTTP
authentication, ``HTTPException`` mapping, and the 202/200 status split
stay in the router.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from typing import TYPE_CHECKING

from shared import access_checks
from src.core.cache.keys import agent_run_steps_stream_key
from src.core.cache.redis_client import get_redis
from src.core.log_safety import log_safe
from src.core.principal import UserPrincipal
from src.models.contracts.agent_runs import (
    AgentRunChildResponse,
    AgentRunDetailResponse,
    AgentRunEnqueueResponse,
    AgentRunStepResponse,
    PausedResponse,
)
from src.models.contracts.executions import AIUsagePublicSimple, AIUsageTotalsSimple
from src.services.execution.agent_run_access import agent_run_visibility_conditions

if TYPE_CHECKING:
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.agents import Agent
    from src.services.authorization.explain import RunAsTarget

logger = logging.getLogger(__name__)


class SdkAgentRunError(Exception):
    """SDK agent run enqueue/get failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Missing entities are 404, an
    inactive Solution is 409 — matching the historical handler responses
    exactly.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def resolve_executable_agent(
    session: AsyncSession,
    *,
    agent_name: str,
    principal: UserPrincipal,
) -> Agent:
    """Resolve an agent by name and enforce solution execution availability.

    Case-insensitive name lookup; unknown names are 404. Once a candidate
    row is found by name, access is verified through the exact same
    ``AgentRepository.get_agent_with_access_check`` gate REST uses for
    ``GET /api/agents/{id}`` — org cascade + role-based/private access
    level — so a caller can never execute another org's or another
    user's private agent merely by guessing its name. An agent bound to
    a non-active Solution is 409 — the reinstall hint matches the
    historical handler exactly.

    Args:
        session: Database session.
        agent_name: Agent name as validated on the request.
        principal: The auth-verified caller, used for the access check.

    Raises:
        SdkAgentRunError: 404 when the agent name is unknown or not
            accessible to ``principal``; 409 when the agent belongs to an
            inactive Solution.
    """
    from shared.scope_resolver import has_scope_bypass
    from src.models.orm.agents import Agent
    from src.models.orm.solutions import Solution
    from src.repositories.agents import AgentRepository

    result = await session.execute(select(Agent.id).where(Agent.name.ilike(agent_name)))
    agent_id = result.scalar_one_or_none()

    if agent_id is None:
        raise SdkAgentRunError(404, f"Agent '{agent_name}' not found")

    repo = AgentRepository(
        session,
        org_id=principal.organization_id,
        user_id=principal.user_id,
        is_superuser=has_scope_bypass(
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=principal.is_provider_org,
        ),
        is_external=principal.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if agent is None:
        raise SdkAgentRunError(404, f"Agent '{agent_name}' not found")

    if agent.solution_id is not None:
        sol_result = await session.execute(
            select(Solution.status).where(Solution.id == agent.solution_id)
        )
        if sol_result.scalar_one_or_none() != "active":
            raise SdkAgentRunError(
                409,
                f"Agent '{agent.name}' belongs to an inactive solution. "
                "Reinstall the solution to execute this agent.",
            )
    access_checks.note_launch("agents.execute", agent.organization_id, principal.organization_id, subject=f"agent:{agent.id}")

    return agent


class AgentRunActor(NamedTuple):
    """Where an agent run runs and who it acts as."""

    org_id: UUID | None
    # None: the caller acts.
    run_as_user_id: UUID | None


async def authorize_agent_run_as(
    session: AsyncSession, principal: UserPrincipal, run_as_user_id: UUID
) -> RunAsTarget | None:
    """The user an agent run acts as when ``run_as`` names ``run_as_user_id``;
    None when it names the caller, who then acts as without ``run_as``.

    ``authorize_run_as`` decides, and its refusal keeps its status. Naming
    the run user is not impersonation, but the run still acts as that user
    (a workflow's engine token is not the run user), so only existence is
    checked.

    Raises:
        SdkAgentRunError: the helper's 403/404/400, or 404 when the named
            run user no longer exists.
    """
    from src.services.authorization.explain import load_run_as_target
    from src.services.authorization.impersonation import RunAsError, authorize_run_as

    try:
        target = await authorize_run_as(session, principal, run_as_user_id)
    except RunAsError as exc:
        raise SdkAgentRunError(exc.status_code, exc.detail) from None
    if target is not None or run_as_user_id == principal.user_id:
        return target
    target = await load_run_as_target(session, run_as_user_id)
    if target is None:
        raise SdkAgentRunError(404, f"Run As user '{run_as_user_id}' not found")
    return target


def _acting_org(agent_org_id: UUID | None, target: RunAsTarget) -> UUID | None:
    """Where a run acting as ``target`` runs: the agent's organization, which
    must be the user's, or the user's home organization for a Global agent."""
    if agent_org_id is None:
        return target.organization_id
    if target.organization_id != agent_org_id:
        raise SdkAgentRunError(400, "Run As user must belong to the agent's organization")
    return agent_org_id


async def resolve_agent_run_actor(
    session: AsyncSession,
    principal: UserPrincipal,
    agent: Agent,
    run_as: UUID | None,
) -> AgentRunActor:
    """The run's organization and acting user for a launch by ``principal``.

    Without ``run_as``, or when it names the caller, the run is in the
    caller's organization and the caller acts. Otherwise the run acts as the
    authorized user: in the agent's organization when the agent is
    org-scoped (the user must belong to it), otherwise in the user's home
    organization.

    Raises:
        SdkAgentRunError: as ``authorize_agent_run_as``; 400 when an
            org-scoped agent's organization is not the user's.
    """
    caller_acts = AgentRunActor(principal.organization_id, None)
    if run_as is None:
        return caller_acts
    target = await authorize_agent_run_as(session, principal, run_as)
    if target is None:
        return caller_acts
    return AgentRunActor(_acting_org(agent.organization_id, target), target.user_id)


async def resolve_rerun_actor(
    session: AsyncSession, principal: UserPrincipal, run: AgentRun
) -> AgentRunActor:
    """The organization and acting user for ``principal`` rerunning ``run``.

    A run that acted as someone keeps acting as them, decided again for
    this caller, and the organization follows the user's current home
    organization as a launch's does. Otherwise (or when the acting user is
    the caller) the rerun keeps the run's organization and the caller acts.

    Raises:
        SdkAgentRunError: as ``resolve_agent_run_actor``.
    """
    from src.models.orm.agents import Agent

    keep = AgentRunActor(run.org_id, None)
    if run.run_as_user_id is None:
        return keep
    target = await authorize_agent_run_as(session, principal, run.run_as_user_id)
    if target is None:
        return keep
    agent_org_id = (
        await session.execute(select(Agent.organization_id).where(Agent.id == run.agent_id))
    ).scalar_one()
    return AgentRunActor(_acting_org(agent_org_id, target), target.user_id)


async def enqueue_sdk_agent_run(
    session: AsyncSession,
    principal: UserPrincipal,
    *,
    agent_name: str,
    input_data: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    run_as: UUID | None = None,
) -> AgentRunEnqueueResponse | PausedResponse:
    """Queue an agent run for the SDK ``agents.enqueue`` operation.

    Error precedence (unchanged from the HTTP handler):
    1. Unknown agent name (case-insensitive) → 404.
    2. Agent bound to a non-active Solution → 409.
    3. Paused agent (``is_active=False``) → ``PausedResponse`` (the
       caller maps this to HTTP 200, not an error).
    4. ``run_as`` refused → the status ``resolve_agent_run_actor`` gives.

    The queue payload and actor attribution match the historical
    handler: ``trigger_type="api"``, the caller's org, user id, email,
    and name, ``sync=False``. With ``run_as`` the row also records the
    acting user and the run's org follows ``resolve_agent_run_actor``;
    the caller fields and lineage stay the caller's. The durable row
    commit and queue publish (including failure marking) stay inside
    ``enqueue_agent_run``.

    Args:
        session: Database session (agent lookup only; the run row is
            committed by ``enqueue_agent_run`` in its own session).
        principal: The auth-verified trusted principal.
        agent_name: Agent name as validated on the request.
        input_data: Structured input for the run.
        output_schema: Optional JSON Schema for the expected output.
        run_as: The user to act as, when not the caller.

    Raises:
        SdkAgentRunError: 404 when the agent name is unknown; 409 when
            the agent belongs to an inactive Solution; a refused
            ``run_as``.
    """
    agent = await resolve_executable_agent(session, agent_name=agent_name, principal=principal)

    if not agent.is_active:
        return PausedResponse(
            message=f"Agent '{agent.name}' is paused. Request not processed.",
            agent_id=agent.id,
        )

    from shared.run_lineage import principal_lineage
    from src.services.execution.agent_run_service import enqueue_agent_run

    actor = await resolve_agent_run_actor(session, principal, agent, run_as)
    run_id = await enqueue_agent_run(
        agent_id=str(agent.id),
        trigger_type="api",
        input_data=input_data,
        output_schema=output_schema,
        org_id=str(actor.org_id) if actor.org_id else None,
        caller_user_id=str(principal.user_id),
        caller_email=principal.email,
        caller_name=getattr(principal, "name", None),
        sync=False,
        lineage=await principal_lineage(session, principal),
        run_as_user_id=actor.run_as_user_id,
    )
    return AgentRunEnqueueResponse(run_id=UUID(run_id), run_as_user_id=actor.run_as_user_id)


async def get_sdk_agent_run(
    session: AsyncSession,
    principal: UserPrincipal,
    *,
    run_id: UUID,
) -> AgentRunDetailResponse:
    """Get an agent run detail for the SDK ``agents.get_run`` operation.

    Applies the shared run visibility rule — a run outside the
    principal's scope reads as missing (404), never forbidden. Detail
    construction matches the historical handler: AI usage entries plus
    aggregate totals, ordered delegation summaries, and the dual-read
    steps (Redis Stream while in-progress, DB once terminal, with DB
    fallback when the stream read fails).

    Args:
        session: Database session.
        principal: The auth-verified trusted principal.
        run_id: Agent run UUID.

    Raises:
        SdkAgentRunError: 404 when the run is missing or not visible to
            the principal.
    """
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.ai_usage import AIUsage

    query = (
        select(AgentRun)
        .options(selectinload(AgentRun.steps), selectinload(AgentRun.run_as_user))
        .where(AgentRun.id == run_id)
    )

    query = query.where(*agent_run_visibility_conditions(principal))

    result = await session.execute(query)
    run = result.scalar_one_or_none()

    if not run:
        raise SdkAgentRunError(404, f"Agent run {run_id} not found")

    # Fetch AI usage records for this run
    ai_usage_result = await session.execute(
        select(AIUsage)
        .where(AIUsage.agent_run_id == run_id)
        .order_by(AIUsage.timestamp)
    )
    ai_usage_entries = ai_usage_result.scalars().all()

    ai_usage_list: list[AIUsagePublicSimple] | None = None
    ai_totals_response: AIUsageTotalsSimple | None = None

    if ai_usage_entries:
        ai_usage_list = [
            AIUsagePublicSimple(
                provider=entry.provider,
                model=entry.model,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                cache_read_tokens=entry.cache_read_tokens,
                cache_write_tokens=entry.cache_write_tokens,
                reasoning_tokens=entry.reasoning_tokens,
                provider_cost=(str(entry.provider_cost) if entry.provider_cost is not None else None),
                cost=str(entry.cost) if entry.cost else None,
                duration_ms=entry.duration_ms,
                timestamp=entry.timestamp.isoformat(),
                sequence=entry.sequence,
            )
            for entry in ai_usage_entries
        ]

        # Calculate totals
        totals_result = await session.execute(
            select(
                func.sum(AIUsage.input_tokens).label("total_input"),
                func.sum(AIUsage.output_tokens).label("total_output"),
                func.sum(AIUsage.cache_read_tokens).label("total_cache_read"),
                func.sum(AIUsage.cache_write_tokens).label("total_cache_write"),
                func.sum(AIUsage.reasoning_tokens).label("total_reasoning"),
                func.sum(AIUsage.provider_cost).label("total_provider_cost"),
                func.sum(AIUsage.cost).label("total_cost"),
                func.sum(AIUsage.duration_ms).label("total_duration"),
                func.count(AIUsage.id).label("call_count"),
            ).where(AIUsage.agent_run_id == run_id)
        )
        totals_row = totals_result.one()
        ai_totals_response = AIUsageTotalsSimple(
            total_input_tokens=int(totals_row.total_input or 0),
            total_output_tokens=int(totals_row.total_output or 0),
            total_cache_read_tokens=int(totals_row.total_cache_read or 0),
            total_cache_write_tokens=int(totals_row.total_cache_write or 0),
            total_reasoning_tokens=int(totals_row.total_reasoning or 0),
            total_provider_cost=str(totals_row.total_provider_cost or Decimal("0")),
            total_cost=str(totals_row.total_cost or Decimal("0")),
            total_duration_ms=int(totals_row.total_duration or 0),
            call_count=int(totals_row.call_count or 0),
        )

    from src.services.agent_run_usage_summary import summarize_run_usage

    usage_summary = (await summarize_run_usage(session, [run_id])).get(run_id)

    # Fetch delegation sub-runs and their agents in one ordered query. Keep the
    # legacy ID list while also returning enough context for a user-facing view.
    child_runs_result = await session.execute(
        select(AgentRun)
        .options(joinedload(AgentRun.agent))
        .where(AgentRun.parent_run_id == run_id)
        .order_by(AgentRun.created_at, AgentRun.id)
    )
    child_runs = child_runs_result.scalars().all()
    child_run_ids = [child.id for child in child_runs]
    child_runs_response = [
        AgentRunChildResponse(
            id=child.id,
            agent_id=child.agent_id,
            agent_name=child.agent.name,
            status=child.status,
            asked=child.asked,
            did=child.did,
            answered=child.answered,
            duration_ms=child.duration_ms,
            created_at=child.created_at,
        )
        for child in child_runs
    ]

    # Dual-read steps: Redis Stream when in-progress, DB when complete
    steps_response: list[AgentRunStepResponse] = []
    is_in_progress = run.status in ("queued", "running", "cancelling")

    if is_in_progress:
        # Read from Redis Stream (steps are uncommitted in DB during execution)
        try:
            async with get_redis() as r:
                stream_key = agent_run_steps_stream_key(str(run_id))
                entries = await r.xrange(stream_key, min="-", max="+")  # type: ignore[misc]
                for _entry_id, data in entries:
                    content_raw = data.get("content", "{}")
                    content = json.loads(content_raw) if content_raw else None
                    tokens_str = data.get("tokens_used", "")
                    duration_str = data.get("duration_ms", "")
                    steps_response.append(AgentRunStepResponse(
                        id=UUID(data["id"]),
                        run_id=UUID(data["run_id"]),
                        step_number=int(data["step_number"]),
                        type=data["type"],
                        content=content,
                        tokens_used=int(tokens_str) if tokens_str else None,
                        duration_ms=int(duration_str) if duration_str else None,
                        created_at=datetime.fromisoformat(data["created_at"]),
                    ))
        except Exception:
            logger.warning(f"Failed to read steps from Redis for run {log_safe(run_id)}, falling back to DB")
            # Fall back to DB steps (may be empty if uncommitted)
            steps_response = [
                AgentRunStepResponse(
                    id=step.id, run_id=step.run_id, step_number=step.step_number,
                    type=step.type, content=step.content, tokens_used=step.tokens_used,
                    duration_ms=step.duration_ms, created_at=step.created_at,
                )
                for step in run.steps
            ]
    else:
        # Completed — read from DB (steps are committed)
        steps_response = [
            AgentRunStepResponse(
                id=step.id, run_id=step.run_id, step_number=step.step_number,
                type=step.type, content=step.content, tokens_used=step.tokens_used,
                duration_ms=step.duration_ms, created_at=step.created_at,
            )
            for step in run.steps
        ]

    return AgentRunDetailResponse(
        id=run.id,
        agent_id=run.agent_id,
        agent_name=run.agent.name if run.agent else None,
        trigger_type=run.trigger_type,
        trigger_source=run.trigger_source,
        conversation_id=run.conversation_id,
        event_delivery_id=run.event_delivery_id,
        input=run.input,
        output=run.output,
        status=run.status,
        error=run.error,
        org_id=run.org_id,
        caller_user_id=run.caller_user_id,
        caller_email=run.caller_email,
        caller_name=run.caller_name,
        run_as_user_id=run.run_as_user_id,
        run_as_user_name=run.run_as_user.name if run.run_as_user else None,
        run_as_user_email=run.run_as_user.email if run.run_as_user else None,
        iterations_used=run.iterations_used,
        tokens_used=run.tokens_used,
        budget_max_iterations=run.budget_max_iterations,
        budget_max_tokens=run.budget_max_tokens,
        duration_ms=run.duration_ms,
        llm_model=run.llm_model,
        asked=run.asked,
        did=run.did,
        answered=run.answered,
        metadata=run.run_metadata or {},
        confidence=run.confidence,
        confidence_reason=run.confidence_reason,
        summary_status=run.summary_status,
        summary_error=run.summary_error,
        verdict=run.verdict,
        verdict_note=run.verdict_note,
        verdict_set_at=run.verdict_set_at,
        verdict_set_by=run.verdict_set_by,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        parent_run_id=run.parent_run_id,
        child_run_ids=child_run_ids,
        child_runs=child_runs_response,
        steps=steps_response,
        ai_usage=ai_usage_list,
        ai_totals=ai_totals_response,
        usage_summary=usage_summary,
    )
