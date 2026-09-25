"""Engine-local import channel: ``module_cache_sync`` wiring.

- Redis hits never touch the channel.
- After Redis misses, an installed transport serves resolve/fetch with
  no HTTP/S3 fallback: 404 advances to the next candidate, any other
  local failure raises.
- Without an installed transport the existing HTTP/S3 fallbacks apply
  unchanged, and successful local fetches re-cache to Redis.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

import src.core.module_cache_sync as mcs
from bifrost._import_transport import (
    ImportNotFound,
    ImportServiceError,
    ImportTransportTimeout,
)


def _redis_mock(modules: dict[str, dict] | None = None) -> MagicMock:
    redis = MagicMock()
    store = dict(modules or {})

    def _get(key: str) -> str | None:
        value = store.get(key)
        return json.dumps(value) if value is not None else None

    redis.get.side_effect = _get
    return redis


def _resolution(
    kind: str = "module",
    path: str = "cold/dep.py",
    storage_path: str = "cold/dep.py",
    content: str = "VALUE = 1\n",
) -> dict:
    return {
        "kind": kind,
        "path": path,
        "storage_path": storage_path,
        "content": content,
        "hash": "abc",
    }


@pytest.fixture(autouse=True)
def _fresh_resolution_memo():
    """Isolate the thread-local execution memo between tests."""
    mcs.clear_solution_context()
    yield
    mcs.clear_solution_context()


@pytest.fixture()
def _no_transport():
    with patch.object(mcs, "_get_import_transport", return_value=None):
        yield


@pytest.fixture()
def _solution_flag():
    solution_id = uuid4().hex
    mcs.set_solution_context(solution_id, global_repo_access=True)
    try:
        yield solution_id
    finally:
        mcs.clear_solution_context()


class TestRedisHitAvoidsChannel:
    def test_resolve_redis_hit_never_touches_channel(self, _no_transport):
        del _no_transport
        transport = MagicMock()
        with (
            patch.object(mcs, "_get_import_transport", return_value=transport),
            patch.object(
                mcs,
                "_get_cached_module_resolution",
                return_value=mcs.ModuleResolution(
                    kind="module",
                    path="cold/dep.py",
                    content="VALUE = 1\n",
                    hash="abc",
                    storage_path="cold/dep.py",
                ),
            ),
        ):
            resolution = mcs.resolve_module_sync("cold.dep")
        assert resolution.kind == "module"
        transport.call_modules_resolve.assert_not_called()

    def test_fetch_redis_hit_never_touches_channel(self):
        module = {"content": "VALUE = 1\n", "path": "cold/dep.py", "hash": "abc"}
        redis = _redis_mock({"bifrost:module:cold/dep.py": module})
        transport = MagicMock()
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_import_transport", return_value=transport),
        ):
            assert mcs.get_module_sync("cold/dep.py")["content"] == "VALUE = 1\n"
        transport.call_modules_fetch.assert_not_called()


class TestResolveViaChannel:
    def test_cold_resolve_uses_transport_not_http(self):
        transport = MagicMock()
        transport.call_modules_resolve.return_value = _resolution()
        with (
            patch.object(mcs, "_get_cached_module_resolution", return_value=None),
            patch.object(mcs, "_get_exact_scoped_module", return_value=None),
            patch.object(mcs, "_get_import_transport", return_value=transport),
            patch.object(
                mcs,
                "_fetch_module_resolution_from_api",
                side_effect=AssertionError("HTTP fallback must not run"),
            ),
        ):
            resolution = mcs.resolve_module_sync("cold.dep")
        assert resolution.kind == "module"
        assert resolution.content == "VALUE = 1\n"
        transport.call_modules_resolve.assert_called_once_with("cold.dep")

    def test_resolve_transport_failure_raises_without_http(self):
        transport = MagicMock()
        transport.call_modules_resolve.side_effect = ImportServiceError("boom (500)")
        with (
            patch.object(mcs, "_get_cached_module_resolution", return_value=None),
            patch.object(mcs, "_get_exact_scoped_module", return_value=None),
            patch.object(mcs, "_get_import_transport", return_value=transport),
            patch.object(
                mcs,
                "_fetch_module_resolution_from_api",
                side_effect=AssertionError("HTTP fallback must not run"),
            ),
        ):
            with pytest.raises(mcs.ModuleResolutionError, match="Local module resolver"):
                mcs.resolve_module_sync("cold.dep")

    def test_resolve_transport_timeout_is_wrapped_without_http(self):
        transport = MagicMock()
        transport.call_modules_resolve.side_effect = ImportTransportTimeout("stalled")
        with (
            patch.object(mcs, "_get_cached_module_resolution", return_value=None),
            patch.object(mcs, "_get_exact_scoped_module", return_value=None),
            patch.object(mcs, "_get_import_transport", return_value=transport),
            patch.object(
                mcs,
                "_fetch_module_resolution_from_api",
                side_effect=AssertionError("HTTP fallback must not run"),
            ),
        ):
            with pytest.raises(mcs.ModuleResolutionError, match="Local module resolver"):
                mcs.resolve_module_sync("cold.dep")

    def test_resolve_invalid_kind_raises(self):
        transport = MagicMock()
        transport.call_modules_resolve.return_value = {"kind": "wormhole"}
        with (
            patch.object(mcs, "_get_cached_module_resolution", return_value=None),
            patch.object(mcs, "_get_exact_scoped_module", return_value=None),
            patch.object(mcs, "_get_import_transport", return_value=transport),
        ):
            with pytest.raises(mcs.ModuleResolutionError, match="invalid kind"):
                mcs.resolve_module_sync("cold.dep")

    def test_no_transport_keeps_http_fallback(self, _no_transport):
        with (
            patch.object(mcs, "_get_cached_module_resolution", return_value=None),
            patch.object(mcs, "_get_exact_scoped_module", return_value=None),
            patch.object(
                mcs,
                "_fetch_module_resolution_from_api",
                return_value=mcs.ModuleResolution(
                    kind="namespace", path="cold/dep"
                ),
            ) as api,
        ):
            assert mcs.resolve_module_sync("cold.dep").kind == "namespace"
        api.assert_called_once_with("cold.dep")

    def test_installed_transport_lookup_does_not_hide_unexpected_errors(self):
        with patch("bifrost._import_transport.get", side_effect=RuntimeError("broken SDK")):
            with pytest.raises(RuntimeError, match="broken SDK"):
                mcs._get_import_transport()


class TestFetchViaChannel:
    def test_404_advances_to_next_candidate(self, _solution_flag):
        solution_id = _solution_flag
        rooted = f"_solutions/{solution_id}/cold/dep.py"
        module = {"content": "VALUE = 9\n", "path": "cold/dep.py", "hash": "h"}
        redis = _redis_mock()
        transport = MagicMock()
        transport.call_modules_fetch.side_effect = [
            ImportNotFound("miss"),
            dict(module),
        ]
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_import_transport", return_value=transport),
            patch.object(
                mcs,
                "_fetch_module_from_api",
                side_effect=AssertionError("HTTP fallback must not run"),
            ),
            patch.object(
                mcs,
                "_get_s3_module",
                side_effect=AssertionError("S3 fallback must not run"),
            ),
        ):
            fetched = mcs.get_module_sync("cold/dep.py")
        assert fetched is not None
        assert fetched["content"] == "VALUE = 9\n"
        assert [c.args[0] for c in transport.call_modules_fetch.call_args_list] == [
            rooted,
            "cold/dep.py",
        ]
        # Successful recache under the winning candidate key.
        redis.setex.assert_called_once()
        assert redis.setex.call_args[0][0] == "bifrost:module:cold/dep.py"

    def test_non_404_error_raises_without_fallbacks(self):
        redis = _redis_mock()
        transport = MagicMock()
        transport.call_modules_fetch.side_effect = ImportServiceError("boom (500)")
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_import_transport", return_value=transport),
            patch.object(
                mcs,
                "_fetch_module_from_api",
                side_effect=AssertionError("HTTP fallback must not run"),
            ),
            patch.object(
                mcs,
                "_get_s3_module",
                side_effect=AssertionError("S3 fallback must not run"),
            ),
        ):
            with pytest.raises(mcs.ModuleResolutionError, match="Local module fetch"):
                mcs.get_module_sync("cold/dep.py")

    def test_all_candidates_404_returns_none(self, _solution_flag):
        solution_id = _solution_flag
        redis = _redis_mock()
        transport = MagicMock()
        transport.call_modules_fetch.side_effect = ImportNotFound("miss")
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_import_transport", return_value=transport),
        ):
            assert mcs.get_module_sync("cold/dep.py") is None
        assert transport.call_modules_fetch.call_count == 2
        first_path = transport.call_modules_fetch.call_args_list[0].args[0]
        assert first_path == f"_solutions/{solution_id}/cold/dep.py"

    def test_no_transport_keeps_api_fallback(self, _no_transport):
        module = {"content": "VALUE = 3\n", "path": "cold/dep.py", "hash": "h"}
        redis = _redis_mock()
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_fetch_module_from_api", return_value=dict(module)),
        ):
            assert mcs.get_module_sync("cold/dep.py")["content"] == "VALUE = 3\n"
