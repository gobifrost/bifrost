"""POST /api/agent-runs/execute against a paused agent — e2e tests.

A paused agent (``is_active=False``) is a graceful, expected condition. The
API must return HTTP 200 with ``status='paused'`` and ``accepted=False`` so
that downstream consumers (webhook senders, SDK callers) can discriminate
without treating it as an error.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio


logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def paused_agent(e2e_client, platform_admin) -> AsyncGenerator[dict, None]:
    """Create an agent then PUT ``is_active=False`` to pause it."""
    name = f"Paused Test Agent {uuid4().hex[:8]}"
    create = e2e_client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "test",
            "system_prompt": "test",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert create.status_code == 201, create.text
    agent = create.json()

    pause = e2e_client.put(
        f"/api/agents/{agent['id']}",
        json={"is_active": False},
        headers=platform_admin.headers,
    )
    assert pause.status_code == 200, pause.text
    assert pause.json()["is_active"] is False

    yield agent

    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}", headers=platform_admin.headers
        )
    except Exception as e:
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")


class TestPauseSemantics:
    async def test_enqueue_paused_agent_returns_200_with_paused_body(
        self, e2e_client, platform_admin, paused_agent
    ):
        res = e2e_client.post(
            "/api/agent-runs/enqueue",
            json={"agent_name": paused_agent["name"], "input": {}},
            headers=platform_admin.headers,
        )

        assert res.status_code == 200, res.text
        assert res.json()["status"] == "paused"
        assert res.json()["accepted"] is False

    async def test_execute_paused_agent_returns_200_with_paused_body(
        self, e2e_client, platform_admin, paused_agent
    ):
        """A paused agent yields HTTP 200 + structured paused body, not an error."""
        res = e2e_client.post(
            "/api/agent-runs/execute",
            json={"agent_name": paused_agent["name"], "input": {}},
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "paused"
        assert body["accepted"] is False
        assert "paused" in body["message"].lower()
        assert body["agent_id"] == paused_agent["id"]

    async def test_execute_paused_agent_does_not_create_run_row(
        self, e2e_client, platform_admin, paused_agent
    ):
        """Paused executes must not enqueue a run — no wasted history."""
        # Snapshot run count for this agent before
        before = e2e_client.get(
            f"/api/agent-runs?agent_id={paused_agent['id']}",
            headers=platform_admin.headers,
        )
        assert before.status_code == 200, before.text
        before_total = before.json()["total"]

        res = e2e_client.post(
            "/api/agent-runs/execute",
            json={"agent_name": paused_agent["name"], "input": {}},
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text

        after = e2e_client.get(
            f"/api/agent-runs?agent_id={paused_agent['id']}",
            headers=platform_admin.headers,
        )
        assert after.status_code == 200, after.text
        assert after.json()["total"] == before_total, (
            "Paused execute must not create an AgentRun row"
        )
