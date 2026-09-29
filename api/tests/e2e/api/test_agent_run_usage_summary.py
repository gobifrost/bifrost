"""Agent-run list and detail expose cost, peak context, and cache rate."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.agents import Agent
from src.models.orm.ai_usage import AIUsage


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def run_with_delegate(db_session: AsyncSession) -> AsyncGenerator[AgentRun, None]:
    now = datetime.now(timezone.utc)
    agent = Agent(
        id=uuid4(),
        name=f"Usage Summary Agent {uuid4().hex[:8]}",
        description="usage-summary",
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
    db_session.add(agent)
    await db_session.flush()

    root = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=2,
        tokens_used=25_300,
        completed_at=now,
    )
    child = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="delegation",
        status="timeout",
        iterations_used=1,
        tokens_used=3_100,
        parent_run_id=root.id,
        completed_at=now,
    )
    db_session.add_all([root, child])
    await db_session.flush()
    db_session.add_all(
        [
            AIUsage(
                agent_run_id=root.id,
                provider="test",
                model="test-model",
                input_tokens=10_000,
                output_tokens=100,
                cache_read_tokens=0,
                cost=Decimal("0.0100"),
            ),
            AIUsage(
                agent_run_id=root.id,
                provider="test",
                model="test-model",
                input_tokens=12_000,
                output_tokens=100,
                cache_read_tokens=9_000,
                cost=Decimal("0.0040"),
            ),
            AIUsage(
                agent_run_id=child.id,
                provider="test",
                model="test-model",
                input_tokens=3_000,
                output_tokens=100,
                cache_read_tokens=0,
                cost=Decimal("0.0020"),
            ),
            AgentRunStep(
                run_id=root.id,
                step_number=2,
                type="llm_response",
                content={"usage": {"input_tokens": 12_000, "output_tokens": 100}},
            ),
        ]
    )
    await db_session.commit()

    yield root

    await db_session.execute(delete(AgentRun).where(AgentRun.id.in_([child.id, root.id])))
    await db_session.execute(delete(Agent).where(Agent.id == agent.id))
    await db_session.commit()


async def test_list_and_detail_report_the_same_usage_summary(
    e2e_client, platform_admin, run_with_delegate
):
    expected = {
        "cost": Decimal("0.0160"),
        "delegate_cost": Decimal("0.0020"),
        "peak_context_tokens": 12_000,
        "cache_hit_rate": 9_000 / 25_000,
    }

    listed = e2e_client.get(
        "/api/agent-runs",
        params={"agent_id": str(run_with_delegate.agent_id)},
        headers=platform_admin.headers,
    )
    assert listed.status_code == 200, listed.text
    summaries = {
        item["id"]: item["usage_summary"] for item in listed.json()["items"]
    }
    detail = e2e_client.get(
        f"/api/agent-runs/{run_with_delegate.id}",
        headers=platform_admin.headers,
    )
    assert detail.status_code == 200, detail.text

    for summary in (summaries[str(run_with_delegate.id)], detail.json()["usage_summary"]):
        assert Decimal(summary["cost"]) == expected["cost"]
        assert Decimal(summary["delegate_cost"]) == expected["delegate_cost"]
        assert summary["peak_context_tokens"] == expected["peak_context_tokens"]
        assert summary["cache_hit_rate"] == pytest.approx(expected["cache_hit_rate"])
