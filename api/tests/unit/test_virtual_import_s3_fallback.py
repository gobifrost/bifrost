"""Tests for virtual import S3 fallback."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest


def test_s3_fallback_on_redis_miss():
    """When Redis returns None, should try S3 and cache result."""
    from src.core.module_cache_sync import get_module_sync

    with (
        patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
        patch("src.core.module_cache_sync._get_s3_module") as mock_s3,
    ):
        # Redis returns None (cache miss)
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis_factory.return_value = mock_redis

        # S3 returns the module content
        mock_s3.return_value = b"def helper(): return 42"

        result = get_module_sync("shared/utils.py")

        # Should have tried S3
        mock_s3.assert_called_once_with("shared/utils.py")
        # Should have cached to Redis
        assert mock_redis.setex.called
        # Should return the module
        assert result is not None
        assert result["content"] == "def helper(): return 42"


def test_redis_hit_skips_s3():
    """When Redis has the module, should not touch S3."""
    from src.core.module_cache_sync import get_module_sync

    cached = json.dumps(
        {"content": "cached content", "path": "shared/utils.py", "hash": "abc"}
    )

    with (
        patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
        patch("src.core.module_cache_sync._get_s3_module") as mock_s3,
    ):
        mock_redis = MagicMock()
        mock_redis.get.return_value = cached
        mock_redis_factory.return_value = mock_redis

        result = get_module_sync("shared/utils.py")

        mock_s3.assert_not_called()
        assert result is not None
        assert result["content"] == "cached content"


def test_s3_miss_returns_none():
    """When both Redis and S3 miss, should return None."""
    from src.core.module_cache_sync import get_module_sync

    with (
        patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
        patch("src.core.module_cache_sync._get_s3_module") as mock_s3,
    ):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis_factory.return_value = mock_redis
        mock_s3.return_value = None

        result = get_module_sync("shared/nonexistent.py")

        assert result is None


class TestModuleIndexS3Fallback:
    """Tests for get_module_index_sync S3 fallback when Redis index is empty."""

    def test_empty_redis_index_falls_back_to_s3(self):
        """When Redis index is empty, should list S3 and return paths."""
        from src.core.module_cache_sync import get_module_index_sync
        from src.core.module_cache import MODULE_INDEX_KEY

        s3_paths = {
            "features/spotify_journal/services/spotify_api.py",
            "features/spotify_journal/__init__.py",
        }

        with (
            patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
            patch(
                "src.core.module_cache_sync._list_s3_modules", return_value=s3_paths
            ) as mock_list,
        ):
            mock_redis = MagicMock()
            mock_redis.smembers.return_value = set()  # Redis index empty
            mock_redis_factory.return_value = mock_redis

            result = get_module_index_sync()

            mock_list.assert_called_once()
            assert result == s3_paths
            # Should have repopulated Redis
            mock_redis.sadd.assert_called_once()
            assert mock_redis.sadd.call_args[0][0] == MODULE_INDEX_KEY

    def test_populated_redis_index_skips_s3(self):
        """When Redis index has entries, should not touch S3."""
        from src.core.module_cache_sync import get_module_index_sync

        with (
            patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
            patch("src.core.module_cache_sync._list_s3_modules") as mock_list,
        ):
            mock_redis = MagicMock()
            mock_redis.smembers.return_value = {
                "features/spotify_journal/services/spotify_api.py"
            }
            mock_redis_factory.return_value = mock_redis

            result = get_module_index_sync()

            mock_list.assert_not_called()
            assert "features/spotify_journal/services/spotify_api.py" in result

    def test_empty_redis_and_empty_s3_returns_empty_set(self):
        """When both Redis and S3 are empty, should return empty set."""
        from src.core.module_cache_sync import get_module_index_sync

        with (
            patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
            patch("src.core.module_cache_sync._list_s3_modules", return_value=set()),
        ):
            mock_redis = MagicMock()
            mock_redis.smembers.return_value = set()
            mock_redis_factory.return_value = mock_redis

            result = get_module_index_sync()

            assert result == set()
            # Should not try to sadd empty set
            mock_redis.sadd.assert_not_called()

    def test_namespace_package_resolves_via_s3_index_fallback(self):
        """Integration: namespace package lookup succeeds when Redis index is cold but S3 has modules."""
        from src.services.execution.virtual_import import VirtualModuleFinder

        finder = VirtualModuleFinder()

        s3_paths = {"features/spotify_journal/services/spotify_api.py"}

        def mock_get_module(path: str):
            if path == "features/spotify_journal/services/spotify_api.py":
                return {"content": "API = True", "path": path, "hash": "abc"}
            return None

        with (
            patch(
                "src.services.execution.virtual_import.get_module_sync",
                side_effect=mock_get_module,
            ),
            patch(
                "src.services.execution.virtual_import.get_module_index_sync",
                return_value=s3_paths,
            ),
        ):
            # "features" should be recognized as a namespace package
            spec = finder.find_spec("features")

            assert spec is not None
            assert spec.origin is None  # namespace package
            assert spec.submodule_search_locations == ["features"]


class TestS3ClientCaching:
    """Tests for botocore S3 client caching in _get_s3_module."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset S3 client cache before each test."""
        from src.core.module_cache_sync import reset_s3_client

        reset_s3_client()
        yield
        reset_s3_client()

    def test_nosuchkey_logs_debug_not_warning(self, caplog):
        """NoSuchKey errors should log at DEBUG level, not WARNING."""
        import src.core.module_cache_sync as mod

        # Set up mock botocore client that raises NoSuchKey
        mock_client = MagicMock()
        nosuchkey_error = Exception("NoSuchKey")
        nosuchkey_error.response = {"Error": {"Code": "NoSuchKey"}}
        mock_client.get_object.side_effect = nosuchkey_error

        mod._s3_client = mock_client
        mod._s3_available = True

        env_vars = {
            "BIFROST_S3_ENDPOINT_URL": "http://localhost:8333",
            "BIFROST_S3_ACCESS_KEY": "test",
            "BIFROST_S3_SECRET_KEY": "test",
            "BIFROST_S3_BUCKET": "test-bucket",
        }

        with patch.dict("os.environ", env_vars):
            with caplog.at_level(logging.DEBUG, logger="src.core.module_cache_sync"):
                result = mod._get_s3_module("missing/module.py")

        assert result is None
        # Should have a DEBUG log about not found, no WARNING
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not found in S3" in r.message for r in debug_msgs)
        assert not any("S3 fallback error" in r.message for r in warning_msgs)

    def test_s3_unavailable_returns_none_gracefully(self):
        """When S3 client is unavailable, _get_s3_module should return None."""
        import src.core.module_cache_sync as mod

        # Simulate unavailable client
        mod._s3_available = False
        mod._s3_client = None

        result = mod._get_s3_module("any/path.py")
        assert result is None

    def test_module_index_populated_on_s3_fallback(self):
        """S3 fallback should add module to Redis index after caching."""
        from src.core.module_cache_sync import get_module_sync

        with (
            patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
            patch("src.core.module_cache_sync._get_s3_module") as mock_s3,
        ):
            mock_redis = MagicMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis
            mock_s3.return_value = b"print('hello')"

            result = get_module_sync("modules/helper.py")

            assert result is not None
            # Should have added to module index
            mock_redis.sadd.assert_called_once()
            # The key should be the module index key
            from src.core.module_cache import MODULE_INDEX_KEY

            call_args = mock_redis.sadd.call_args
            assert call_args[0][0] == MODULE_INDEX_KEY
            assert call_args[0][1] == "modules/helper.py"


class TestAzureBlobFallback:
    """Tests for Azure Blob fallback when Blob is the storage provider."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset object storage client caches before each test."""
        from src.core.module_cache_sync import reset_blob_client, reset_s3_client

        reset_blob_client()
        reset_s3_client()
        yield
        reset_blob_client()
        reset_s3_client()

    def test_blob_provider_reads_blob_on_redis_miss(self):
        """When Blob is selected, Redis misses should read from Blob, not S3."""
        from src.core.module_cache_sync import get_module_sync

        with (
            patch.dict("os.environ", {"BIFROST_OBJECT_STORAGE_PROVIDER": "azure_blob"}),
            patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
            patch("src.core.module_cache_sync._get_blob_module") as mock_blob,
            patch("src.core.module_cache_sync._get_s3_module") as mock_s3,
        ):
            mock_redis = MagicMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis
            mock_blob.return_value = b"def helper(): return 42"

            result = get_module_sync("shared/utils.py")

            mock_blob.assert_called_once_with("shared/utils.py")
            mock_s3.assert_not_called()
            assert result is not None
            assert result["content"] == "def helper(): return 42"
            assert mock_redis.setex.called

    def test_blob_provider_lists_blob_when_index_is_empty(self):
        """When Blob is selected, cold Redis index should rebuild from Blob."""
        from src.core.module_cache import MODULE_INDEX_KEY
        from src.core.module_cache_sync import get_module_index_sync

        blob_paths = {
            "features/ninjaone/workflows/seed_bifrost_webhook_custom_fields.py",
            "modules/ninjaone.py",
        }

        with (
            patch.dict("os.environ", {"BIFROST_OBJECT_STORAGE_PROVIDER": "azure_blob"}),
            patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory,
            patch(
                "src.core.module_cache_sync._list_blob_modules", return_value=blob_paths
            ) as mock_blob_list,
            patch("src.core.module_cache_sync._list_s3_modules") as mock_s3_list,
        ):
            mock_redis = MagicMock()
            mock_redis.smembers.return_value = set()
            mock_redis_factory.return_value = mock_redis

            result = get_module_index_sync()

            mock_blob_list.assert_called_once()
            mock_s3_list.assert_not_called()
            assert result == blob_paths
            mock_redis.sadd.assert_called_once()
            assert mock_redis.sadd.call_args[0][0] == MODULE_INDEX_KEY

    def test_blob_not_found_logs_debug_not_warning(self, caplog):
        """BlobNotFound should be a cache miss, not a noisy warning."""
        import src.core.module_cache_sync as mod

        class BlobNotFound(Exception):
            error_code = "BlobNotFound"

        mock_client = MagicMock()
        mock_client.download_blob.side_effect = BlobNotFound("missing")
        mod._blob_container_client = mock_client
        mod._blob_available = True

        with caplog.at_level(logging.DEBUG, logger="src.core.module_cache_sync"):
            result = mod._get_blob_module("missing/module.py")

        assert result is None
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not found in Azure Blob" in r.message for r in debug_msgs)
        assert not any("Azure Blob fallback error" in r.message for r in warning_msgs)
