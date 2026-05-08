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
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    runs_q = select(AgentRun).where(
        AgentRun.agent_id == agent_id,
        AgentRun.created_at >= cutoff,
    )
    runs = (await db.execute(runs_q)).scalars().all()

    runs_count = len(runs)
    completed = [r for r in runs if r.status == "completed"]
    success_rate = (len(completed) / runs_count) if runs_count else 0.0
    durations = [r.duration_ms for r in runs if r.duration_ms is not None]
    avg_duration_ms = int(sum(durations) / len(durations)) if durations else 0

    if runs:
        cost_q = select(func.coalesce(func.sum(AIUsage.cost), 0)).where(
            AIUsage.agent_run_id.in_([r.id for r in runs])
        )
        total_cost = (await db.execute(cost_q)).scalar() or Decimal("0")
    else:
        total_cost = Decimal("0")

    last_run_at = max((r.created_at for r in runs), default=None)

    # Per-day bucket counts (oldest first; index 0 = window_days days ago,
    # index -1 = today).
    buckets = [0] * window_days
    now = datetime.now(timezone.utc)
    for r in runs:
        day_offset = (now - r.created_at).days
        if 0 <= day_offset < window_days:
            buckets[window_days - 1 - day_offset] += 1

    total_cost_decimal = (
        total_cost if isinstance(total_cost, Decimal) else Decimal(total_cost)
    )

    # Chat-channel rollup. Both queries hit ix_conversations_agent_id +
    # ix_ai_usage_conversation; the cost query also benefits from
    # ix_ai_usage_timestamp.
    chat_count_q = select(func.count(Conversation.id)).where(
        Conversation.agent_id == agent_id,
        Conversation.updated_at >= cutoff,
    )
    chat_runs_count = (await db.execute(chat_count_q)).scalar() or 0

    chat_cost_q = (
        select(func.coalesce(func.sum(AIUsage.cost), 0))
        .join(Conversation, Conversation.id == AIUsage.conversation_id)
        .where(
            Conversation.agent_id == agent_id,
            AIUsage.timestamp >= cutoff,
        )
    )
    chat_cost = (await db.execute(chat_cost_q)).scalar() or Decimal("0")
    chat_cost_decimal = (
        chat_cost if isinstance(chat_cost, Decimal) else Decimal(chat_cost)
    )

    last_chat_q = select(func.max(Conversation.updated_at)).where(
        Conversation.agent_id == agent_id,
        Conversation.updated_at >= cutoff,
    )
    last_chat_at = (await db.execute(last_chat_q)).scalar()

    runs_count += chat_runs_count
    total_cost_decimal += chat_cost_decimal
    if last_chat_at is not None:
        last_run_at = (
            last_chat_at if last_run_at is None else max(last_run_at, last_chat_at)
        )

    return AgentStatsResponse(
        agent_id=agent_id,
        runs_7d=runs_count,
        success_rate=success_rate,
        avg_duration_ms=avg_duration_ms,
        total_cost_7d=total_cost_decimal,
        last_run_at=last_run_at,
        runs_by_day=buckets,
        needs_review=sum(1 for r in runs if r.verdict == "down"),
        unreviewed=sum(
            1 for r in runs if r.verdict is None and r.status == "completed"
        ),
    )


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
