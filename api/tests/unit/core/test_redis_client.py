"""
Unit tests for Redis client for sync execution results.

Tests the BLPOP/RPUSH pattern for synchronous workflow execution.
"""

import pytest
import json
from decimal import Decimal
from unittest.mock import AsyncMock


class TestRedisClient:
    """Tests for RedisClient."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis instance."""
        redis = AsyncMock()
        redis.rpush = AsyncMock()
        redis.expire = AsyncMock()
        redis.blpop = AsyncMock()
        redis.setex = AsyncMock()
        redis.aclose = AsyncMock()
        return redis

    async def test_push_result_success(self, mock_redis):
        """Test pushing execution result to Redis."""
        from src.core.redis_client import RedisClient, RESULT_KEY_PREFIX, RESULT_TTL_SECONDS

        client = RedisClient()
        client._redis = mock_redis

        await client.push_result(
            execution_id="exec-123",
            status="Success",
            result={"data": "test"},
            duration_ms=150,
        )

        expected_key = f"{RESULT_KEY_PREFIX}exec-123"
        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args
        assert call_args[0][0] == expected_key

        # Verify payload
        payload = json.loads(call_args[0][1])
        assert payload["status"] == "Success"
        assert payload["result"] == {"data": "test"}
        assert payload["duration_ms"] == 150

        # Verify TTL was set
        mock_redis.expire.assert_called_once_with(expected_key, RESULT_TTL_SECONDS)

    async def test_push_result_with_error(self, mock_redis):
        """Test pushing error result to Redis."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.push_result(
            execution_id="exec-456",
            status="Failed",
            error="Something went wrong",
            error_type="RuntimeError",
            duration_ms=50,
        )

        call_args = mock_redis.rpush.call_args
        payload = json.loads(call_args[0][1])
        assert payload["status"] == "Failed"
        assert payload["error"] == "Something went wrong"
        assert payload["error_type"] == "RuntimeError"

    async def test_wait_for_result_success(self, mock_redis):
        """Test waiting for execution result from Redis."""
        from src.core.redis_client import RedisClient, RESULT_KEY_PREFIX

        expected_result = {"status": "Success", "result": {"data": "test"}}
        mock_redis.blpop.return_value = (
            f"{RESULT_KEY_PREFIX}exec-789",
            json.dumps(expected_result),
        )

        client = RedisClient()
        client._redis = mock_redis

        result = await client.wait_for_result(
            execution_id="exec-789",
            timeout_seconds=30,
        )

        assert result == expected_result
        mock_redis.blpop.assert_called_once()

    async def test_wait_for_result_timeout(self, mock_redis):
        """Test timeout when waiting for result."""
        from src.core.redis_client import RedisClient

        mock_redis.blpop.return_value = None  # Timeout

        client = RedisClient()
        client._redis = mock_redis

        result = await client.wait_for_result(
            execution_id="exec-timeout",
            timeout_seconds=5,
        )

        assert result is None

    async def test_close(self, mock_redis):
        """Test closing Redis connection."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.close()

        mock_redis.aclose.assert_called_once()
        assert client._redis is None

    async def test_set_workflow_metadata_cache_serializes_decimal(self, mock_redis):
        """Workflow metadata cache should coerce Decimal values to JSON numbers."""
        from src.core.redis_client import RedisClient, WORKFLOW_METADATA_CACHE_PREFIX

        client = RedisClient()
        client._redis = mock_redis

        await client.set_workflow_metadata_cache(
            workflow_id="wf-123",
            name="Test Workflow",
            file_path="features/test/workflows/example.py",
            timeout_seconds=300,
            time_saved=15,
            value=Decimal("42.50"),
            execution_mode="sync",
        )

        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == f"{WORKFLOW_METADATA_CACHE_PREFIX}wf-123"
        payload = json.loads(call_args[0][2])
        assert payload["value"] == 42.5


class TestRedisClientSingleton:
    """Tests for Redis client singleton functions."""

    def test_get_redis_client_returns_singleton(self):
        """Test that get_redis_client returns same instance."""
        from src.core.redis_client import get_redis_client

        # Reset singleton
        import src.core.redis_client as module
        module._redis_client = None

        client1 = get_redis_client()
        client2 = get_redis_client()

        assert client1 is client2

    async def test_close_redis_client(self):
        """Test closing singleton Redis client."""
        from src.core.redis_client import close_redis_client, get_redis_client
        import src.core.redis_client as module

        # Reset singleton
        module._redis_client = None

        get_redis_client()  # Creates singleton
        assert module._redis_client is not None

        # Close without connecting
        await close_redis_client()
        assert module._redis_client is None
