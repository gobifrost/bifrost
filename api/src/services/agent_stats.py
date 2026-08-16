"""Agent stats — per-agent and fleet-level aggregations."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.agent_stats import AgentStatsResponse, FleetStatsResponse
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent, Conversation
from src.models.orm.ai_usage import AIUsage


async def get_agent_stats_batch(
    agent_ids: list[UUID],
    db: AsyncSession,
    *,
    window_days: int = 7,
) -> dict[UUID, AgentStatsResponse]:
    """Compute the list-card stats for many agents in four bounded queries."""
    if not agent_ids:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    now = datetime.now(timezone.utc)
    unique_agent_ids = list(dict.fromkeys(agent_ids))

    runs = (
        (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.agent_id.in_(unique_agent_ids),
                    AgentRun.created_at >= cutoff,
                )
            )
        )
        .scalars()
        .all()
    )

    run_ids = [run.id for run in runs]
    costs_by_agent: dict[UUID, Decimal] = {}
    if run_ids:
        cost_rows = (
            await db.execute(
                select(
                    AgentRun.agent_id,
                    func.coalesce(func.sum(AIUsage.cost), 0),
                )
                .join(AIUsage, AIUsage.agent_run_id == AgentRun.id)
                .where(AgentRun.id.in_(run_ids))
                .group_by(AgentRun.agent_id)
            )
        ).all()
        costs_by_agent = {
            agent_id: cost if isinstance(cost, Decimal) else Decimal(cost)
            for agent_id, cost in cost_rows
        }

    chat_rows = (
        await db.execute(
            select(
                Conversation.agent_id,
                func.count(Conversation.id),
                func.max(Conversation.updated_at),
            )
            .where(
                Conversation.agent_id.in_(unique_agent_ids),
                Conversation.updated_at >= cutoff,
            )
            .group_by(Conversation.agent_id)
        )
    ).all()
    chat_by_agent = {
        agent_id: (count, last_at) for agent_id, count, last_at in chat_rows
    }

    chat_cost_rows = (
        await db.execute(
            select(
                Conversation.agent_id,
                func.coalesce(func.sum(AIUsage.cost), 0),
            )
            .join(AIUsage, AIUsage.conversation_id == Conversation.id)
            .where(
                Conversation.agent_id.in_(unique_agent_ids),
                AIUsage.timestamp >= cutoff,
            )
            .group_by(Conversation.agent_id)
        )
    ).all()
    chat_costs_by_agent = {
        agent_id: cost if isinstance(cost, Decimal) else Decimal(cost)
        for agent_id, cost in chat_cost_rows
    }

    runs_by_agent: dict[UUID, list[AgentRun]] = {
        agent_id: [] for agent_id in unique_agent_ids
    }
    for run in runs:
        runs_by_agent[run.agent_id].append(run)

    result: dict[UUID, AgentStatsResponse] = {}
    for agent_id in unique_agent_ids:
        agent_runs = runs_by_agent[agent_id]
        run_count = len(agent_runs)
        completed = sum(1 for run in agent_runs if run.status == "completed")
        durations = [
            run.duration_ms for run in agent_runs if run.duration_ms is not None
        ]
        last_run_at = max(
            (run.created_at for run in agent_runs),
            default=None,
        )
        buckets = [0] * window_days
        for run in agent_runs:
            day_offset = (now - run.created_at).days
            if 0 <= day_offset < window_days:
                buckets[window_days - 1 - day_offset] += 1

        chat_count, last_chat_at = chat_by_agent.get(agent_id, (0, None))
        if last_chat_at is not None:
            last_run_at = (
                last_chat_at
                if last_run_at is None
                else max(last_run_at, last_chat_at)
            )

        result[agent_id] = AgentStatsResponse(
            agent_id=agent_id,
            runs_7d=run_count + chat_count,
            success_rate=(completed / run_count) if run_count else 0.0,
            avg_duration_ms=(
                int(sum(durations) / len(durations)) if durations else 0
            ),
            total_cost_7d=(
                costs_by_agent.get(agent_id, Decimal("0"))
                + chat_costs_by_agent.get(agent_id, Decimal("0"))
            ),
            last_run_at=last_run_at,
            runs_by_day=buckets,
            needs_review=sum(1 for run in agent_runs if run.verdict == "down"),
            unreviewed=sum(
                1
                for run in agent_runs
                if run.verdict is None and run.status == "completed"
            ),
        )

    return result


async def get_agent_stats(
    agent_id: UUID,
    db: AsyncSession,
    *,
    window_days: int = 7,
) -> AgentStatsResponse:
    """Per-agent stats over the last ``window_days`` (default 7).

    Returns counts, success rate, average duration, total cost, last-run
    timestamp, a per-day bucket histogram, and verdict-derived review
    counts. ``runs_by_day`` is oldest-first (index 0 = ``window_days`` days
    ago, index ``-1`` = today).

    Chat-channel rollup (issue #200): the chat executor doesn't write
    ``AgentRun`` rows — its ``AIUsage`` rows are tagged with
    ``conversation_id`` only. To keep dashboard cards honest for chat
    agents, this also counts each ``Conversation`` (windowed on
    ``updated_at``) as one run and adds chat ``AIUsage.cost`` (windowed on
    ``timestamp``, since cost is incurred at LLM-call time) to
    ``total_cost_7d``. Fields without a chat-side analog
    (``success_rate``, ``avg_duration_ms``, ``runs_by_day``,
    ``needs_review``, ``unreviewed``) intentionally stay keyed on
    ``AgentRun`` only — the deferred "tuning + chat conversations"
    follow-up will revisit those.
    """
    return (
        await get_agent_stats_batch(
            [agent_id],
            db,
            window_days=window_days,
        )
    )[agent_id]


async def get_fleet_stats(
    db: AsyncSession,
    *,
    org_id: UUID | None,
    window_days: int = 7,
) -> FleetStatsResponse:
    """Fleet-wide stats over the last ``window_days``.

    Optionally scoped to a single organization (org_id=None means
    cross-org, only allowed for superusers — the router enforces that).

    Chat-channel rollup mirrors :func:`get_agent_stats`: each
    ``Conversation`` updated in window counts as one run, and chat
    ``AIUsage`` cost (windowed on ``AIUsage.timestamp``) is added to the
    fleet spend. ``avg_success_rate`` stays keyed on ``AgentRun`` only —
    there is no "success" concept on a chat conversation.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    agent_filter = []
    if org_id is not None:
        agent_filter.append(Agent.organization_id == org_id)

    run_filter = [AgentRun.created_at >= cutoff]
    if org_id is not None:
        run_filter.append(AgentRun.org_id == org_id)

    total_runs = (
        await db.execute(select(func.count(AgentRun.id)).where(*run_filter))
    ).scalar() or 0
    completed = (
        await db.execute(
            select(func.count(AgentRun.id)).where(
                *run_filter, AgentRun.status == "completed"
            )
        )
    ).scalar() or 0
    active_agents = (
        await db.execute(
            select(func.count(Agent.id)).where(
                *agent_filter, Agent.is_active.is_(True)
            )
        )
    ).scalar() or 0
    needs_review = (
        await db.execute(
            select(func.count(AgentRun.id)).where(
                *run_filter, AgentRun.verdict == "down"
            )
        )
    ).scalar() or 0
    total_cost_q = (
        select(func.coalesce(func.sum(AIUsage.cost), 0))
        .join(AgentRun, AgentRun.id == AIUsage.agent_run_id)
        .where(*run_filter)
    )
    total_cost = (await db.execute(total_cost_q)).scalar() or Decimal("0")
    total_cost_decimal = (
        total_cost if isinstance(total_cost, Decimal) else Decimal(total_cost)
    )

    # Chat-channel rollup. Org scope is via the conversation's agent.
    chat_conv_q = (
        select(func.count(Conversation.id))
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(Conversation.updated_at >= cutoff)
    )
    if org_id is not None:
        chat_conv_q = chat_conv_q.where(Agent.organization_id == org_id)
    chat_runs = (await db.execute(chat_conv_q)).scalar() or 0

    chat_cost_q = (
        select(func.coalesce(func.sum(AIUsage.cost), 0))
        .join(Conversation, Conversation.id == AIUsage.conversation_id)
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(AIUsage.timestamp >= cutoff)
    )
    if org_id is not None:
        chat_cost_q = chat_cost_q.where(Agent.organization_id == org_id)
    chat_cost = (await db.execute(chat_cost_q)).scalar() or Decimal("0")
    chat_cost_decimal = (
        chat_cost if isinstance(chat_cost, Decimal) else Decimal(chat_cost)
    )

    return FleetStatsResponse(
        total_runs=total_runs + chat_runs,
        avg_success_rate=(completed / total_runs) if total_runs else 0.0,
        total_cost_7d=total_cost_decimal + chat_cost_decimal,
        active_agents=active_agents,
        needs_review=needs_review,
    )
