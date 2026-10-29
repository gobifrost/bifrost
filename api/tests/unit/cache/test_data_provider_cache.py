"""Unit tests for data provider Redis cache."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.core.cache.data_provider_cache import (
    TTL_DATA_PROVIDER,
    cache_result,
    compute_param_hash,
    data_provider_cache_key,
    get_cached_result,
    invalidate_data_provider,
)


class TestKeyGeneration:
    """Tests for cache key generation functions."""

    def test_data_provider_cache_key_partitions_by_workflow_and_authority(self):
        """A result is never shared across workflow or authority partitions."""
        key = data_provider_cache_key("org-123", "workflow-123", "authority-a", "abc123")
        assert key == "bifrost:v2:org:org-123:dp:workflow-123:authority-a:abc123"
        assert key != data_provider_cache_key("org-123", "workflow-123", "authority-b", "abc123")
        assert key != data_provider_cache_key("org-123", "workflow-456", "authority-a", "abc123")

    def test_data_provider_cache_key_global(self):
        """Key uses global scope when org_id is None."""
        key = data_provider_cache_key(None, "workflow-123", "authority-a", "abc123")
        assert key == "bifrost:v2:global:dp:workflow-123:authority-a:abc123"

    def test_data_provider_cache_key_global_string(self):
        """Key uses global scope when org_id is 'GLOBAL'."""
        key = data_provider_cache_key("GLOBAL", "workflow-123", "authority-a", "abc123")
        assert key == "bifrost:v2:global:dp:workflow-123:authority-a:abc123"


class TestComputeParamHash:
    """Tests for parameter hashing."""

    def test_empty_params_returns_empty(self):
        """Empty or None params return 'empty' hash."""
        assert compute_param_hash(None) == "empty"
        assert compute_param_hash({}) == "empty"

    def test_deterministic_hash(self):
        """Same params produce same hash."""
        params = {"user_id": "123", "limit": 10}
        hash1 = compute_param_hash(params)
        hash2 = compute_param_hash(params)
        assert hash1 == hash2

    def test_order_independent(self):
        """Parameter order doesn't affect hash."""
        params1 = {"a": 1, "b": 2}
        params2 = {"b": 2, "a": 1}
        assert compute_param_hash(params1) == compute_param_hash(params2)

    def test_different_params_different_hash(self):
        """Different params produce different hashes."""
        hash1 = compute_param_hash({"a": 1})
        hash2 = compute_param_hash({"a": 2})
        assert hash1 != hash2

    def test_hash_length(self):
        """Hash is 16 characters."""
        hash_val = compute_param_hash({"key": "value"})
        assert len(hash_val) == 16


class TestGetCachedResult:
    """Tests for cache retrieval."""

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """Returns None when key not in cache."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_cached_result("org-123", "workflow-123", "authority-a", {"id": 1})
            assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_returns_entry(self):
        """Returns cached entry when found and not expired."""
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        cached_data = {
            "data": {"users": [1, 2, 3]},
            "expires_at": expires_at
        }

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_cached_result("org-123", "workflow-123", "authority-a", {"id": 1})
            assert result is not None
            assert result["data"] == {"users": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_expired_cache_returns_none(self):
        """Returns None and deletes expired entries."""
        expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        cached_data = {
            "data": {"users": [1, 2, 3]},
            "expires_at": expires_at
        }

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))
        mock_redis.delete = AsyncMock()

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_cached_result("org-123", "workflow-123", "authority-a", {"id": 1})
            assert result is None
            mock_redis.delete.assert_called_once()


class TestCacheResult:
    """Tests for caching results."""

    @pytest.mark.asyncio
    async def test_cache_result_stores_with_ttl(self):
        """Stores result with correct TTL."""
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            result = {"users": [1, 2, 3]}
            expires_at = await cache_result("org-123", "workflow-123", "authority-a", {"id": 1}, result, 300)

            mock_redis.setex.assert_called_once()
            call_args = mock_redis.setex.call_args
            assert call_args[0][1] == 300  # TTL

            # Verify expiration is in the future
            assert expires_at > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_cache_result_uses_default_ttl(self):
        """Uses default TTL when not specified."""
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            await cache_result("org-123", "workflow-123", "authority-a", None, {"data": 1})

            call_args = mock_redis.setex.call_args
            assert call_args[0][1] == TTL_DATA_PROVIDER


class TestInvalidateDataProvider:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_invalidate_specific_params(self):
        """Invalidates specific parameter combination."""
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, [
            "bifrost:v2:org:org-123:dp:workflow-123:authority-a:abc123",
        ]))
        mock_redis.delete = AsyncMock(return_value=1)

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            await invalidate_data_provider("org-123", "workflow-123", {"id": 1})
            mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_all_for_provider(self):
        """Invalidates all cached results for a data provider."""
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock(return_value=(0, [
            "bifrost:v2:org:org-123:dp:workflow-123:authority-a:abc123",
            "bifrost:v2:org:org-123:dp:workflow-123:authority-b:def456",
        ]))
        mock_redis.delete = AsyncMock(return_value=2)

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            await invalidate_data_provider("org-123", "workflow-123", None)
            mock_redis.scan.assert_called()
            mock_redis.delete.assert_called()
