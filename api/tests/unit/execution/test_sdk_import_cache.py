"""Engine-local import channel: ``module_cache_sync`` wiring.

- Redis hits never touch the channel or the engine socket.
- After Redis misses, an installed engine socket serves resolve/fetch through
  ``engine_request_sync`` with no HTTP/S3 fallback: a 404 advances to the
  next candidate, any other local failure raises.
- After Redis misses, a legacy installed import transport serves resolve/fetch
  with no HTTP/S3 fallback.
- Without either local transport the existing HTTP/S3 fallbacks apply
  unchanged, and successful local fetches re-cache to Redis.
"""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
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


@pytest.fixture(autouse=True)
def _no_engine_socket():
    """Default every test to the non-engine path; socket tests override it."""
    with patch.object(mcs, "_get_engine_client", return_value=None):
        yield


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


class _EngineSocketClient:
    """Stand-in for ``BifrostClient`` reading the worker socket synchronously."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def engine_request_sync(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.calls.append((method, path, kwargs))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]


def _json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


class TestEngineSocketWarmHit:
    """Warm child Redis reads stay direct; the socket is never consulted."""

    def test_resolve_redis_hit_never_touches_engine_socket(self):
        client = _EngineSocketClient([])
        with (
            patch.object(mcs, "_get_engine_client", return_value=client),
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
        assert client.calls == []

    def test_fetch_redis_hit_never_touches_engine_socket(self):
        module = {"content": "VALUE = 1\n", "path": "cold/dep.py", "hash": "abc"}
        redis = _redis_mock({"bifrost:module:cold/dep.py": module})
        client = _EngineSocketClient([])
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_engine_client", return_value=client),
        ):
            assert mcs.get_module_sync("cold/dep.py")["content"] == "VALUE = 1\n"
        assert client.calls == []


class TestEngineSocketColdPath:
    """Cold misses resolve and fetch over the trusted engine socket."""

    def _cold_resolve_patches(self, client: _EngineSocketClient):
        @contextlib.contextmanager
        def _patches():
            with (
                patch.object(mcs, "_get_engine_client", return_value=client),
                patch.object(mcs, "_get_cached_module_resolution", return_value=None),
                patch.object(mcs, "_get_exact_scoped_module", return_value=None),
                patch.object(
                    mcs,
                    "_fetch_module_resolution_from_api",
                    side_effect=AssertionError("HTTP fallback must not run"),
                ),
                patch.object(
                    mcs,
                    "_get_import_transport",
                    side_effect=AssertionError("shared pipe must not be consulted"),
                ),
            ):
                yield

        return _patches()

    def test_cold_resolve_uses_engine_socket_not_http_or_pipe(self):
        client = _EngineSocketClient(
            [_json_response(_resolution(kind="package", content="PKG = 1\n"))]
        )
        with self._cold_resolve_patches(client):
            resolution = mcs.resolve_module_sync("cold.dep")
        assert resolution.kind == "package"
        assert resolution.content == "PKG = 1\n"
        assert resolution.hash == "abc"
        assert resolution.storage_path == "cold/dep.py"
        assert client.calls == [
            ("GET", "/api/sdk/modules-resolve", {"params": {"name": "cold.dep"}})
        ]

    def test_cold_resolve_not_found_kind_is_preserved(self):
        client = _EngineSocketClient(
            [_json_response({"kind": "not_found", "path": "missing/dep"})]
        )
        with self._cold_resolve_patches(client):
            resolution = mcs.resolve_module_sync("missing.dep")
        assert resolution == mcs.ModuleResolution(kind="not_found", path="missing/dep")

    def test_resolve_preserves_solution_scope_query(self):
        client = _EngineSocketClient([_json_response(_resolution())])
        mcs.set_solution_context("sol-1", global_repo_access=True)
        try:
            with self._cold_resolve_patches(client):
                mcs.resolve_module_sync("modules.helpers")
        finally:
            mcs.clear_solution_context()
        assert client.calls[0][2]["params"] == {
            "name": "modules.helpers",
            "solution_id": "sol-1",
            "global_repo_access": True,
        }

    def test_resolve_invalid_kind_raises(self):
        client = _EngineSocketClient([_json_response({"kind": "wormhole"})])
        with self._cold_resolve_patches(client):
            with pytest.raises(mcs.ModuleResolutionError, match="invalid kind"):
                mcs.resolve_module_sync("cold.dep")

    def test_resolve_socket_failure_raises_without_http_or_pipe(self):
        client = _EngineSocketClient([httpx.ConnectError("dead socket")])
        with self._cold_resolve_patches(client):
            with pytest.raises(
                mcs.ModuleResolutionError, match="Engine-local module resolver failed"
            ):
                mcs.resolve_module_sync("cold.dep")

    def test_resolve_non_200_raises_without_http_or_pipe(self):
        client = _EngineSocketClient([_json_response({"detail": "forbidden"}, 403)])
        with self._cold_resolve_patches(client):
            with pytest.raises(
                mcs.ModuleResolutionError, match="returned 403"
            ):
                mcs.resolve_module_sync("cold.dep")

    def test_cold_fetch_uses_engine_socket_and_recaches(self):
        module = {"content": "VALUE = 1\n", "path": "cold/dep.py", "hash": "h"}
        client = _EngineSocketClient([_json_response(dict(module))])
        redis = _redis_mock()
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_engine_client", return_value=client),
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
        assert fetched["content"] == "VALUE = 1\n"
        assert fetched.get("storage_path") == "cold/dep.py"
        assert client.calls == [("GET", "/api/sdk/modules/cold/dep.py", {})]
        redis.setex.assert_called_once()
        assert redis.setex.call_args[0][0] == "bifrost:module:cold/dep.py"
        redis.sadd.assert_called_once()

    def test_fetch_404_advances_to_next_candidate(self):
        solution_id = uuid4().hex
        mcs.set_solution_context(solution_id, global_repo_access=True)
        rooted = f"_solutions/{solution_id}/cold/dep.py"
        module = {"content": "VALUE = 9\n", "path": "cold/dep.py", "hash": "h"}
        client = _EngineSocketClient(
            [_json_response({"detail": "miss"}, 404), _json_response(dict(module))]
        )
        redis = _redis_mock()
        try:
            with (
                patch.object(mcs, "_get_sync_redis", return_value=redis),
                patch.object(mcs, "_get_engine_client", return_value=client),
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
        finally:
            mcs.clear_solution_context()
        assert fetched is not None
        assert fetched["content"] == "VALUE = 9\n"
        assert [call[1] for call in client.calls] == [
            f"/api/sdk/modules/{rooted}",
            "/api/sdk/modules/cold/dep.py",
        ]
        assert redis.setex.call_args[0][0] == "bifrost:module:cold/dep.py"

    def test_all_candidates_404_returns_none(self):
        mcs.set_solution_context(uuid4().hex, global_repo_access=True)
        client = _EngineSocketClient(
            [_json_response({"detail": "miss"}, 404), _json_response({"detail": "miss"}, 404)]
        )
        redis = _redis_mock()
        try:
            with (
                patch.object(mcs, "_get_sync_redis", return_value=redis),
                patch.object(mcs, "_get_engine_client", return_value=client),
            ):
                assert mcs.get_module_sync("cold/dep.py") is None
        finally:
            mcs.clear_solution_context()
        assert len(client.calls) == 2

    def test_non_404_fetch_error_raises_without_fallbacks(self):
        client = _EngineSocketClient([_json_response({"detail": "boom"}, 500)])
        redis = _redis_mock()
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_engine_client", return_value=client),
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
            with pytest.raises(mcs.ModuleResolutionError, match="returned 500"):
                mcs.get_module_sync("cold/dep.py")

    def test_fetch_socket_failure_raises_without_api_or_s3(self):
        client = _EngineSocketClient([httpx.ConnectError("dead socket")])
        redis = _redis_mock()
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_engine_client", return_value=client),
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
            with pytest.raises(
                mcs.ModuleResolutionError, match="Engine-local module fetch failed"
            ):
                mcs.get_module_sync("cold/dep.py")

    def test_large_module_round_trips_unchanged(self):
        content = "# large\n" + ("value = 1\n" * 50000)
        client = _EngineSocketClient(
            [_json_response({"content": content, "path": "big.py", "hash": "h"})]
        )
        redis = _redis_mock()
        with (
            patch.object(mcs, "_get_sync_redis", return_value=redis),
            patch.object(mcs, "_get_engine_client", return_value=client),
        ):
            fetched = mcs.get_module_sync("big.py")
        assert fetched is not None
        assert fetched["content"] == content
        assert len(fetched["content"]) == len(content)
