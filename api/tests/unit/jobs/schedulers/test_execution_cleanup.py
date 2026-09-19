"""Regression coverage for the scheduler execution cleanup job.

The job now cleans up both workflow executions and stale AgentRun rows.
These tests pin the agent-run side so the cleanup remains generic and
does not interfere with active runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.jobs.schedulers import execution_cleanup as cleanup
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Conversation

pytestmark = pytest.mark.asyncio


def _stale_time(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _fresh_time(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


async def _seed_agent_runs(db_session, agent, *, stale_minutes: int = 10) -> tuple[AgentRun, AgentRun, AgentRun]:
    agent.max_run_timeout = 60
    agent.updated_at = datetime.now(timezone.utc)

    queued = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="queued",
        iterations_used=0,
        tokens_used=0,
        created_at=_stale_time(stale_minutes),
    )
    running = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="running",
        iterations_used=1,
        tokens_used=25,
        created_at=_stale_time(stale_minutes),
        started_at=_stale_time(stale_minutes),
    )
    fresh_running = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="running",
        iterations_used=1,
        tokens_used=25,
        created_at=_fresh_time(2),
        started_at=_fresh_time(2),
    )

    db_session.add_all([agent, queued, running, fresh_running])
    await db_session.commit()
    return queued, running, fresh_running


def _patch_cleanup_dependencies(monkeypatch, async_session_factory):
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(cleanup, "publish_execution_update", AsyncMock())
    monkeypatch.setattr(cleanup, "publish_history_update", AsyncMock())
    monkeypatch.setattr(cleanup, "publish_agent_run_update", AsyncMock())
    monkeypatch.setattr(cleanup, "publish_chat_run_event", AsyncMock())
    monkeypatch.setattr(cleanup, "publish_message", AsyncMock())


async def _load_run(async_session_factory, run_id):
    async with async_session_factory() as db_session:
        result = await db_session.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one()


def _agent_run_updates(mock_publish):
    return [(call.args[0].id, call.args[0].status) for call in mock_publish.await_args_list]


class TestExecutionCleanupAgentRuns:
    async def test_cleanup_stale_agent_runs_terminalizes_and_broadcasts(
        self,
        db_session,
        async_session_factory,
        seed_agent,
        monkeypatch,
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        queued, running, fresh_running = await _seed_agent_runs(db_session, seed_agent)

        results = await cleanup.cleanup_stuck_executions()

        assert results["agent_run_queued_timeouts"] == 1
        assert results["agent_run_running_timeouts"] == 1
        assert results["agent_run_total_cleaned"] == 2

        queued_reloaded = await _load_run(async_session_factory, queued.id)
        running_reloaded = await _load_run(async_session_factory, running.id)
        fresh_reloaded = await _load_run(async_session_factory, fresh_running.id)

        assert queued_reloaded.status == "failed"
        assert queued_reloaded.completed_at is not None
        assert "waiting in queue" in queued_reloaded.error

        assert running_reloaded.status == "timeout"
        assert running_reloaded.completed_at is not None
        assert "timed out after 360 seconds" in running_reloaded.error

        assert fresh_reloaded.status == "running"
        assert fresh_reloaded.completed_at is None

        assert cleanup.publish_agent_run_update.await_count == 2
        assert set(_agent_run_updates(cleanup.publish_agent_run_update)) == {
            (queued.id, "failed"),
            (running.id, "timeout"),
        }

    async def test_cleanup_is_idempotent_and_skips_active_runs(
        self,
        db_session,
        async_session_factory,
        seed_agent,
        monkeypatch,
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        queued, running, fresh_running = await _seed_agent_runs(db_session, seed_agent)

        first = await cleanup.cleanup_stuck_executions()
        queued_first = await _load_run(async_session_factory, queued.id)
        running_first = await _load_run(async_session_factory, running.id)
        fresh_first = await _load_run(async_session_factory, fresh_running.id)

        second = await cleanup.cleanup_stuck_executions()
        queued_second = await _load_run(async_session_factory, queued.id)
        running_second = await _load_run(async_session_factory, running.id)
        fresh_second = await _load_run(async_session_factory, fresh_running.id)

        assert first["agent_run_total_cleaned"] == 2
        assert second["agent_run_total_cleaned"] == 0
        assert queued_first.status == queued_second.status == "failed"
        assert running_first.status == running_second.status == "timeout"
        assert fresh_first.status == fresh_second.status == "running"
        assert fresh_second.completed_at is None

    async def test_cleanup_terminalizes_agentless_chat_and_publishes_terminal_event(
        self,
        db_session,
        async_session_factory,
        seed_user,
        monkeypatch,
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        conversation = Conversation(
            id=uuid4(),
            user_id=seed_user.id,
            title="Stale chat",
        )
        run = AgentRun(
            id=uuid4(),
            agent_id=None,
            conversation_id=conversation.id,
            trigger_type="chat",
            status="running",
            iterations_used=0,
            tokens_used=0,
            created_at=_stale_time(40),
            started_at=_stale_time(40),
        )
        db_session.add_all([conversation, run])
        await db_session.commit()

        results = await cleanup.cleanup_stuck_executions()

        assert results["agent_run_running_timeouts"] == 1
        assert results["agent_run_total_cleaned"] == 1
        reloaded = await _load_run(async_session_factory, run.id)
        assert reloaded.status == "timeout"
        assert reloaded.completed_at is not None
        cleanup.publish_agent_run_update.assert_awaited_once()
        cleanup.publish_chat_run_event.assert_awaited_once()
        kwargs = cleanup.publish_chat_run_event.await_args.kwargs
        assert kwargs["conversation_id"] == conversation.id
        assert kwargs["run_id"] == str(run.id)
        assert kwargs["kind"] == "error"
        assert kwargs["status"] == "timeout"
        assert kwargs["payload"].run_status == "timeout"


class TestExecutionCleanupLeaseRecovery:
    async def _delete_run(self, async_session_factory, run_id):
        from sqlalchemy import delete as sa_delete

        async with async_session_factory() as session:
            await session.execute(
                sa_delete(AgentRun).where(AgentRun.id == run_id)
            )
            await session.commit()

    def _published_ids(self):
        return [
            call.args[1]["run_id"]
            for call in cleanup.publish_message.await_args_list
        ]

    async def test_live_lease_is_untouched(
        self, db_session, async_session_factory, seed_agent, monkeypatch
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        now = datetime.now(timezone.utc)
        seed_agent.max_run_timeout = 60
        run = AgentRun(
            id=uuid4(),
            agent_id=seed_agent.id,
            trigger_type="api",
            status="running",
            iterations_used=1,
            tokens_used=25,
            created_at=now - timedelta(minutes=40),
            started_at=now - timedelta(minutes=40),
            lease_owner="worker-a",
            lease_token="live-token",
            lease_expires_at=now + timedelta(minutes=2),
        )
        db_session.add(run)
        await db_session.commit()

        try:
            await cleanup.cleanup_stuck_executions()

            reloaded = await _load_run(async_session_factory, run.id)
            assert reloaded.status == "running"
            assert reloaded.lease_token == "live-token"
            assert str(run.id) not in self._published_ids()
        finally:
            await self._delete_run(async_session_factory, run.id)

    async def test_expired_lease_within_safety_age_is_requeued_not_terminalized(
        self, db_session, async_session_factory, seed_agent, monkeypatch
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        now = datetime.now(timezone.utc)
        seed_agent.max_run_timeout = 1800
        run = AgentRun(
            id=uuid4(),
            agent_id=seed_agent.id,
            trigger_type="api",
            status="running",
            iterations_used=1,
            tokens_used=25,
            created_at=now - timedelta(minutes=2),
            started_at=now - timedelta(minutes=2),
            lease_owner="dead-worker",
            lease_token="expired-token",
            lease_expires_at=now - timedelta(seconds=10),
        )
        db_session.add(run)
        await db_session.commit()

        try:
            await cleanup.cleanup_stuck_executions()

            reloaded = await _load_run(async_session_factory, run.id)
            assert reloaded.status == "running"
            assert reloaded.completed_at is None
            assert str(run.id) in self._published_ids()
        finally:
            await self._delete_run(async_session_factory, run.id)

    async def test_due_sleeping_run_is_woken_and_republished(
        self, db_session, async_session_factory, seed_agent, monkeypatch
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        now = datetime.now(timezone.utc)
        run = AgentRun(
            id=uuid4(),
            agent_id=seed_agent.id,
            trigger_type="api",
            status="sleeping",
            iterations_used=1,
            tokens_used=25,
            created_at=now - timedelta(minutes=10),
            started_at=now - timedelta(minutes=10),
            wake_at=now - timedelta(seconds=5),
        )
        db_session.add(run)
        await db_session.commit()

        try:
            results = await cleanup.cleanup_stuck_executions()

            reloaded = await _load_run(async_session_factory, run.id)
            assert reloaded.status == "running"
            assert reloaded.wake_at is None
            assert results["agent_run_woken"] >= 1
            assert str(run.id) in self._published_ids()
        finally:
            await self._delete_run(async_session_factory, run.id)

    async def test_future_sleeping_run_is_left_alone(
        self, db_session, async_session_factory, seed_agent, monkeypatch
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        now = datetime.now(timezone.utc)
        run = AgentRun(
            id=uuid4(),
            agent_id=seed_agent.id,
            trigger_type="api",
            status="sleeping",
            iterations_used=1,
            tokens_used=25,
            created_at=now - timedelta(minutes=10),
            started_at=now - timedelta(minutes=10),
            wake_at=now + timedelta(hours=1),
        )
        db_session.add(run)
        await db_session.commit()

        try:
            await cleanup.cleanup_stuck_executions()

            reloaded = await _load_run(async_session_factory, run.id)
            assert reloaded.status == "sleeping"
            assert str(run.id) not in self._published_ids()
        finally:
            await self._delete_run(async_session_factory, run.id)
