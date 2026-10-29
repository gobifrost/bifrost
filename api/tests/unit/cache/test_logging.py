"""
Unit tests for the unified log streaming module.

Tests the Redis Stream-based logging that replaces the old sync Postgres approach.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from bifrost._logging import (
    LogFlushReceipt,
    append_log_to_stream,
    publish_log_to_pubsub,
    log_and_broadcast,
    append_log_to_stream_async,
    acknowledge_staged_logs,
    read_logs_from_stream,
    recover_terminal_execution_logs,
    flush_logs_to_postgres,
    close_thread_redis,
)


class TestAppendLogToStream:
    """Tests for sync append_log_to_stream function."""

    def test_append_log_to_stream_success(self):
        """Successfully appends log entry to Redis Stream."""
        exec_id = str(uuid4())

        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            entry_id = append_log_to_stream(
                execution_id=exec_id,
                level="INFO",
                message="Test log message",
                metadata={"key": "value"},
            )

            assert entry_id == "1234567890-0"
            mock_redis.xadd.assert_called_once()

            # Verify the call arguments
            call_args = mock_redis.xadd.call_args
            stream_key = call_args[0][0]
            entry = call_args[0][1]

            assert f"bifrost:logs:{exec_id}" == stream_key
            assert entry["execution_id"] == exec_id
            assert entry["level"] == "INFO"
            assert entry["message"] == "Test log message"
            assert json.loads(entry["metadata"]) == {"key": "value"}

    def test_append_log_to_stream_handles_uuid(self):
        """Handles UUID execution_id correctly."""
        exec_uuid = uuid4()

        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            entry_id = append_log_to_stream(
                execution_id=exec_uuid,
                level="ERROR",
                message="Error occurred",
            )

            assert entry_id is not None
            call_args = mock_redis.xadd.call_args
            entry = call_args[0][1]
            assert entry["execution_id"] == str(exec_uuid)

    def test_append_log_to_stream_normalizes_level(self):
        """Normalizes log level to uppercase."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            append_log_to_stream(
                execution_id="exec-123",
                level="warning",
                message="Warning message",
            )

            call_args = mock_redis.xadd.call_args
            entry = call_args[0][1]
            assert entry["level"] == "WARNING"

    def test_append_log_to_stream_handles_none_metadata(self):
        """Handles None metadata by serializing to empty object."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            append_log_to_stream(
                execution_id="exec-123",
                level="INFO",
                message="Message",
                metadata=None,
            )

            call_args = mock_redis.xadd.call_args
            entry = call_args[0][1]
            assert entry["metadata"] == "{}"

    def test_append_log_to_stream_returns_none_on_error(self):
        """Returns None and resets connection on error."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.side_effect = Exception("Connection failed")
            mock_get_redis.return_value = mock_redis

            with patch("bifrost._logging._local") as mock_local:
                mock_local.redis = mock_redis

                entry_id = append_log_to_stream(
                    execution_id="exec-123",
                    level="INFO",
                    message="Message",
                )

                assert entry_id is None


class TestPublishLogToPubsub:
    """Tests for sync publish_log_to_pubsub function."""

    def test_publish_log_to_pubsub_success(self):
        """Successfully publishes log to PubSub channel."""
        exec_id = str(uuid4())

        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_get_redis.return_value = mock_redis

            publish_log_to_pubsub(
                execution_id=exec_id,
                level="INFO",
                message="Test message",
                metadata={"key": "value"},
            )

            mock_redis.publish.assert_called_once()

            call_args = mock_redis.publish.call_args
            channel = call_args[0][0]
            message = json.loads(call_args[0][1])

            assert channel == f"bifrost:execution:{exec_id}"
            assert message["type"] == "execution_log"
            assert message["executionId"] == exec_id
            assert message["level"] == "INFO"
            assert message["message"] == "Test message"
            assert message["metadata"] == {"key": "value"}

    def test_publish_log_to_pubsub_handles_error(self):
        """Handles publish errors gracefully."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.publish.side_effect = Exception("Publish failed")
            mock_get_redis.return_value = mock_redis

            # Should not raise
            publish_log_to_pubsub(
                execution_id="exec-123",
                level="INFO",
                message="Message",
            )


class TestLogAndBroadcast:
    """Tests for combined log_and_broadcast function."""

    def test_log_and_broadcast_calls_both(self):
        """Calls both stream append and pubsub publish."""
        exec_id = str(uuid4())
        timestamp = datetime.now(timezone.utc)

        with patch("bifrost._logging.append_log_to_stream") as mock_append:
            with patch("bifrost._logging.publish_log_to_pubsub") as mock_publish:
                mock_append.return_value = "entry-id-123"

                entry_id = log_and_broadcast(
                    execution_id=exec_id,
                    level="INFO",
                    message="Test message",
                    metadata={"key": "value"},
                    timestamp=timestamp,
                )

                assert entry_id == "entry-id-123"

                # Both should be called with same timestamp
                mock_append.assert_called_once_with(
                    execution_id=exec_id,
                    level="INFO",
                    message="Test message",
                    metadata={"key": "value"},
                    timestamp=timestamp,
                )
                mock_publish.assert_called_once_with(
                    execution_id=exec_id,
                    level="INFO",
                    message="Test message",
                    metadata={"key": "value"},
                    timestamp=timestamp,
                    sequence=0,
                )

    def test_log_and_broadcast_uses_current_time_if_not_provided(self):
        """Uses current UTC time if timestamp not provided."""
        with patch("bifrost._logging.append_log_to_stream") as mock_append:
            with patch("bifrost._logging.publish_log_to_pubsub") as mock_publish:
                mock_append.return_value = "entry-id"

                log_and_broadcast(
                    execution_id="exec-123",
                    level="INFO",
                    message="Message",
                )

                # Both calls should have same timestamp
                append_ts = mock_append.call_args.kwargs.get("timestamp")
                publish_ts = mock_publish.call_args.kwargs.get("timestamp")

                assert append_ts == publish_ts
                assert append_ts is not None


class TestAsyncLogFunctions:
    """Tests for async log functions."""

    @pytest.mark.asyncio
    async def test_append_log_to_stream_async_success(self):
        """Async version successfully appends to stream."""
        exec_id = str(uuid4())

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xadd.return_value = "async-entry-id"
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            entry_id = await append_log_to_stream_async(
                execution_id=exec_id,
                level="INFO",
                message="Async log message",
            )

            assert entry_id == "async-entry-id"

    @pytest.mark.asyncio
    async def test_append_log_to_stream_async_handles_error(self):
        """Async version returns None on error."""
        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xadd.side_effect = Exception("Async error")
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            entry_id = await append_log_to_stream_async(
                execution_id="exec-123",
                level="INFO",
                message="Message",
            )

            assert entry_id is None


class TestPersistenceBoundary:
    """Execution children must not acquire Redis or database persistence paths."""

    @pytest.mark.asyncio
    async def test_persistence_and_recovery_entrypoints_require_server_process(self):
        receipt = LogFlushReceipt(
            execution_id=str(uuid4()),
            stream_key="bifrost:logs:test",
            entry_ids=("1-0",),
        )
        with patch(
            "bifrost._logging._require_server_process",
            side_effect=RuntimeError("Direct log storage access is unavailable in execution children"),
        ):
            with pytest.raises(RuntimeError, match="execution children"):
                await acknowledge_staged_logs(receipt)
            with pytest.raises(RuntimeError, match="execution children"):
                await flush_logs_to_postgres(receipt.execution_id)
            with pytest.raises(RuntimeError, match="execution children"):
                await recover_terminal_execution_logs()

    @pytest.mark.asyncio
    async def test_read_logs_from_stream_success(self):
        """Successfully reads logs from stream."""
        exec_id = str(uuid4())

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = [
                ("1234-0", {
                    "execution_id": exec_id,
                    "level": "INFO",
                    "message": "First log",
                    "metadata": "{}",
                    "timestamp": "2025-01-01T00:00:00",
                }),
                ("1234-1", {
                    "execution_id": exec_id,
                    "level": "ERROR",
                    "message": "Second log",
                    "metadata": '{"error": true}',
                    "timestamp": "2025-01-01T00:00:01",
                }),
            ]
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            logs = await read_logs_from_stream(exec_id)

            assert len(logs) == 2
            assert logs[0].id == "1234-0"
            assert logs[0].level == "INFO"
            assert logs[0].message == "First log"
            assert logs[1].level == "ERROR"
            assert logs[1].metadata == {"error": True}

    @pytest.mark.asyncio
    async def test_read_logs_from_stream_empty(self):
        """Returns empty list when no logs."""
        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = []
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            logs = await read_logs_from_stream("exec-123")

            assert logs == []


class TestFlushLogsToPostgres:
    """Tests for flush_logs_to_postgres function."""

    @pytest.mark.asyncio
    async def test_flush_logs_returns_zero_when_no_entries(self):
        """Returns 0 when stream is empty."""
        exec_id = str(uuid4())  # Use valid UUID

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = []
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            count = await flush_logs_to_postgres(exec_id)

            assert count == 0

    @pytest.mark.asyncio
    async def test_flush_logs_persists_entries(self):
        """The owned wrapper commits before it acknowledges its receipt."""
        exec_id = str(uuid4())
        receipt = LogFlushReceipt(exec_id, f"bifrost:logs:{exec_id}", ("1234-0", "1234-1"))
        mock_db = MagicMock()
        mock_db.commit = AsyncMock()
        with (
            patch("src.core.database.get_session_factory") as mock_session_factory,
            patch("bifrost._logging.stage_logs_to_postgres", AsyncMock(return_value=receipt)),
            patch("bifrost._logging.acknowledge_staged_logs", new_callable=AsyncMock) as acknowledge,
        ):
            mock_session_factory.return_value.return_value.__aenter__.return_value = mock_db
            count = await flush_logs_to_postgres(exec_id)

        assert count == 2
        mock_db.commit.assert_awaited_once()
        acknowledge.assert_awaited_once_with(receipt)


class TestCloseThreadRedis:
    """Tests for close_thread_redis function."""

    def test_close_thread_redis_with_connection(self):
        """Closes connection when one exists."""
        with patch("bifrost._logging._local") as mock_local:
            mock_redis = MagicMock()
            mock_local.redis = mock_redis

            close_thread_redis()

            mock_redis.close.assert_called_once()

    def test_close_thread_redis_without_connection(self):
        """Does nothing when no connection exists."""
        with patch("bifrost._logging._local") as mock_local:
            mock_local.redis = None

            # Should not raise
            close_thread_redis()
