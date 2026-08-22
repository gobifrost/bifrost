"""Tests for virtual import S3 fallback."""
import json
import logging
from unittest.mock import MagicMock, call, patch

import pytest

from src.core.module_cache_sync import ModuleResolution


def test_s3_fallback_on_redis_miss():
    """When Redis returns None, should try S3 and cache result."""
    from src.core.module_cache_sync import get_module_sync

    with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
         patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

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

    cached = json.dumps({"content": "cached content", "path": "shared/utils.py", "hash": "abc"})

    with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
         patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

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

    with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
         patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis_factory.return_value = mock_redis
        mock_s3.return_value = None

        result = get_module_sync("shared/nonexistent.py")

        assert result is None


def test_targeted_resolution_is_cached_per_execution_and_skips_index_endpoint():
    from src.core import module_cache_sync as mcs

    calls: list[str] = []

    def fake_resolve(name: str):
        calls.append(name)
        return mcs.ModuleResolution(kind="not_found", path=name.replace(".", "/"))

    mcs.clear_solution_context()
    try:
        with (
            patch("src.core.module_cache_sync._get_cached_module_resolution", return_value=None),
            patch("src.core.module_cache_sync._get_exact_scoped_module", return_value=None),
            patch("src.core.module_cache_sync._fetch_module_resolution_from_api", side_effect=fake_resolve),
        ):
            assert mcs.resolve_module_sync("missing.shared").kind == "not_found"
            assert mcs.resolve_module_sync("missing.shared").kind == "not_found"
            assert mcs.resolve_module_sync("missing.other").kind == "not_found"

        assert calls == ["missing.shared", "missing.other"]
    finally:
        mcs.clear_solution_context()


def test_targeted_resolution_threads_solution_scope_params():
    from src.core import module_cache_sync as mcs

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "kind": "module",
        "path": "modules/helpers.py",
        "storage_path": "_solutions/sol-1/modules/helpers.py",
        "content": "VALUE = 1",
        "hash": "abc",
    }
    client = MagicMock()
    client.get.return_value = response

    mcs._close_http_client()
    mcs.set_solution_context("sol-1", global_repo_access=True)
    try:
        with (
            patch("src.core.module_cache_sync._get_engine_credentials", return_value=("http://api", "token")),
            patch("src.core.module_cache_sync._get_cached_module_resolution", return_value=None),
            patch("src.core.module_cache_sync._get_exact_scoped_module", return_value=None),
            patch("httpx.Client", return_value=client) as client_cls,
        ):
            result = mcs.resolve_module_sync("modules.helpers")
            result_again = mcs.resolve_module_sync("modules.helpers")

        assert result.kind == "module"
        assert result_again is result
        assert result.content == "VALUE = 1"
        client_cls.assert_called_once_with(timeout=10.0)
        client.get.assert_called_once_with(
            "http://api/api/sdk/modules-resolve",
            headers={"Authorization": "Bearer token"},
            params={
                "name": "modules.helpers",
                "solution_id": "sol-1",
                "global_repo_access": True,
            },
        )
    finally:
        mcs.clear_solution_context()
        mcs._close_http_client()


def test_targeted_resolution_hydrates_resolver_metadata_from_redis_without_http():
    from src.core import module_cache_sync as mcs

    redis_client = MagicMock()
    redis_client.get.side_effect = [
        json.dumps(
            {
                "kind": "module",
                "path": "modules/tickets.py",
                "storage_path": "_solutions/sol-1/modules/tickets.py",
            }
        ),
        json.dumps(
            {
                "content": "VALUE = 42",
                "path": "_solutions/sol-1/modules/tickets.py",
                "hash": "abc",
            }
        ),
    ]
    mcs.set_solution_context("sol-1", global_repo_access=True)
    try:
        with (
            patch("src.core.module_cache_sync._get_sync_redis", return_value=redis_client),
            patch("src.core.module_cache_sync._fetch_module_resolution_from_api") as api,
        ):
            result = mcs.resolve_module_sync("modules.tickets")

        assert result == mcs.ModuleResolution(
            kind="module",
            path="modules/tickets.py",
            content="VALUE = 42",
            hash="abc",
            storage_path="_solutions/sol-1/modules/tickets.py",
        )
        api.assert_not_called()
    finally:
        mcs.clear_solution_context()


def test_targeted_resolution_reads_exact_workspace_module_without_http():
    from src.core import module_cache_sync as mcs

    redis_client = MagicMock()
    redis_client.get.side_effect = [
        None,
        json.dumps(
            {
                "content": "VALUE = 7",
                "path": "modules/tickets.py",
                "hash": "def",
            }
        ),
    ]
    mcs.clear_solution_context()
    try:
        with (
            patch("src.core.module_cache_sync._get_sync_redis", return_value=redis_client),
            patch("src.core.module_cache_sync._fetch_module_resolution_from_api") as api,
        ):
            result = mcs.resolve_module_sync("modules.tickets")

        assert result.kind == "module"
        assert result.content == "VALUE = 7"
        assert result.storage_path == "modules/tickets.py"
        api.assert_not_called()
    finally:
        mcs.clear_solution_context()


def test_solution_global_fallback_uses_api_when_primary_scope_has_no_exact_match():
    """The child must not let a global module bypass a Solution namespace."""
    from src.core import module_cache_sync as mcs

    redis_client = MagicMock()
    redis_client.get.return_value = None
    api_result = mcs.ModuleResolution(
        kind="namespace",
        path="modules",
    )
    mcs.set_solution_context("sol-1", global_repo_access=True)
    try:
        with (
            patch("src.core.module_cache_sync._get_sync_redis", return_value=redis_client),
            patch(
                "src.core.module_cache_sync._fetch_module_resolution_from_api",
                return_value=api_result,
            ) as api,
        ):
            result = mcs.resolve_module_sync("modules")

        assert result is api_result
        api.assert_called_once_with("modules")
        assert redis_client.get.call_args_list == [
            # Resolver metadata, then Solution module and package candidates.
            call("bifrost:module:resolution:sol-1:1:modules"),
            call("bifrost:module:_solutions/sol-1/modules.py"),
            call("bifrost:module:_solutions/sol-1/modules/__init__.py"),
        ]
    finally:
        mcs.clear_solution_context()


class TestTargetedResolver:
    """Tests for targeted resolver import behavior."""

    def test_namespace_package_resolves_via_targeted_s3_prefix_fallback(self):
        """Namespace package lookup uses bounded prefix detection, not module index."""
        from src.services.execution.virtual_import import VirtualModuleFinder

        finder = VirtualModuleFinder()

        with (
            patch(
                "src.services.execution.virtual_import.resolve_module_sync",
                return_value=ModuleResolution(
                    kind="namespace",
                    path="features",
                ),
            ) as mock_resolve,
        ):

            # "features" should be recognized as a namespace package
            spec = finder.find_spec("features")

            assert spec is not None
            assert spec.origin is None  # namespace package
            assert spec.submodule_search_locations == ["features"]
            mock_resolve.assert_called_once_with("features")


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

        with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
             patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

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
