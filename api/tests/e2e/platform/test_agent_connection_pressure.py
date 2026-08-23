"""E2E test: Concurrent agent runs under constrained DB pool.

Validates that the Redis-first pattern prevents DB connection exhaustion
when multiple agent runs execute concurrently. The worker container is
configured with pool_size=1, max_overflow=2 (3 max connections).

Requires:
- LLM API key configured (ANTHROPIC_API_TEST_KEY or OPENAPI_API_TEST_KEY)
- Full stack running (API + worker + Redis + Postgres + RabbitMQ)
"""

import concurrent.futures
import logging
import os
import time
from uuid import uuid4

import pytest

logger = logging.getLogger(__name__)

# Skip if no LLM key available
LLM_KEY = os.environ.get("ANTHROPIC_API_TEST_KEY") or os.environ.get("OPENAPI_API_TEST_KEY")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not LLM_KEY, reason="Requires LLM API key (ANTHROPIC_API_TEST_KEY or OPENAPI_API_TEST_KEY)"),
]


@pytest.fixture(scope="module")
def _llm_configured(e2e_client, platform_admin):
    """Configure LLM provider for the module (required for agent runs)."""
    api_key = os.environ.get("ANTHROPIC_API_TEST_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_TEST_KEY not configured")

    suffix = uuid4().hex[:8]
    response = e2e_client.post(
        "/api/admin/ai/connections",
        json={"name": f"Connection pressure {suffix}", "provider": "anthropic", "api_key": api_key},
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to configure provider: {response.text}"
    profile_response = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"Connection pressure {suffix}",
            "connection_id": response.json()["id"],
            "model": "claude-haiku-4-5-20251001",
            "enabled_for_chat": False,
        },
        headers=platform_admin.headers,
    )
    assert profile_response.status_code == 201, profile_response.text
    assignment_response = e2e_client.put(
        "/api/admin/ai/assignments/primary",
        json={"profile_id": profile_response.json()["id"]},
        headers=platform_admin.headers,
    )
    assert assignment_response.status_code == 200, assignment_response.text
    yield profile_response.json()


@pytest.fixture(scope="module")
def simple_agent(e2e_client, platform_admin, _llm_configured):
    """Create a minimal agent for connection pressure testing.

    Uses a trivial system prompt so the LLM responds quickly with no tool calls.
    """
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Connection Pressure Test Agent {int(time.time())}",
            "description": "Minimal agent for DB connection pressure testing",
            "system_prompt": (
                "You are a test agent. Reply with exactly one word: 'hello'. "
                "Do not use any tools. Do not elaborate."
            ),
            "channels": [],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to create agent: {response.text}"
    agent = response.json()
    logger.info(f"Created test agent: {agent['id']}")

    yield agent

    # Cleanup
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}",
            headers=platform_admin.headers,
        )
    except Exception as e:
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")


class TestConcurrentAgentRuns:
    """Test that concurrent agent runs work with a constrained DB pool."""

    def test_concurrent_agent_runs_succeed(
        self,
        e2e_client,
        platform_admin,
        simple_agent,
    ):
        """Fire multiple concurrent agent runs and verify all complete.

        With pool_size=1 and max_overflow=2, this would deadlock under
        the old pattern where each run held a session for its lifetime.
        """
        num_runs = 3
        timeout_per_run = 120  # seconds

        def trigger_run(i: int) -> dict:
            """Trigger a single agent run synchronously."""
            resp = e2e_client.post(
                "/api/agent-runs/execute",
                json={
                    "agent_name": simple_agent["name"],
                    "input": {"task": f"Test run {i}"},
                    "timeout": timeout_per_run,
                },
                headers=platform_admin.headers,
                timeout=timeout_per_run + 10,
            )
            return {"index": i, "status_code": resp.status_code, "body": resp.json() if resp.status_code == 200 else resp.text}

        # Fire runs concurrently using threads (httpx client is thread-safe)
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_runs) as pool:
            futures = [pool.submit(trigger_run, i) for i in range(num_runs)]
            for f in concurrent.futures.as_completed(futures, timeout=timeout_per_run + 30):
                results.append(f.result())

        # All should have succeeded
        for r in results:
            assert r["status_code"] == 200, (
                f"Run {r['index']} failed with status {r['status_code']}: {r['body']}"
            )
            body = r["body"]
            assert body.get("status") in ("completed", "budget_exceeded"), (
                f"Run {r['index']} unexpected status: {body.get('status')}"
            )

        logger.info(f"All {num_runs} concurrent runs completed successfully")

    def test_agent_run_steps_persisted(
        self,
        e2e_client,
        platform_admin,
        simple_agent,
    ):
        """Verify steps are flushed to Postgres after run completion.

        The Redis-first pattern defers step writes. This test confirms
        they end up in Postgres and are queryable via the API.
        """
        # Run a single agent
        resp = e2e_client.post(
            "/api/agent-runs/execute",
            json={
                "agent_name": simple_agent["name"],
                "input": {"task": "Say hello for the steps test"},
                "timeout": 120,
            },
            headers=platform_admin.headers,
            timeout=130,
        )
        assert resp.status_code == 200, f"Agent run failed: {resp.text}"

        # Get the run ID from recent runs
        runs_resp = e2e_client.get(
            "/api/agent-runs",
            params={"agent_id": simple_agent["id"], "limit": 1},
            headers=platform_admin.headers,
        )
        assert runs_resp.status_code == 200
        runs_data = runs_resp.json()
        runs = runs_data["items"]
        assert len(runs) > 0, "No agent runs found"

        run_id = runs[0]["id"]

        # Fetch steps embedded in the run response
        run_resp = e2e_client.get(
            f"/api/agent-runs/{run_id}",
            headers=platform_admin.headers,
        )
        assert run_resp.status_code == 200
        steps = run_resp.json()["steps"]

        # Should have at least: llm_request + llm_response
        assert len(steps) >= 2, (
            f"Expected at least 2 steps, got {len(steps)}: "
            f"{[s.get('type') for s in steps]}"
        )

        step_types = [s["type"] for s in steps]
        assert "llm_request" in step_types
        assert "llm_response" in step_types

        logger.info(f"Run {run_id} has {len(steps)} steps persisted to Postgres")
