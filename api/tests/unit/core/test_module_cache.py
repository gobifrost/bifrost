"""
Unit tests for Redis module cache.

Tests both async (module_cache.py) and sync (module_cache_sync.py) cache operations.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestModuleCacheAsync:
    """Tests for async module cache functions."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock async Redis client."""
        mock_client = AsyncMock()
        mock_redis = AsyncMock()
        mock_client._get_redis = AsyncMock(return_value=mock_redis)
        mock_client.get = AsyncMock()
        mock_client.setex = AsyncMock()
        mock_client.delete = AsyncMock()
        return mock_client, mock_redis

    async def test_get_module_found(self, mock_redis_client):
        """Test fetching a module that exists in cache."""
        mock_client, _ = mock_redis_client
        cached_data = {"content": "print('hello')", "path": "shared/test.py", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached_data)

        with patch("src.core.module_cache.get_redis_client", return_value=mock_client):
            from src.core.module_cache import get_module

            result = await get_module("shared/test.py")

            assert result is not None
            assert result["content"] == "print('hello')"
            assert result["path"] == "shared/test.py"
            assert result["hash"] == "abc123"
            mock_client.get.assert_called_once_with("bifrost:module:shared/test.py")

    async def test_get_module_not_found(self, mock_redis_client):
        """Test fetching a module that doesn't exist in cache or S3."""
        mock_client, _ = mock_redis_client
        mock_client.get.return_value = None

        mock_repo = AsyncMock()
        mock_repo.read.side_effect = Exception("NoSuchKey")

        with (
            patch("src.core.module_cache.get_redis_client", return_value=mock_client),
            patch("src.core.module_cache.RepoStorage", return_value=mock_repo),
        ):
            from src.core.module_cache import get_module

            result = await get_module("nonexistent/module.py")

            assert result is None

    async def test_get_module_falls_back_to_s3(self, mock_redis_client):
        """When Redis misses, get_module should fall back to S3 and re-cache."""
        mock_client, mock_redis = mock_redis_client
        mock_client.get.return_value = None  # Redis miss

        mock_repo = AsyncMock()
        mock_repo.read.return_value = b"print('from s3')"

        with (
            patch("src.core.module_cache.get_redis_client", return_value=mock_client),
            patch("src.core.module_cache.RepoStorage", return_value=mock_repo),
        ):
            from src.core.module_cache import get_module

            result = await get_module("shared/test.py")

            assert result is not None
            assert result["content"] == "print('from s3')"
            assert result["path"] == "shared/test.py"
            assert result["hash"]  # SHA-256 hash present
            # Verify re-cached to Redis
            mock_client.setex.assert_called_once()

    async def test_get_module_falls_back_to_solution_storage_for_solution_module(self, mock_redis_client):
        """Cold solution module lookups read from _solutions storage and re-cache."""
        mock_client, mock_redis = mock_redis_client
        mock_client.get.return_value = None

        solution_id = "12345678-1234-5678-1234-567812345678"
        storage_path = f"_solutions/{solution_id}/workflows/triage.py"

        mock_repo = AsyncMock()
        mock_repo.read.side_effect = AssertionError("solution modules must not use RepoStorage")
        mock_solution_storage = AsyncMock()
        mock_solution_storage.read.return_value = b"print('from solution s3')"

        with (
            patch("src.core.module_cache.get_redis_client", return_value=mock_client),
            patch("src.core.module_cache.RepoStorage", return_value=mock_repo),
            patch("src.core.module_cache.SolutionStorage", return_value=mock_solution_storage) as solution_storage_cls,
        ):
            from src.core.module_cache import get_module

            result = await get_module(storage_path)

            assert result is not None
            assert result["content"] == "print('from solution s3')"
            assert result["path"] == storage_path
            assert result["hash"]
            solution_storage_cls.assert_called_once_with(solution_id)
            mock_solution_storage.read.assert_called_once_with("workflows/triage.py")
            mock_client.setex.assert_called_once()
            mock_redis.sadd.assert_called_once_with("bifrost:module:index", storage_path)

    async def test_get_module_s3_not_found(self, mock_redis_client):
        """When both Redis and S3 miss, get_module returns None."""
        mock_client, _ = mock_redis_client
        mock_client.get.return_value = None

        mock_repo = AsyncMock()
        mock_repo.read.side_effect = Exception("NoSuchKey")

        with (
            patch("src.core.module_cache.get_redis_client", return_value=mock_client),
            patch("src.core.module_cache.RepoStorage", return_value=mock_repo),
        ):
            from src.core.module_cache import get_module

            result = await get_module("nonexistent.py")
            assert result is None

    async def test_get_module_s3_fallback_handles_binary(self, mock_redis_client):
        """S3 fallback gracefully handles non-UTF-8 content."""
        mock_client, _ = mock_redis_client
        mock_client.get.return_value = None

        mock_repo = AsyncMock()
        mock_repo.read.return_value = b"\x89PNG\r\n"  # Binary

        with (
            patch("src.core.module_cache.get_redis_client", return_value=mock_client),
            patch("src.core.module_cache.RepoStorage", return_value=mock_repo),
        ):
            from src.core.module_cache import get_module

            result = await get_module("image.png")
            assert result is None

    async def test_set_module(self, mock_redis_client):
        """Test caching a module."""
        mock_client, mock_redis = mock_redis_client

        with patch("src.core.module_cache.get_redis_client", return_value=mock_client):
            from src.core.module_cache import set_module

            await set_module(
                path="shared/test.py",
                content="print('hello')",
                content_hash="abc123",
            )

            # Verify module was cached
            mock_client.setex.assert_called_once()
            call_args = mock_client.setex.call_args
            assert call_args[0][0] == "bifrost:module:shared/test.py"
            assert call_args[0][1] == 86400  # 24hr TTL

            # Verify content was stored as JSON
            stored_data = json.loads(call_args[0][2])
            assert stored_data["content"] == "print('hello')"
            assert stored_data["path"] == "shared/test.py"
            assert stored_data["hash"] == "abc123"

            # Verify path was added to index
            mock_redis.sadd.assert_called_once_with("bifrost:module:index", "shared/test.py")

    async def test_invalidate_module(self, mock_redis_client):
        """Test removing a module from cache."""
        mock_client, mock_redis = mock_redis_client

        with patch("src.core.module_cache.get_redis_client", return_value=mock_client):
            from src.core.module_cache import invalidate_module

            await invalidate_module("shared/test.py")

            mock_client.delete.assert_called_once_with("bifrost:module:shared/test.py")
            mock_redis.srem.assert_called_once_with("bifrost:module:index", "shared/test.py")

    async def test_get_all_module_paths(self, mock_redis_client):
        """Test getting all cached module paths."""
        mock_client, mock_redis = mock_redis_client
        mock_redis.smembers.return_value = {"shared/a.py", "shared/b.py", "modules/c.py"}

        with patch("src.core.module_cache.get_redis_client", return_value=mock_client):
            from src.core.module_cache import get_all_module_paths

            result = await get_all_module_paths()

            assert result == {"shared/a.py", "shared/b.py", "modules/c.py"}
            mock_redis.smembers.assert_called_once_with("bifrost:module:index")

    async def test_get_all_module_paths_empty(self, mock_redis_client):
        """Test getting module paths when cache is empty."""
        mock_client, mock_redis = mock_redis_client
        mock_redis.smembers.return_value = set()

        with patch("src.core.module_cache.get_redis_client", return_value=mock_client):
            from src.core.module_cache import get_all_module_paths

            result = await get_all_module_paths()

            assert result == set()

    async def test_clear_module_cache(self, mock_redis_client):
        """Test clearing all modules from cache."""
        mock_client, mock_redis = mock_redis_client
        mock_redis.smembers.return_value = {"shared/a.py", "shared/b.py"}

        with patch("src.core.module_cache.get_redis_client", return_value=mock_client):
            from src.core.module_cache import clear_module_cache

            count = await clear_module_cache()

            assert count == 2
            mock_redis.delete.assert_called()

    async def test_clear_module_cache_empty(self, mock_redis_client):
        """Test clearing cache when already empty."""
        mock_client, mock_redis = mock_redis_client
        mock_redis.smembers.return_value = set()

        with patch("src.core.module_cache.get_redis_client", return_value=mock_client):
            from src.core.module_cache import clear_module_cache

            count = await clear_module_cache()

            assert count == 0


class TestModuleCacheSync:
    """Tests for synchronous module cache functions."""

    @pytest.fixture
    def mock_sync_redis(self):
        """Create a mock sync Redis client."""
        mock = MagicMock()
        mock.get.return_value = None
        mock.smembers.return_value = set()
        return mock

    def test_get_module_sync_found(self, mock_sync_redis):
        """Test fetching a module synchronously."""
        cached_data = {"content": "print('hello')", "path": "shared/test.py", "hash": "abc123"}
        mock_sync_redis.get.return_value = json.dumps(cached_data)

        with patch("src.core.module_cache_sync._get_sync_redis", return_value=mock_sync_redis):
            from src.core.module_cache_sync import get_module_sync

            result = get_module_sync("shared/test.py")

            assert result is not None
            assert result["content"] == "print('hello')"
            mock_sync_redis.get.assert_called_once_with("bifrost:module:shared/test.py")

    def test_get_module_sync_not_found_returns_none(self, mock_sync_redis):
        """Test fetching a nonexistent module returns None (no DB fallback)."""
        mock_sync_redis.get.return_value = None

        with patch("src.core.module_cache_sync._get_sync_redis", return_value=mock_sync_redis):
            from src.core.module_cache_sync import get_module_sync

            result = get_module_sync("nonexistent.py")

            assert result is None

    def test_get_module_sync_handles_redis_error(self, mock_sync_redis):
        """Test that Redis errors return None instead of crashing."""
        import redis

        mock_sync_redis.get.side_effect = redis.RedisError("Connection failed")

        with patch("src.core.module_cache_sync._get_sync_redis", return_value=mock_sync_redis):
            from src.core.module_cache_sync import get_module_sync

            result = get_module_sync("shared/test.py")

            assert result is None

    def test_get_module_index_sync(self, mock_sync_redis):
        """Test getting module index synchronously."""
        mock_sync_redis.smembers.return_value = {"shared/a.py", "modules/b.py"}

        with patch("src.core.module_cache_sync._get_sync_redis", return_value=mock_sync_redis):
            from src.core.module_cache_sync import get_module_index_sync

            result = get_module_index_sync()

            assert result == {"shared/a.py", "modules/b.py"}
            mock_sync_redis.smembers.assert_called_once_with("bifrost:module:index")

    def test_get_module_index_sync_empty(self, mock_sync_redis):
        """Test getting empty module index.

        When Redis returns an empty set, the function falls back to listing S3.
        Patch the S3 helper to return empty too, otherwise this test would hit
        the real S3 client (populated by other tests in the shared test stack).
        """
        mock_sync_redis.smembers.return_value = set()

        with patch("src.core.module_cache_sync._get_sync_redis", return_value=mock_sync_redis), \
             patch("src.core.module_cache_sync._list_s3_modules", return_value=set()):
            from src.core.module_cache_sync import get_module_index_sync

            result = get_module_index_sync()

            assert result == set()

    def test_get_module_index_sync_handles_redis_error(self, mock_sync_redis):
        """Test that Redis errors return empty set."""
        import redis

        mock_sync_redis.smembers.side_effect = redis.RedisError("Connection failed")

        with patch("src.core.module_cache_sync._get_sync_redis", return_value=mock_sync_redis):
            from src.core.module_cache_sync import get_module_index_sync

            result = get_module_index_sync()

            assert result == set()

    def test_reset_sync_redis(self):
        """Test resetting the sync Redis client."""
        from src.core.module_cache_sync import reset_sync_redis

        # Should not raise
        reset_sync_redis()


class TestCachedModuleTypedDict:
    """Tests for the CachedModule TypedDict."""

    def test_cached_module_structure(self):
        """Verify CachedModule has expected fields."""
        from src.core.module_cache import CachedModule

        # Create a valid CachedModule
        module: CachedModule = {
            "content": "print('test')",
            "path": "shared/test.py",
            "hash": "abc123def456",
        }

        assert module["content"] == "print('test')"
        assert module["path"] == "shared/test.py"
        assert module["hash"] == "abc123def456"


class TestKeyPatterns:
    """Tests for Redis key patterns."""

    def test_module_key_prefix(self):
        """Verify module key prefix is correct."""
        from src.core.module_cache import MODULE_KEY_PREFIX

        assert MODULE_KEY_PREFIX == "bifrost:module:"

    def test_module_index_key(self):
        """Verify module index key is correct."""
        from src.core.module_cache import MODULE_INDEX_KEY

        assert MODULE_INDEX_KEY == "bifrost:module:index"

    def test_key_patterns_consistent(self):
        """Verify async and sync modules use same key patterns."""
        from src.core.module_cache import MODULE_INDEX_KEY as ASYNC_INDEX
        from src.core.module_cache import MODULE_KEY_PREFIX as ASYNC_PREFIX
        from src.core.module_cache_sync import MODULE_INDEX_KEY as SYNC_INDEX
        from src.core.module_cache_sync import MODULE_KEY_PREFIX as SYNC_PREFIX

        # Both modules should import from module_cache, so these should be identical
        assert ASYNC_PREFIX == SYNC_PREFIX
        assert ASYNC_INDEX == SYNC_INDEX
