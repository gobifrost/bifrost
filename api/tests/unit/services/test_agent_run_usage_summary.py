"""Unit tests for the per-run usage summary (cost, peak context, cache rate)."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.ai_usage import AIUsage
from src.services.agent_run_usage_summary import summarize_run_usage


def _run(agent_id, parent=None) -> AgentRun:
    return AgentRun(
        id=uuid4(),
        agent_id=agent_id,
        trigger_type="delegation" if parent else "manual",
        status="completed",
        iterations_used=0,
        tokens_used=0,
        parent_run_id=parent.id if parent else None,
        created_at=datetime.now(timezone.utc),
    )


def _usage(run: AgentRun, *, input_tokens: int, cache_read: int, cost: str) -> AIUsage:
    return AIUsage(
        agent_run_id=run.id,
        provider="test",
        model="test-model",
        input_tokens=input_tokens,
        output_tokens=100,
        cache_read_tokens=cache_read,
        cache_write_tokens=0,
        cost=Decimal(cost),
    )


def _response_step(run: AgentRun, number: int, input_tokens: int) -> AgentRunStep:
    return AgentRunStep(
        run_id=run.id,
        step_number=number,
        type="llm_response",
        content={"usage": {"input_tokens": input_tokens, "output_tokens": 100}},
    )


@pytest.mark.asyncio
async def test_summary_covers_delegate_tree_but_context_is_own_conversation(
    db_session, seed_agent
):
    root = _run(seed_agent.id)
    child = _run(seed_agent.id, parent=root)
    grandchild = _run(seed_agent.id, parent=child)
    db_session.add_all([root, child, grandchild])
    await db_session.flush()
    db_session.add_all(
        [
            _usage(root, input_tokens=10_000, cache_read=8_000, cost="0.0100"),
            _usage(root, input_tokens=12_000, cache_read=10_000, cost="0.0050"),
            _usage(child, input_tokens=3_000, cache_read=0, cost="0.0020"),
            _usage(grandchild, input_tokens=40_000, cache_read=0, cost="0.0300"),
            _response_step(root, 2, 10_000),
            _response_step(root, 4, 12_000),
            # A delegate's larger context belongs to its own window.
            _response_step(grandchild, 2, 40_000),
        ]
    )
    await db_session.flush()

    summaries = await summarize_run_usage(db_session, [root.id, child.id])

    root_summary = summaries[root.id]
    assert Decimal(root_summary.cost) == Decimal("0.0470")
    assert Decimal(root_summary.delegate_cost) == Decimal("0.0320")
    assert root_summary.peak_context_tokens == 12_000
    assert root_summary.cache_hit_rate == pytest.approx(18_000 / 65_000)

    child_summary = summaries[child.id]
    assert Decimal(child_summary.cost) == Decimal("0.0320")
    assert child_summary.peak_context_tokens is None
    assert child_summary.cache_hit_rate == 0.0


@pytest.mark.asyncio
async def test_runs_without_usage_are_omitted(db_session, seed_agent):
    run = _run(seed_agent.id)
    db_session.add(run)
    await db_session.flush()

    assert await summarize_run_usage(db_session, [run.id]) == {}
    assert await summarize_run_usage(db_session, []) == {}
