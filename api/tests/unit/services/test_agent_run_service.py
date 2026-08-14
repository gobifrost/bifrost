import json
import logging
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.execution.agent_run_service import (
    enqueue_agent_run,
    get_pending_agent_run_context,
)


class TestEnqueueAgentRun:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_returns_run_id(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        run_id = await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="event",
            input_data={"ticket_id": 123},
        )

        assert run_id is not None
        mock_publish.assert_called_once()

        # Verify queue name
        call_args = mock_publish.call_args
        assert call_args[0][0] == "agent-runs"

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_stores_context_in_redis(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        org_id = str(uuid4())
        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            input_data={"task": "analyze"},
            output_schema={"action": {"type": "string"}},
            org_id=org_id,
            caller_user_id=str(uuid4()),
        )

        mock_redis.set.assert_called_once()
        context = json.loads(mock_redis.set.call_args.args[1])
        assert context["caller"]["organization_id"] == org_id

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.publish_message")
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_enqueue_uses_provided_run_id(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

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
    async def test_enqueue_message_contains_sync_flag(self, mock_get_redis, mock_publish):
        mock_redis = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        await enqueue_agent_run(
            agent_id=str(uuid4()),
            trigger_type="sdk",
            sync=True,
        )

        call_args = mock_publish.call_args
        message = call_args[0][1]
        assert message["sync"] is True


class TestGetPendingAgentRunContext:
    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_returns_pending_context(self, mock_get_redis):
        expected = {"agent_id": str(uuid4()), "caller": {"user_id": str(uuid4())}}
        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(expected)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        result = await get_pending_agent_run_context(str(uuid4()))

        assert result == expected

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_returns_none_for_invalid_context(self, mock_get_redis):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = "not-json"
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        result = await get_pending_agent_run_context(str(uuid4()))

        assert result is None

    @pytest.mark.asyncio
    @patch("src.services.execution.agent_run_service.get_redis")
    async def test_invalid_context_sanitizes_run_id_in_warning(self, mock_get_redis, caplog):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = "not-json"
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get_redis.return_value = mock_ctx

        with caplog.at_level(logging.WARNING):
            result = await get_pending_agent_run_context("bad\nforged")

        assert result is None
        assert caplog.messages == ["Invalid pending agent-run context for bad\\nforged"]
