"""Unit tests for AgentRunConsumer error handling paths."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

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
