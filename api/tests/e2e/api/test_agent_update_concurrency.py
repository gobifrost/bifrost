"""PUT /api/agents/{id} stale-guard atomicity under concurrent overlap.

Two overlapping updates carrying the same ``If-Unmodified-Since`` must
serialize: exactly one wins (200 + history row), the other fails closed
(412) with no history row and no grant mutation. The row lock makes the
compare/write atomic; without it both requests could pass the check.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_prompt_history import AgentPromptHistory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def race_agent(e2e_client, platform_admin) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Race Agent {uuid4().hex[:8]}",
            "system_prompt": "Original prompt.",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}", headers=platform_admin.headers
        )
    except Exception as e:
        logger.debug(f"fixture cleanup error: {e}")


def _put(e2e_client, headers, agent_id, prompt, since, barrier: Barrier):
    barrier.wait(timeout=30)
    return e2e_client.put(
        f"/api/agents/{agent_id}",
        json={"system_prompt": prompt, "change_reason": f"race-{prompt[-1]}"},
        headers={**headers, "If-Unmodified-Since": since},
    )


async def test_concurrent_same_base_update_serializes(
    e2e_client, platform_admin, race_agent, db_session: AsyncSession
):
    agent_id = race_agent["id"]
    before = e2e_client.get(
        f"/api/agents/{agent_id}", headers=platform_admin.headers
    )
    assert before.status_code == 200, before.text
    since = before.json()["updated_at"]
    history_before = (
        await db_session.execute(
            select(func.count())
            .select_from(AgentPromptHistory)
            .where(AgentPromptHistory.agent_id == race_agent["id"])
        )
    ).scalar() or 0

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(
            _put,
            e2e_client,
            platform_admin.headers,
            agent_id,
            "Winner prompt A.",
            since,
            barrier,
        )
        future_b = pool.submit(
            _put,
            e2e_client,
            platform_admin.headers,
            agent_id,
            "Winner prompt B.",
            since,
            barrier,
        )
        statuses = sorted(
            [future_a.result(timeout=120).status_code,
             future_b.result(timeout=120).status_code]
        )
    assert statuses == [200, 412], statuses

    final = e2e_client.get(
        f"/api/agents/{agent_id}", headers=platform_admin.headers
    )
    assert final.status_code == 200, final.text
    assert final.json()["system_prompt"] in ("Winner prompt A.", "Winner prompt B.")

    # Exactly one history row was added, matching the winner; the stale
    # loser mutated nothing.
    rows = (
        await db_session.execute(
            select(AgentPromptHistory).where(
                AgentPromptHistory.agent_id == race_agent["id"]
            )
        )
    ).scalars().all()
    assert len(rows) == history_before + 1, [r.new_prompt for r in rows]
    assert rows[-1].new_prompt == final.json()["system_prompt"]
    assert rows[-1].previous_prompt == "Original prompt."
    await db_session.execute(
        delete(AgentPromptHistory).where(
            AgentPromptHistory.agent_id == race_agent["id"]
        )
    )
    await db_session.commit()
