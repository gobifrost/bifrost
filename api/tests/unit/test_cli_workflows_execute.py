"""Unit tests for ``bifrost workflows execute``.

Mocks ``BifrostClient`` and the websockets stream so the CLI is exercised
end-to-end (POST → WS log tail → final GET) without a live platform.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands.workflows import workflows_group


_WORKFLOW_UUID = "11111111-2222-3333-4444-555555555555"


class _FakeClient:
    def __init__(self) -> None:
        self.api_url = "http://test.local"
        self._access_token = "test-token"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses: dict[tuple[str, str], list[tuple[int, Any]]] = {}

    def queue(self, method: str, path: str, status: int, body: Any) -> None:
        self._responses.setdefault((method, path), []).append((status, body))

    def _next(self, method: str, path: str) -> tuple[int, Any]:
        queue = self._responses.get((method, path), [])
        if not queue:
            raise AssertionError(f"Unexpected {method} {path}")
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)

    def _build(self, method: str, path: str) -> httpx.Response:
        status, body = self._next(method, path)
        request = httpx.Request(method, f"{self.api_url}{path}")
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body, request=request)
        return httpx.Response(status, text=str(body), request=request)

    async def get(self, path: str, **_kwargs) -> httpx.Response:
        self.calls.append(("GET", path, None))
        return self._build("GET", path)

    async def post(
        self, path: str, *, json: dict | None = None, **_kwargs
    ) -> httpx.Response:
        self.calls.append(("POST", path, json))
        return self._build("POST", path)


class _FakeWebSocket:
    """Simulates the WS connection by yielding pre-canned JSON messages."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = list(messages)

    async def __aenter__(self) -> "_FakeWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def recv(self) -> str:
        if not self._messages:
            raise AssertionError(
                "Test ran out of WS messages — execute should have exited "
                "after the terminal execution_update"
            )
        return json.dumps(self._messages.pop(0))


def _ws_connect_factory(messages: list[dict[str, Any]]):
    captured: dict[str, Any] = {}

    def _connect(uri: str, *, additional_headers=None, **_kwargs):
        captured["uri"] = uri
        captured["headers"] = dict(additional_headers or {})
        return _FakeWebSocket(messages)

    return _connect, captured


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )
    return fake


@pytest.fixture
def stub_resolver(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Make resolver.resolve() return whatever the test pre-registered.

    Defaults to mapping {"my-workflow": _WORKFLOW_UUID}.
    """
    mapping = {"my-workflow": _WORKFLOW_UUID}

    async def _resolve(self, kind: str, value: str) -> str:
        if value in mapping:
            return mapping[value]
        return value

    from bifrost import refs as refs_module
    monkeypatch.setattr(refs_module.RefResolver, "resolve", _resolve)
    return mapping


def _invoke(args: list[str]):
    return CliRunner().invoke(
        workflows_group, args, standalone_mode=False, catch_exceptions=False
    )


class TestWorkflowsExecute:
    def test_streams_logs_then_exits_on_terminal_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: _FakeClient,
        stub_resolver: dict[str, str],
    ) -> None:
        # POST returns immediately with PENDING status + execution_id.
        fake_client.queue(
            "POST",
            "/api/workflows/execute",
            200,
            {"execution_id": "exec-1", "status": "Pending"},
        )
        # Final GET returns the completed record.
        fake_client.queue(
            "GET",
            "/api/executions/exec-1",
            200,
            {
                "execution_id": "exec-1",
                "status": "Success",
                "result": {"answer": 42},
                "duration_ms": 150,
            },
        )

        ws_messages = [
            {"type": "connected", "executionId": "exec-1"},
            {
                "type": "execution_log",
                "executionId": "exec-1",
                "level": "info",
                "message": "Starting work",
            },
            {
                "type": "execution_log",
                "executionId": "exec-1",
                "level": "info",
                "message": "Done",
            },
            {
                "type": "execution_update",
                "executionId": "exec-1",
                "status": "Success",
            },
        ]
        connect, captured = _ws_connect_factory(ws_messages)
        # Patch the lazy import target inside _stream_execution_logs.
        import websockets.asyncio.client as ws_client_module
        monkeypatch.setattr(ws_client_module, "connect", connect)

        result = _invoke(["--json", "execute", "my-workflow"])

        assert result.exit_code == 0, (result.output, result.stderr)
        # POST was called with sync=False and resolved UUID.
        post_call = next(c for c in fake_client.calls if c[0] == "POST")
        assert post_call[2] == {
            "workflow_id": _WORKFLOW_UUID,
            "input_data": {},
            "sync": False,
        }
        # WS URL is correctly derived from api_url.
        assert captured["uri"] == "ws://test.local/ws/execution/exec-1"
        assert captured["headers"]["Authorization"] == "Bearer test-token"
        # Final result payload was emitted as JSON. The execute command also
        # writes ``[INFO] ...`` lines for log messages on stdout above the
        # JSON body, so split on the first ``{`` at column 0 to isolate the
        # JSON block.
        stdout = result.stdout
        json_start = stdout.find("\n{")
        assert json_start != -1, f"no JSON block found in stdout: {stdout!r}"
        payload = json.loads(stdout[json_start + 1:])
        assert payload["status"] == "Success"
        assert payload["result"] == {"answer": 42}
        # Logs surfaced on stdout (between execution_id stderr and JSON body).
        assert "[INFO] Starting work" in result.output
        assert "[INFO] Done" in result.output

    def test_failure_status_returns_exit_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: _FakeClient,
        stub_resolver: dict[str, str],
    ) -> None:
        fake_client.queue(
            "POST",
            "/api/workflows/execute",
            200,
            {"execution_id": "exec-2", "status": "Pending"},
        )
        fake_client.queue(
            "GET",
            "/api/executions/exec-2",
            200,
            {
                "execution_id": "exec-2",
                "status": "Failed",
                "error": "boom",
            },
        )
        ws_messages = [
            {
                "type": "execution_update",
                "executionId": "exec-2",
                "status": "Failed",
            },
        ]
        connect, _ = _ws_connect_factory(ws_messages)
        import websockets.asyncio.client as ws_client_module
        monkeypatch.setattr(ws_client_module, "connect", connect)

        result = _invoke(["--json", "execute", "my-workflow"])
        assert result.exit_code == 1, result.output

    def test_terminal_status_in_initial_post_skips_websocket(
        self,
        fake_client: _FakeClient,
        stub_resolver: dict[str, str],
    ) -> None:
        # If the platform returned a terminal status synchronously (sync=True
        # was overridden, or the workflow ran inline), we should not even
        # attempt to open a WebSocket.
        fake_client.queue(
            "POST",
            "/api/workflows/execute",
            200,
            {
                "execution_id": "exec-3",
                "status": "Success",
                "result": {"x": 1},
            },
        )
        result = _invoke(["--json", "execute", "my-workflow"])
        assert result.exit_code == 0, result.output
        # No GET happened — the inline result was used.
        methods = [c[0] for c in fake_client.calls]
        assert methods == ["POST"]
        payload = json.loads(result.stdout)
        assert payload["status"] == "Success"

    def test_invalid_params_json_is_usage_error(
        self,
        fake_client: _FakeClient,
        stub_resolver: dict[str, str],
    ) -> None:
        # standalone_mode=False re-raises UsageError instead of converting it
        # to an exit code, so we use the default standalone_mode for this case.
        result = CliRunner().invoke(
            workflows_group, ["execute", "my-workflow", "--params", "{not json}"]
        )
        assert result.exit_code != 0
        # UsageError text lands on stderr; CliRunner mixes it into output.
        assert "not valid JSON" in result.output

    def test_params_and_params_file_are_mutually_exclusive(
        self,
        fake_client: _FakeClient,
        stub_resolver: dict[str, str],
        tmp_path,
    ) -> None:
        params_file = tmp_path / "p.json"
        params_file.write_text("{}")
        result = CliRunner().invoke(
            workflows_group,
            [
                "execute",
                "my-workflow",
                "--params",
                "{}",
                "--params-file",
                str(params_file),
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output
