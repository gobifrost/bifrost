"""Agent stats service: per-agent + fleet aggregations."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete

from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent, Conversation
from src.models.orm.ai_usage import AIUsage
from src.services.agent_stats import get_agent_stats, get_fleet_stats


@pytest_asyncio.fixture
async def seed_runs_for_agent(db_session, seed_agent):
    """Insert several runs across the last 7 days for the agent, with mixed status/verdict/cost."""
    now = datetime.now(timezone.utc)
    runs = []
    rows = [
        # (days_ago, status, verdict, duration_ms, cost)
        (0, "completed", "up", 1500, Decimal("0.02")),
        (1, "completed", None, 2000, Decimal("0.03")),
        (1, "failed", None, 500, Decimal("0.01")),
        (3, "completed", "down", 1800, Decimal("0.04")),
        (5, "completed", None, 1200, Decimal("0.02")),
    ]
    for days_ago, status, verdict, duration_ms, cost in rows:
        r = AgentRun(
            id=uuid4(),
            agent_id=seed_agent.id,
            trigger_type="test",
            status=status,
            iterations_used=1,
            tokens_used=100,
            duration_ms=duration_ms,
            verdict=verdict,
            verdict_set_at=(now - timedelta(days=days_ago)) if verdict else None,
            created_at=now - timedelta(days=days_ago),
        )
        db_session.add(r)
        await db_session.flush()
        db_session.add(AIUsage(
            agent_run_id=r.id,
            organization_id=None,
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
            cost=cost,
            timestamp=now - timedelta(days=days_ago),
        ))
        runs.append(r)
    await db_session.commit()
    yield runs
    # Cleanup committed rows
    for r in runs:
        await db_session.execute(delete(AIUsage).where(AIUsage.agent_run_id == r.id))
        await db_session.execute(delete(AgentRun).where(AgentRun.id == r.id))
    await db_session.commit()


@pytest_asyncio.fixture
async def seed_agents_with_runs(db_session):
    """Two agents, both active, each with one completed run."""
    now = datetime.now(timezone.utc)
    agents = []
    runs = []
    for i in range(2):
        a = Agent(
            id=uuid4(),
            name=f"Stats Test Agent {i}_{uuid4().hex[:6]}",
            description="stats test",
            system_prompt="test",
            channels=["chat"],
            access_level=AgentAccessLevel.AUTHENTICATED,
            organization_id=None,
            is_active=True,
            knowledge_sources=[],
            system_tools=[],
            created_by="test@example.com",
            created_at=now,
            updated_at=now,
        )
        db_session.add(a)
        await db_session.flush()
        agents.append(a)

        r = AgentRun(
            id=uuid4(),
            agent_id=a.id,
            trigger_type="test",
            status="completed",
            iterations_used=1,
            tokens_used=10,
            duration_ms=1000,
            created_at=now,
        )
        db_session.add(r)
        await db_session.flush()
        runs.append(r)
    await db_session.commit()
    yield {"agents": agents, "runs": runs}
    for r in runs:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == r.id))
    for a in agents:
        await db_session.execute(delete(Agent).where(Agent.id == a.id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_per_agent_stats(db_session, seed_agent, seed_runs_for_agent):
    stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
    assert stats.runs_7d == 5
    assert 0.0 <= stats.success_rate <= 1.0
    # 4 completed / 5 total = 0.8
    assert abs(stats.success_rate - 0.8) < 0.001
    assert stats.avg_duration_ms > 0
    assert stats.total_cost_7d > 0
    assert stats.last_run_at is not None
    assert len(stats.runs_by_day) == 7
    assert stats.needs_review == 1  # one verdict='down'
    # Unreviewed: completed runs with verdict=None
    # 4 completed, 1 has 'up', 1 has 'down', 2 unreviewed
    assert stats.unreviewed == 2


@pytest.mark.asyncio
async def test_per_agent_stats_empty(db_session, seed_agent):
    stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
    assert stats.runs_7d == 0
    assert stats.success_rate == 0.0  # no runs == 0% success
    assert stats.avg_duration_ms == 0
    assert stats.last_run_at is None


@pytest.mark.asyncio
async def test_fleet_stats(db_session, seed_agents_with_runs):
    s = await get_fleet_stats(db_session, org_id=None, window_days=7)
    assert s.total_runs >= 2
    assert s.active_agents >= 2
    assert 0.0 <= s.avg_success_rate <= 1.0
    assert s.needs_review >= 0


@pytest.mark.asyncio
async def test_summarizer_cost_is_included_in_total_cost_7d(
    db_session, seed_agent
):
    """Regression: summarizer-generated AIUsage rows must roll up into
    AgentStats.total_cost_7d so a backfill's $$ impact is visible in the
    UI's Spend (7d) card without separate bookkeeping.
    """
    now = datetime.now(timezone.utc)
    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=100,
        duration_ms=1000,
        created_at=now,
    )
    db_session.add(run)
    await db_session.flush()

    # Primary agent-work AIUsage row (what the autonomous executor writes).
    db_session.add(AIUsage(
        agent_run_id=run.id,
        organization_id=None,
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost=Decimal("0.01"),
        timestamp=now,
    ))
    # Summarizer-generated AIUsage row (what run_summarizer writes).
    # The service sums AIUsage.cost for all usage rows linked to the run,
    # regardless of which model/provider wrote them — this asserts that.
    db_session.add(AIUsage(
        agent_run_id=run.id,
        organization_id=None,
        provider="anthropic",
        model="claude-haiku-4-5",
        input_tokens=80,
        output_tokens=40,
        cost=Decimal("0.005"),
        timestamp=now,
    ))
    await db_session.commit()

    stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
    assert stats.total_cost_7d == Decimal("0.015"), (
        f"Expected 0.015 (0.01 agent-work + 0.005 summarizer), got {stats.total_cost_7d}. "
        "If this fails, the summarizer's cost is being silently dropped from "
        "the Spend (7d) metric — admins running a backfill will see no $$ "
        "movement and be confused."
    )

    # Cleanup
    await db_session.execute(delete(AIUsage).where(AIUsage.agent_run_id == run.id))
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


# ---------------------------------------------------------------------------
# Chat-channel rollup (issue #200)
#
# Chat agents don't write AgentRun rows — their AIUsage rows are tagged with
# conversation_id only. Without a chat-aware rollup, every dashboard card on
# a chat agent shows zero. These tests pin the per-conversation count and
# cost-via-conversation sum so the cards stop lying.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_chat_conversations_for_agent(db_session, seed_agent, seed_user):
    """Two conversations on `seed_agent`, each with an AIUsage row.

    One conversation is recent (updated today); the other was updated 4 days
    ago. Both fall inside the 7-day window so they count toward the rollup.
    """
    now = datetime.now(timezone.utc)
    convs = []
    usages = []

    # Recent conversation, two AIUsage rows (multi-message)
    c1 = Conversation(
        id=uuid4(),
        agent_id=seed_agent.id,
        user_id=seed_user.id,
        channel="chat",
        is_active=True,
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    db_session.add(c1)
    await db_session.flush()
    convs.append(c1)
    for cost in (Decimal("0.02"), Decimal("0.03")):
        u = AIUsage(
            conversation_id=c1.id,
            organization_id=None,
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
            cost=cost,
            timestamp=now,
        )
        db_session.add(u)
        usages.append(u)

    # Older conversation, one AIUsage row
    c2 = Conversation(
        id=uuid4(),
        agent_id=seed_agent.id,
        user_id=seed_user.id,
        channel="chat",
        is_active=True,
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=4),
    )
    db_session.add(c2)
    await db_session.flush()
    convs.append(c2)
    u = AIUsage(
        conversation_id=c2.id,
        organization_id=None,
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=80,
        output_tokens=40,
        cost=Decimal("0.05"),
        timestamp=now - timedelta(days=4),
    )
    db_session.add(u)
    usages.append(u)

    await db_session.commit()
    yield {"conversations": convs, "usages": usages}

    for u in usages:
        await db_session.execute(delete(AIUsage).where(AIUsage.id == u.id))
    for c in convs:
        await db_session.execute(delete(Conversation).where(Conversation.id == c.id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_per_agent_stats_counts_chat_conversations(
    db_session, seed_agent, seed_chat_conversations_for_agent
):
    """Chat agent with no AgentRun rows still reports non-zero runs_7d.

    A run for a chat agent is one Conversation. The fixture seeds two
    conversations, so runs_7d should be 2.
    """
    stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
    assert stats.runs_7d == 2, (
        f"Expected 2 conversations counted as runs, got {stats.runs_7d}. "
        "If this is 0, the chat-channel rollup is missing — chat agents "
        "will continue to display RUNS 0 on the dashboard."
    )


@pytest.mark.asyncio
async def test_per_agent_stats_sums_chat_ai_usage_cost(
    db_session, seed_agent, seed_chat_conversations_for_agent
):
    """Chat agent total_cost_7d sums AIUsage.cost via conversation_id.

    The fixture seeds 0.02 + 0.03 + 0.05 = 0.10 inside the 7-day window.
    """
    stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
    assert stats.total_cost_7d == Decimal("0.10"), (
        f"Expected 0.10 (sum of chat AIUsage.cost), got {stats.total_cost_7d}. "
        "Chat-channel cost is being silently dropped from the Spend (7d) card."
    )


@pytest.mark.asyncio
async def test_per_agent_stats_chat_last_run_at_uses_conversation_updated_at(
    db_session, seed_agent, seed_chat_conversations_for_agent
):
    """last_run_at for a chat agent is max(Conversation.updated_at).

    The fixture seeds one conversation updated today and one updated 4 days
    ago. last_run_at should be ~now (within 1 minute).
    """
    stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
    assert stats.last_run_at is not None
    drift = abs((datetime.now(timezone.utc) - stats.last_run_at).total_seconds())
    assert drift < 60, (
        f"last_run_at drifted {drift}s from now; expected ~0s. "
        "Chat agent's last_run_at should track the most-recent "
        "Conversation.updated_at, not Conversation.created_at."
    )


@pytest.mark.asyncio
async def test_per_agent_stats_chat_window_excludes_old_conversations(
    db_session, seed_agent, seed_user
):
    """Conversations updated outside the window don't count toward runs_7d.

    Window is on Conversation.updated_at (matches the "active recently"
    semantic chosen for #200). An ancient conversation must not pollute
    the count even if it has fresh AIUsage rows from a re-summarize or
    historical re-pull.
    """
    now = datetime.now(timezone.utc)
    ancient = Conversation(
        id=uuid4(),
        agent_id=seed_agent.id,
        user_id=seed_user.id,
        channel="chat",
        is_active=True,
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )
    db_session.add(ancient)
    await db_session.flush()
    await db_session.commit()

    try:
        stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
        assert stats.runs_7d == 0, (
            f"Conversation outside the 7d window must not count, got runs_7d={stats.runs_7d}. "
            "Window-on-updated_at semantics are not being applied."
        )
    finally:
        await db_session.execute(
            delete(Conversation).where(Conversation.id == ancient.id)
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_per_agent_stats_chat_cost_window_uses_ai_usage_timestamp(
    db_session, seed_agent, seed_user
):
    """total_cost_7d windows on AIUsage.timestamp, not Conversation.updated_at.

    A conversation updated yesterday but with an AIUsage row stamped 30
    days ago should NOT have that ancient cost in the 7d card. Runs count
    on conversation lifecycle; cost counts on when money was actually spent.
    """
    now = datetime.now(timezone.utc)
    conv = Conversation(
        id=uuid4(),
        agent_id=seed_agent.id,
        user_id=seed_user.id,
        channel="chat",
        is_active=True,
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=1),  # in window
    )
    db_session.add(conv)
    await db_session.flush()

    ancient_usage = AIUsage(
        conversation_id=conv.id,
        organization_id=None,
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost=Decimal("0.99"),
        timestamp=now - timedelta(days=30),  # outside window
    )
    db_session.add(ancient_usage)
    await db_session.commit()

    try:
        stats = await get_agent_stats(seed_agent.id, db_session, window_days=7)
        assert stats.total_cost_7d == Decimal("0"), (
            f"Expected 0 (ancient AIUsage outside 7d window), got {stats.total_cost_7d}. "
            "Cost is incurred at AIUsage time — window should be on AIUsage.timestamp, "
            "not Conversation.updated_at."
        )
    finally:
        await db_session.execute(delete(AIUsage).where(AIUsage.id == ancient_usage.id))
        await db_session.execute(delete(Conversation).where(Conversation.id == conv.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_fleet_stats_includes_chat_conversations(
    db_session, seed_agent, seed_chat_conversations_for_agent
):
    """Fleet total_runs and total_cost_7d include chat-channel activity.

    With 2 chat conversations on the seeded agent and no autonomous runs,
    fleet total_runs must be ≥ 2 and total_cost_7d must include the chat
    AIUsage cost (0.10).
    """
    s = await get_fleet_stats(db_session, org_id=None, window_days=7)
    assert s.total_runs >= 2, (
        f"Fleet total_runs={s.total_runs} excludes chat conversations. "
        "RUNS (7D) card on the fleet dashboard will undercount."
    )
    assert s.total_cost_7d >= Decimal("0.10"), (
        f"Fleet total_cost_7d={s.total_cost_7d} excludes chat AIUsage cost. "
        "Fleet Spend card will undercount."
    )
