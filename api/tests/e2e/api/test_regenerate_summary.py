"""POST /api/agent-runs/{id}/regenerate-summary — e2e tests.

Validates the Task 18 behavior: admin-only auth, summary state reset to
``pending``, and a successful enqueue (response status='enqueued').
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_runs import AgentRun


logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def regen_test_agent(e2e_client, platform_admin) -> AsyncGenerator[dict, None]:
    """Create an agent for regenerate-summary tests."""
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Regen Test Agent {uuid4().hex[:8]}",
            "description": "test",
            "system_prompt": "test",
            "channels": [],
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
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")


@pytest_asyncio.fixture
async def failed_summary_run(
    regen_test_agent, db_session: AsyncSession
) -> AsyncGenerator[AgentRun, None]:
    """Insert a completed AgentRun with summary_status='failed'."""
    run = AgentRun(
        id=uuid4(),
        agent_id=UUID(regen_test_agent["id"]),
        trigger_type="api",
        status="completed",
        iterations_used=1,
        tokens_used=100,
        completed_at=datetime.now(timezone.utc),
        summary_status="failed",
        summary_error="Previously failed for testing",
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


class TestRegenerateSummary:
    async def test_regeneration_locks_run_before_staging_summary(
        self, monkeypatch, platform_admin
    ):
        """Recovery and regeneration serialize on the run before either stages work."""
        from src.routers import agent_runs as router_module

        run = AgentRun(id=uuid4(), trigger_type="api", status="completed")
        statements = []

        class Result:
            def scalar_one_or_none(self):
                return run

        class Session:
            async def execute(self, statement):
                statements.append(statement)
                return Result()

            async def commit(self):
                return None

        async def stage_after_lock(db, staged_run):
            assert db is session
            assert staged_run is run
            lock = statements[0]._for_update_arg
            assert lock is not None
            assert AgentRun.__table__ in lock.of
            return None

        session = Session()
        monkeypatch.setattr(router_module, "load_caller", AsyncMock(return_value=object()))
        monkeypatch.setattr(router_module, "require_operation", lambda *_: None)
        monkeypatch.setattr(router_module, "hand_off_summary", AsyncMock())
        monkeypatch.setattr(router_module, "request_summary", stage_after_lock)

        response = await router_module.regenerate_summary(run.id, session, platform_admin)

        assert response == {"status": "enqueued", "run_id": str(run.id)}

    async def test_admin_can_regenerate(
        self,
        e2e_client,
        platform_admin,
        failed_summary_run,
        db_session: AsyncSession,
    ):
        """Admin call returns 200, summary_status reset to 'pending', error cleared."""
        res = e2e_client.post(
            f"/api/agent-runs/{failed_summary_run.id}/regenerate-summary",
            headers=platform_admin.headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "enqueued"
        assert body["run_id"] == str(failed_summary_run.id)

        # Verify state reset happened. The endpoint sets status='pending'
        # and enqueues a job. The worker may pick it up and transition to
        # generating/done — or (in CI without a real LLM) run all the way
        # through to 'failed' again with a *different* error. We assert
        # the reset fired by comparing against the original sentinel error
        # rather than pinning an exact post-reset status.
        await db_session.refresh(failed_summary_run)
        refreshed = (
            await db_session.execute(
                select(AgentRun).where(AgentRun.id == failed_summary_run.id)
            )
        ).scalar_one()
        assert refreshed.summary_status in {"pending", "generating", "done", "failed"}
        assert refreshed.summary_error != "Previously failed for testing"

    async def test_non_admin_cannot_regenerate(
        self,
        e2e_client,
        org1_user,
        failed_summary_run,
    ):
        """Non-admin gets 403."""
        res = e2e_client.post(
            f"/api/agent-runs/{failed_summary_run.id}/regenerate-summary",
            headers=org1_user.headers,
        )
        assert res.status_code == 403, res.text

    async def test_unknown_run_returns_404(
        self, e2e_client, platform_admin
    ):
        """Admin call against a missing run returns 404."""
        res = e2e_client.post(
            f"/api/agent-runs/{uuid4()}/regenerate-summary",
            headers=platform_admin.headers,
        )
        assert res.status_code == 404, res.text
