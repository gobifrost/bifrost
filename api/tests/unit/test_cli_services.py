"""Tests for ``bifrost services`` CLI commands.

The tests mock ``BifrostClient.get_instance`` and ``RefResolver.resolve`` so no
network or credentials are required (mirrors ``test_cli_tables.py``). The
``service`` ref-kind resolver itself is exercised directly against a fake
client at the bottom.
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
import pytest
from click.testing import CliRunner

# Ensure the standalone bifrost package is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.services import services_group  # noqa: E402
from bifrost.refs import (  # noqa: E402
    AmbiguousRefError,
    RefNotFoundError,
    resolve_ref,
)


_DUMMY_REQUEST = httpx.Request("GET", "https://bifrost.test/api/services")
_SVC_UUID = "11111111-1111-1111-1111-111111111111"
_ATTEMPT_UUID = "22222222-2222-2222-2222-222222222222"


def _fake_response(body: dict, *, status: int = 200) -> httpx.Response:
    """Build an httpx.Response with a request set (required for raise_for_status)."""
    return httpx.Response(status, json=body, request=_DUMMY_REQUEST)


def _async_value(value: str):  # type: ignore[no-untyped-def]
    """Return a coroutine that resolves to ``value``."""

    async def _inner():  # type: ignore[no-untyped-def]
        return value

    return _inner()


def _async_raise(exc: Exception):  # type: ignore[no-untyped-def]
    """Return a coroutine that raises ``exc``."""

    async def _inner():  # type: ignore[no-untyped-def]
        raise exc

    return _inner()


def _make_mock_client(captured: dict) -> mock.AsyncMock:
    """Return a mock BifrostClient whose verbs record path/params/body."""

    async def capturing_get(path, params=None):  # type: ignore[no-untyped-def]
        captured["get_path"] = path
        captured["get_params"] = params
        return _fake_response({"items": [], "total": 0})

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured["post_path"] = path
        captured["post_body"] = json
        return _fake_response({"id": _SVC_UUID, "desired_state": "running"})

    async def capturing_patch(path, json=None):  # type: ignore[no-untyped-def]
        captured["patch_path"] = path
        captured["patch_body"] = json
        return _fake_response({"id": _SVC_UUID, **(json or {})})

    client = mock.AsyncMock()
    client.get = capturing_get
    client.post = capturing_post
    client.patch = capturing_patch
    return client


def _invoke(
    args: list[str],
    captured: dict,
    *,
    resolve_to: str = _SVC_UUID,
) -> "CliRunner._Result":  # type: ignore[name-defined]
    client = _make_mock_client(captured)
    with (
        mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client),
        mock.patch(
            "bifrost.refs.RefResolver.resolve",
            new=lambda self, kind, ref: _async_value(resolve_to),
        ),
    ):
        return CliRunner().invoke(services_group, args)


class TestList:
    def test_list_hits_collection_endpoint(self) -> None:
        captured: dict = {}
        result = _invoke(["list"], captured)
        assert result.exit_code == 0, result.output
        assert captured["get_path"] == "/api/services"
        # No pagination flags → no query params (server defaults apply).
        assert captured["get_params"] == {}

    def test_list_forwards_limit_offset(self) -> None:
        captured: dict = {}
        result = _invoke(["list", "--limit", "10", "--offset", "20"], captured)
        assert result.exit_code == 0, result.output
        assert captured["get_params"] == {"limit": 10, "offset": 20}


class TestGet:
    def test_get_resolves_ref_to_uuid_path(self) -> None:
        captured: dict = {}
        result = _invoke(["get", "my-workflow"], captured)
        assert result.exit_code == 0, result.output
        assert captured["get_path"] == f"/api/services/{_SVC_UUID}"

    def test_get_output_shape(self) -> None:
        """The wrapped service payload is echoed (human-readable fallback)."""
        captured: dict = {}
        result = _invoke(["get", _SVC_UUID], captured)
        assert result.exit_code == 0, result.output
        assert "total" in result.output


@pytest.mark.parametrize(
    "action",
    ["start", "stop", "restart", "enable", "disable"],
)
class TestActions:
    def test_each_action_posts_to_its_endpoint(self, action: str) -> None:
        captured: dict = {}
        result = _invoke([action, "my-workflow"], captured)
        assert result.exit_code == 0, result.output
        assert captured["post_path"] == f"/api/services/{_SVC_UUID}/{action}"


class TestUpdate:
    def test_policy_flags_map_to_patch_body(self) -> None:
        captured: dict = {}
        result = _invoke(
            [
                "update",
                "my-workflow",
                "--startup-policy",
                "manual",
                "--restart-policy",
                "never",
                "--graceful-shutdown-seconds",
                "30",
            ],
            captured,
        )
        assert result.exit_code == 0, result.output
        assert captured["patch_path"] == f"/api/services/{_SVC_UUID}"
        assert captured["patch_body"] == {
            "startup_policy": "manual",
            "restart_policy": "never",
            "graceful_shutdown_seconds": 30,
        }

    def test_unset_flags_are_omitted(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["update", _SVC_UUID, "--crash-loop-max-restarts", "5"],
            captured,
        )
        assert result.exit_code == 0, result.output
        assert captured["patch_body"] == {"crash_loop_max_restarts": 5}

    def test_invalid_choice_rejected(self) -> None:
        captured: dict = {}
        result = _invoke(["update", _SVC_UUID, "--startup-policy", "sometimes"], captured)
        assert result.exit_code == 2
        assert "patch_path" not in captured


class TestAttempts:
    def test_attempts_hits_nested_endpoint(self) -> None:
        captured: dict = {}
        result = _invoke(["attempts", "my-workflow"], captured)
        assert result.exit_code == 0, result.output
        assert captured["get_path"] == f"/api/services/{_SVC_UUID}/attempts"
        assert captured["get_params"] == {}

    def test_attempts_forwards_limit_offset(self) -> None:
        captured: dict = {}
        result = _invoke(["attempts", _SVC_UUID, "--limit", "5"], captured)
        assert result.exit_code == 0, result.output
        assert captured["get_params"] == {"limit": 5}


class TestLogs:
    def test_logs_flags_map_to_query_params(self) -> None:
        captured: dict = {}
        result = _invoke(
            [
                "logs",
                "my-workflow",
                "--attempt-id",
                _ATTEMPT_UUID,
                "--level",
                "INFO",
                "--level",
                "ERROR",
                "--start-date",
                "2026-09-01T00:00:00Z",
                "--end-date",
                "2026-09-02T00:00:00Z",
                "--limit",
                "50",
                "--continuation-token",
                "tok123",
                "--order",
                "newest_first",
            ],
            captured,
        )
        assert result.exit_code == 0, result.output
        assert captured["get_path"] == f"/api/services/{_SVC_UUID}/logs"
        assert captured["get_params"] == {
            "attempt_id": _ATTEMPT_UUID,
            "levels": ["INFO", "ERROR"],
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-02T00:00:00Z",
            "limit": 50,
            "continuation_token": "tok123",
            "order": "newest_first",
        }

    def test_continuation_token_pages_without_other_filters(self) -> None:
        """A bare ref + token is the whole load-older paging contract."""
        captured: dict = {}
        result = _invoke(["logs", _SVC_UUID, "--continuation-token", "tok9"], captured)
        assert result.exit_code == 0, result.output
        assert captured["get_params"] == {"continuation_token": "tok9"}

    def test_invalid_order_rejected(self) -> None:
        captured: dict = {}
        result = _invoke(["logs", _SVC_UUID, "--order", "sideways"], captured)
        assert result.exit_code == 2
        assert "get_path" not in captured


class TestFriendlyErrors:
    def test_unknown_ref_exits_2(self) -> None:
        client = _make_mock_client({})
        with (
            mock.patch(
                "bifrost.client.BifrostClient.get_instance", return_value=client
            ),
            mock.patch(
                "bifrost.refs.RefResolver.resolve",
                new=lambda self, kind, ref: _async_raise(
                    RefNotFoundError(kind, ref)
                ),
            ),
        ):
            result = CliRunner().invoke(services_group, ["get", "nope"])
        assert result.exit_code == 2
        assert "Could not find service" in result.output

    def test_server_404_exits_1_with_body(self) -> None:
        async def not_found(path, **kwargs):  # type: ignore[no-untyped-def]
            return _fake_response({"detail": "Service 'x' not found"}, status=404)

        client = _make_mock_client({})
        client.get = not_found
        with (
            mock.patch(
                "bifrost.client.BifrostClient.get_instance", return_value=client
            ),
            mock.patch(
                "bifrost.refs.RefResolver.resolve",
                new=lambda self, kind, ref: _async_value(_SVC_UUID),
            ),
        ):
            result = CliRunner().invoke(services_group, ["get", _SVC_UUID])
        assert result.exit_code == 1


class _FakeListClient:
    """Minimal client stub exposing ``async get`` for the real resolver."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def get(self, path: str, **kwargs: object) -> httpx.Response:  # type: ignore[no-untyped-def]
        self.calls.append(path)
        return _fake_response(self.payload)


def _service_item(
    uuid: str, workflow_name: str, org: str | None = None
) -> dict:
    return {"id": uuid, "workflow_name": workflow_name, "organization_id": org}


class TestServiceRefResolver:
    @pytest.mark.asyncio
    async def test_uuid_passes_through_without_network(self) -> None:
        client = _FakeListClient({"items": [], "total": 0})
        resolved = await resolve_ref(client, "service", _SVC_UUID)  # type: ignore[arg-type]
        assert resolved == _SVC_UUID
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_workflow_name_lookup(self) -> None:
        client = _FakeListClient(
            {"items": [_service_item(_SVC_UUID, "nightly-sync")], "total": 1}
        )
        resolved = await resolve_ref(client, "service", "nightly-sync")  # type: ignore[arg-type]
        assert resolved == _SVC_UUID

    @pytest.mark.asyncio
    async def test_unknown_name_raises_not_found(self) -> None:
        client = _FakeListClient({"items": [], "total": 0})
        with pytest.raises(RefNotFoundError):
            await resolve_ref(client, "service", "ghost")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_duplicate_workflow_name_is_ambiguous(self) -> None:
        other = "33333333-3333-3333-3333-333333333333"
        client = _FakeListClient(
            {
                "items": [
                    _service_item(_SVC_UUID, "dup", "org-a"),
                    _service_item(other, "dup", "org-b"),
                ],
                "total": 2,
            }
        )
        with pytest.raises(AmbiguousRefError):
            await resolve_ref(client, "service", "dup")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_name_past_default_page_limit_still_resolves(self) -> None:
        """The resolver must request the max page, not the server default.

        Regression test: ``GET /api/services`` defaults to ``limit=100``,
        so a name-only fetch missed definitions past item 100 (and saw an
        incomplete candidate set for ambiguity detection). The resolver
        now passes ``limit=1000`` — the endpoint max.
        """

        seen: list[dict] = []

        class _RecordingClient(_FakeListClient):
            async def get(self, path: str, **kwargs: object) -> httpx.Response:  # type: ignore[no-untyped-def]
                seen.append({"path": path, **kwargs})
                return await super().get(path, **kwargs)

        items = [
            _service_item(f"00000000-0000-4000-8000-{i:012d}", f"svc-{i:03d}")
            for i in range(149)
        ]
        target = _service_item(_SVC_UUID, "deep-target")
        items.insert(120, target)  # past the server's default limit=100
        client = _RecordingClient({"items": items, "total": 150})

        resolved = await resolve_ref(client, "service", "deep-target")  # type: ignore[arg-type]

        assert resolved == _SVC_UUID
        assert seen[0].get("params") == {"limit": 1000}
