import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.execution import agent_run_service
from src.services.execution.agent_run_service import enqueue_agent_run


@pytest.fixture
def db_session(monkeypatch):
    session = AsyncMock()
    session.add = MagicMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=session_context)
    monkeypatch.setattr(
        agent_run_service,
        "get_session_factory",
        MagicMock(return_value=session_factory),
    )
    return session


def _redis_context(mock_get_redis):
    redis = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=redis)
    context.__aexit__ = AsyncMock(return_value=False)
    mock_get_redis.return_value = context
    return redis


class TestEnqueueAgentRun:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_persists_queued_run_before_publish(
        self, mock_get_redis, mock_publish, db_session
    ):
        _redis_context(mock_get_redis)
        calls = []
        db_session.commit.side_effect = lambda: calls.append("commit")
        mock_publish.side_effect = lambda *_: calls.append("publish")

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
        )

        queued_run = db_session.add.call_args.args[0]
        assert str(queued_run.id) == run_id
        assert queued_run.status == "queued"
        assert queued_run.input == {"ticket_id": 123}
        assert calls == ["commit", "publish"]
        assert mock_publish.call_args.args[0] == "agent-runs"

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_stores_context_in_redis(
        self, mock_get_redis, _mock_publish, db_session
    ):
        redis = _redis_context(mock_get_redis)
        org_id = str(uuid4())

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"task": "analyze"},
            output_schema={"action": {"type": "string"}},
            org_id=org_id,
            caller_user_id=str(uuid4()),
        )

        redis.set.assert_awaited_once()
        context = json.loads(redis.set.call_args.args[1])
        assert context["caller"]["organization_id"] == org_id

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_uses_provided_run_id(
        self, mock_get_redis, _mock_publish, db_session
    ):
        _redis_context(mock_get_redis)
        expected_run_id = str(uuid4())

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            run_id=expected_run_id,
        )

        assert run_id == expected_run_id

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_message_contains_sync_flag(
        self, mock_get_redis, mock_publish, db_session
    ):
        _redis_context(mock_get_redis)

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            sync=True,
        )

        assert mock_publish.call_args.args[1]["sync"] is True

    @pytest.mark.asyncio
    @patch(
        "src.services.execution.agent_run_service.publish_message",
        new_callable=AsyncMock,
    )
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_publish_failure_marks_durable_run_failed(
        self, mock_get_redis, mock_publish, db_session
    ):
        redis = _redis_context(mock_get_redis)
        mock_publish.side_effect = RuntimeError("queue unavailable")

        async def get_added_run(*_args, **_kwargs):
            return db_session.add.call_args.args[0]

        db_session.get.side_effect = get_added_run

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await enqueue_agent_run(
                agent_id=str(uuid4()),
                trigger_type="sdk",
            )

        failed_run = db_session.add.call_args.args[0]
        assert failed_run.status == "failed"
        assert failed_run.error == "Agent run could not be queued"
        assert failed_run.completed_at is not None
        redis.delete.assert_awaited_once()
