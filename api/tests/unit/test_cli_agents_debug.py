"""Unit tests for ``bifrost agents run-tree/run-timeline/...`` debugger reads."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands.agents import agents_group


class _FakeDebuggerClient:
    """Route canned JSON by request path; record calls for assertions."""

    def __init__(self, routes: dict[str, Any], *, status: int = 200) -> None:
        self.api_url = "http://test.local"
        self._access_token = "test-token"
        self.routes = routes
        self.status = status
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _response(self, method: str, path: str, body: Any) -> httpx.Response:
        request = httpx.Request(method, f"{self.api_url}{path}")
        return httpx.Response(self.status, json=body, request=request)

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        self.calls.append(("GET", path, params))
        route = self.routes.get(path)
        if isinstance(route, BaseException):
            raise route
        return self._response("GET", path, self.routes.get(path, {}))


@pytest.fixture
def _patch_client(monkeypatch: pytest.MonkeyPatch):
    def _install(client: _FakeDebuggerClient) -> _FakeDebuggerClient:
        monkeypatch.setattr(
            bifrost_client_module.BifrostClient,
            "get_instance",
            classmethod(lambda cls, require_auth=False: client),
        )
        return client

    return _install


def _tree_body() -> dict[str, Any]:
    root_id = str(uuid4())
    return {
        "requested_run_id": root_id,
        "root_run_id": root_id,
        "root": {
            "run_id": root_id,
            "agent_name": "coordinator",
            "status": "waiting_child",
            "depth": 0,
            "attempt": 0,
            "diagnostic": None,
            "children": [
                {
                    "run_id": str(uuid4()),
                    "agent_name": "specialist",
                    "status": "failed",
                    "depth": 1,
                    "attempt": 1,
                    "diagnostic": None,
                    "children": [],
                }
            ],
        },
        "total_runs": 2,
        "truncated": False,
    }


def test_run_tree_requests_tree_url_and_renders_status(_patch_client) -> None:
    run_id = str(uuid4())
    body = _tree_body()
    fake = _patch_client(_FakeDebuggerClient({f"/api/agent-runs/{run_id}/tree": body}))

    result = CliRunner().invoke(agents_group, ["run-tree", run_id])

    assert result.exit_code == 0, result.output
    assert fake.calls == [("GET", f"/api/agent-runs/{run_id}/tree", None)]
    assert "[waiting_child]" in result.output
    assert "coordinator" in result.output
    assert "[failed]" in result.output
    assert "specialist" in result.output


def test_run_tree_json_preserves_contract(_patch_client) -> None:
    import json as jsonlib

    run_id = str(uuid4())
    body = _tree_body()
    _patch_client(_FakeDebuggerClient({f"/api/agent-runs/{run_id}/tree": body}))

    result = CliRunner().invoke(agents_group, ["--json", "run-tree", run_id])

    assert result.exit_code == 0, result.output
    assert jsonlib.loads(result.output)["total_runs"] == 2


def test_run_timeline_passes_filters_and_paginates(_patch_client) -> None:
    run_id = str(uuid4())
    page = {
        "run_id": run_id,
        "entries": [
            {
                "sequence": 3,
                "kind": "tool_result",
                "run_id": run_id,
                "summary": "tool search completed",
                "detail": {"invocation_state": "uncertain", "uncertain": True},
            }
        ],
        "next_cursor": "cursor-abc",
    }
    fake = _patch_client(
        _FakeDebuggerClient({f"/api/agent-runs/{run_id}/timeline": page})
    )

    result = CliRunner().invoke(
        agents_group,
        ["run-timeline", run_id, "--kind", "tool_result", "--attempt", "2",
         "--limit", "10", "--cursor", "cursor-0"],
    )

    assert result.exit_code == 0, result.output
    assert fake.calls == [
        (
            "GET",
            f"/api/agent-runs/{run_id}/timeline",
            {"limit": 10, "cursor": "cursor-0", "kind": "tool_result",
             "attempt": 2},
        )
    ]
    assert "#3 [tool_result] tool search completed" in result.output
    assert "uncertain" in result.output
    assert "next cursor: cursor-abc" in result.output


def test_run_timeline_follow_exits_on_terminal_state(_patch_client) -> None:
    run_id = str(uuid4())
    entry = {
        "sequence": 1,
        "kind": "completion",
        "run_id": run_id,
        "summary": "run completed",
        "detail": {},
    }
    fake = _patch_client(
        _FakeDebuggerClient(
            {
                f"/api/agent-runs/{run_id}/timeline": {
                    "run_id": run_id,
                    "entries": [entry],
                    "next_cursor": None,
                },
                f"/api/agent-runs/{run_id}/snapshot": {
                    "run_id": run_id,
                    "status": "completed",
                },
            }
        )
    )

    result = CliRunner().invoke(
        agents_group,
        ["run-timeline", run_id, "--follow", "--poll-interval", "0",
         "--max-polls", "5"],
    )

    assert result.exit_code == 0, result.output
    assert "#1 [completion] run completed" in result.output
    assert "run completed" in result.output
    timeline_calls = [c for c in fake.calls if c[1].endswith("/timeline")]
    assert len(timeline_calls) == 1


def test_run_timeline_follow_respects_poll_budget(_patch_client) -> None:
    run_id = str(uuid4())
    fake = _patch_client(
        _FakeDebuggerClient(
            {
                f"/api/agent-runs/{run_id}/timeline": {
                    "run_id": run_id,
                    "entries": [],
                    "next_cursor": None,
                },
                f"/api/agent-runs/{run_id}/snapshot": {
                    "run_id": run_id,
                    "status": "running",
                },
            }
        )
    )

    result = CliRunner().invoke(
        agents_group,
        ["run-timeline", run_id, "--follow", "--poll-interval", "0",
         "--max-polls", "3"],
    )

    assert result.exit_code == 0, result.output
    assert "poll budget exhausted" in result.output
    assert len([c for c in fake.calls if c[1].endswith("/timeline")]) == 3


def test_run_timeline_follow_interrupted(_patch_client) -> None:
    run_id = str(uuid4())
    fake = _patch_client(
        _FakeDebuggerClient(
            {
                f"/api/agent-runs/{run_id}/timeline": KeyboardInterrupt(),
            }
        )
    )

    result = CliRunner().invoke(
        agents_group,
        ["run-timeline", run_id, "--follow", "--poll-interval", "0"],
    )

    assert result.exit_code == 0, result.output
    assert "follow interrupted" in result.output
    assert fake.calls, "expected at least one timeline poll"


def test_run_snapshot_shows_lease_and_contract_error(_patch_client) -> None:
    run_id = str(uuid4())
    body = {
        "run_id": run_id,
        "agent_id": str(uuid4()),
        "agent_name": "coordinator",
        "snapshot_version": 1,
        "model": {"provider": "anthropic", "model": "m", "profile_id": None,
                   "llm_max_tokens": None},
        "tool_names": ["search"],
        "delegated_agents": [],
        "system_tools": ["sleep_until"],
        "limits": {"max_iterations": 5},
        "correlation": {},
        "status": "waiting_child",
        "attempt": 1,
        "checkpoint_sequence": 2,
        "wake_at": "2026-09-19T00:00:00+00:00",
        "lease": {"owner": "worker-1", "expires_at": None,
                  "last_progress_at": None},
        "usage": {"iterations_used": 3, "tokens_used": 100, "duration_ms": 5},
        "contract": {"valid": False, "errors": ["missing field"]},
        "completion_event": {"pending_at": None, "emitted_at": None,
                             "attempts": 0, "last_error": None},
    }
    fake = _patch_client(
        _FakeDebuggerClient({f"/api/agent-runs/{run_id}/snapshot": body})
    )

    result = CliRunner().invoke(agents_group, ["run-snapshot", run_id])

    assert result.exit_code == 0, result.output
    assert fake.calls == [("GET", f"/api/agent-runs/{run_id}/snapshot", None)]
    assert "status: waiting_child" in result.output
    assert "worker-1" in result.output
    assert "wake_at" in result.output
    assert "contract_failed" in result.output


def test_run_checkpoints_requests_url_with_cursor(_patch_client) -> None:
    run_id = str(uuid4())
    body = {
        "run_id": run_id,
        "checkpoints": [
            {"sequence": 2, "created_at": "2026-09-18T12:00:00+00:00",
             "message_count": 4, "has_pending_tool_calls": False,
             "has_pending_join": True, "has_pending_timer": False}
        ],
        "next_cursor": None,
    }
    fake = _patch_client(
        _FakeDebuggerClient({f"/api/agent-runs/{run_id}/checkpoints": body})
    )

    result = CliRunner().invoke(
        agents_group, ["run-checkpoints", run_id, "--cursor", "cursor-9"]
    )

    assert result.exit_code == 0, result.output
    assert fake.calls == [
        ("GET", f"/api/agent-runs/{run_id}/checkpoints",
         {"limit": 50, "cursor": "cursor-9"})
    ]
    assert "#2" in result.output
    assert "pending_join" in result.output


def test_debugger_http_error_exits_nonzero(_patch_client) -> None:
    run_id = str(uuid4())

    class _ErrorClient(_FakeDebuggerClient):
        async def get(
            self,
            path: str,
            *,
            params: dict[str, Any] | None = None,
        ) -> httpx.Response:
            self.calls.append(("GET", path, params))
            request = httpx.Request("GET", f"{self.api_url}{path}")
            response = httpx.Response(
                404, json={"detail": "not found"}, request=request
            )
            response.raise_for_status()
            raise AssertionError("unreachable")

    _patch_client(_ErrorClient({}))

    result = CliRunner().invoke(agents_group, ["run-tree", run_id])

    assert result.exit_code != 0
