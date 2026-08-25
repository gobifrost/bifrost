"""Unit tests for AgentRunConsumer error handling paths."""

import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from src.jobs.consumers.agent_run import AgentRunConsumer
from src.models.orm.agent_runs import AgentRun


class FakeRedisCtx:
    """Async context manager that yields a mock redis client."""

    def __init__(self, redis_mock):
        self._redis = redis_mock

    async def __aenter__(self):
        return self._redis

    async def __aexit__(self, *args):
        pass


class FakeLateExecutor:
    """Executor stub that simulates a late terminalizer racing the consumer."""

    def __init__(self, session_factory, redis_client):
        self._session_factory = session_factory
        self._redis = redis_client

    async def run(self, *, run_id, **kwargs):
        async with self._session_factory() as db:
            run_obj = await db.get(AgentRun, UUID(run_id))
            run_obj.status = "timeout"
            run_obj.error = "scheduler terminalized the run"
            run_obj.completed_at = datetime.now(timezone.utc)
            await db.commit()

        return {
            "output": {"text": "late consumer result"},
            "iterations_used": 9,
            "tokens_used": 27,
            "status": "completed",
            "llm_model": "test-model",
        }

    async def flush_to_db(self, db):
        return None


async def _load_run(async_session_factory, run_id):
    async with async_session_factory() as db:
        result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one()


@pytest.fixture
def consumer():
    with (
        patch("src.jobs.consumers.agent_run.get_settings") as mock_settings,
        patch("src.jobs.consumers.agent_run.get_session_factory"),
        patch("src.jobs.consumers.agent_run.BaseConsumer.__init__", return_value=None),
    ):
        mock_settings.return_value = MagicMock(max_concurrency=2)
        c = AgentRunConsumer()
        return c


@pytest.mark.asyncio
async def test_missing_redis_context_returns_early(consumer):
    """Missing Redis context durably fails the queued run."""
    queued_run = MagicMock(status="queued")
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = None

    with patch(
        "src.jobs.consumers.agent_run.get_redis",
        return_value=FakeRedisCtx(redis_mock),
    ):
        await consumer.process_message(
            {
                "run_id": str(uuid4()),
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

    redis_mock.get.assert_called_once()
    assert queued_run.status == "failed"
    assert queued_run.error == "Agent run context was unavailable"
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_not_found_returns_early(consumer):
    """When the agent doesn't exist in the DB, process_message logs and returns without crashing."""
    run_id = str(uuid4())

    # Redis returns valid context
    redis_mock = AsyncMock()
    redis_mock.get.return_value = json.dumps({"org_id": str(uuid4()), "input": "hello"})

    queued_run = MagicMock(status="queued")

    # DB session where the durable run exists but the agent no longer does.
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.execute.return_value = mock_result

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    # get_redis is called multiple times (initial context read, then inside finally block)
    # We need it to work for both calls
    with patch(
        "src.jobs.consumers.agent_run.get_redis",
        return_value=FakeRedisCtx(redis_mock),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

    # Verify the agent query was executed
    mock_session.execute.assert_called_once()
    assert queued_run.status == "failed"
    assert queued_run.error == "Agent no longer exists"


@pytest.mark.asyncio
async def test_pre_cancel_updates_existing_queued_run(consumer):
    run_id = str(uuid4())
    queued_run = MagicMock(status="queued")
    mock_session = AsyncMock()
    mock_session.get.return_value = queued_run
    mock_session.add = MagicMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    consumer._session_factory = MagicMock(return_value=mock_session_ctx)

    redis_mock = AsyncMock()
    redis_mock.get.return_value = json.dumps({"cancelled": True})

    with patch(
        "src.jobs.consumers.agent_run.get_redis",
        return_value=FakeRedisCtx(redis_mock),
    ):
        await consumer.process_message(
            {
                "run_id": run_id,
                "agent_id": str(uuid4()),
                "trigger_type": "manual",
            }
        )

    assert queued_run.status == "cancelled"
    assert queued_run.completed_at is not None
    mock_session.add.assert_not_called()
    _, get_kwargs = mock_session.get.call_args
    assert get_kwargs["with_for_update"] == {"of": AgentRun}


@pytest.mark.asyncio
async def test_late_terminalized_run_is_not_overwritten(
    consumer,
    db_session,
    async_session_factory,
    seed_agent,
):
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        agent_id=seed_agent.id,
        trigger_type="manual",
        status="queued",
        iterations_used=0,
        tokens_used=0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()

    consumer._session_factory = async_session_factory

    redis_mock = AsyncMock()
    context_key = f"bifrost:agent_run:{run_id}:context"
    cancel_key = f"bifrost:agent_run:{run_id}:cancel"

    async def _redis_get(key):
        if key == context_key:
            return json.dumps({"org_id": str(uuid4()), "input": "hello"})
        if key == cancel_key:
            return None
        return None

    redis_mock.get.side_effect = _redis_get

    with (
        patch("src.jobs.consumers.agent_run.get_redis", return_value=FakeRedisCtx(redis_mock)),
        patch(
            "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor",
            FakeLateExecutor,
        ),
        patch("src.jobs.consumers.agent_run.publish_agent_run_update", AsyncMock()) as publish_mock,
        patch("src.jobs.consumers.agent_run._publish_sync_result", AsyncMock()) as sync_mock,
    ):
        await consumer.process_message(
            {
                "run_id": str(run_id),
                "agent_id": str(seed_agent.id),
                "trigger_type": "manual",
                "sync": True,
            }
        )

    refreshed = await _load_run(async_session_factory, run_id)
    assert refreshed.status == "timeout"
    assert refreshed.error == "scheduler terminalized the run"
    assert refreshed.completed_at is not None
    assert publish_mock.await_count == 2
    assert publish_mock.await_args_list[0].args[0].status == "running"
    assert publish_mock.await_args_list[1].args[0].status == "timeout"
    sync_payload = sync_mock.await_args.args[1]
    assert sync_payload["status"] == "timeout"
    assert sync_payload["error"] == "scheduler terminalized the run"
