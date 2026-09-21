"""Agent-wide test CLI local contracts (tests-* commands)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost import refs as refs_module
from bifrost.commands.agent_tests import agent_tests_group
from shared.models import AgentTestCreate, AgentTestUpdate

_AGENT_ID = "11111111-1111-1111-1111-111111111111"
_LOGICAL_ID = "22222222-2222-2222-2222-222222222222"


class _FakeResponse:
    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body
        self.request = httpx.Request("GET", "http://test.local")
        self.text = str(body)

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=self.request, response=httpx.Response(self.status_code)
            )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.post_responses: list[_FakeResponse] = []
        self.get_responses: list[_FakeResponse] = []
        self.patch_responses: list[_FakeResponse] = []

    async def post(self, path: str, *, json: dict[str, Any] | None = None, **_kwargs):
        self.calls.append(("POST", path, json))
        if not self.post_responses:
            raise AssertionError(f"Unexpected POST {path}")
        return self.post_responses.pop(0)

    async def get(self, path: str, **kwargs):
        self.calls.append(("GET", path, kwargs.get("params")))
        if not self.get_responses:
            raise AssertionError(f"Unexpected GET {path}")
        return self.get_responses.pop(0)

    async def patch(self, path: str, *, json: dict[str, Any] | None = None, **_kwargs):
        self.calls.append(("PATCH", path, json))
        if not self.patch_responses:
            raise AssertionError(f"Unexpected PATCH {path}")
        return self.patch_responses.pop(0)


@pytest.fixture
def crud_cli(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    async def _resolve(self, kind: str, value: str) -> str:
        if kind == "agent":
            return _AGENT_ID
        return value

    monkeypatch.setattr(refs_module.RefResolver, "resolve", _resolve)
    return fake


def _invoke(args: list[str]):
    return CliRunner().invoke(agent_tests_group, args, catch_exceptions=True)


def test_agent_test_dtos_match_cli_surface():
    assert "name" in AgentTestCreate.model_fields
    assert "fixture" in AgentTestCreate.model_fields
    assert "assertions" in AgentTestCreate.model_fields
    assert "suite_id" not in AgentTestCreate.model_fields
    assert "expected_version" in AgentTestUpdate.model_fields
    assert "suite_id" not in AgentTestUpdate.model_fields


def test_tests_create_builds_body(crud_cli: _FakeClient, tmp_path=None):
    crud_cli.post_responses.append(_FakeResponse(201, {"logical_test_id": _LOGICAL_ID}))
    result = _invoke(
        [
            "tests-create",
            "my-agent",
            "--name",
            "Guards the budget",
            "--fixture",
            '{"version": 1}',
            "--assertions",
            '[{"type": "forbids_tool"}]',
            "--expected-tools",
            "lookup, answer",
            "--repetitions",
            "2",
        ]
    )
    assert result.exit_code == 0, result.output
    assert crud_cli.calls[0] == (
        "POST",
        f"/api/agent-evaluations/agents/{_AGENT_ID}/tests",
        {
            "name": "Guards the budget",
            "repetitions": 2,
            "fixture": {"version": 1},
            "assertions": [{"type": "forbids_tool"}],
            "expected_tools": ["lookup", "answer"],
        },
    )


def test_tests_list_get_paths(crud_cli: _FakeClient):
    crud_cli.get_responses.append(
        _FakeResponse(200, {"items": [], "total": 0, "limit": 100, "offset": 0})
    )
    result = _invoke(["tests-list", "my-agent", "--limit", "10"])
    assert result.exit_code == 0, result.output
    assert crud_cli.calls[-1] == (
        "GET",
        f"/api/agent-evaluations/agents/{_AGENT_ID}/tests",
        {"limit": 10, "offset": 0},
    )

    crud_cli.get_responses.append(_FakeResponse(200, {"logical_test_id": _LOGICAL_ID}))
    result = _invoke(["tests-get", "my-agent", _LOGICAL_ID])
    assert result.exit_code == 0, result.output
    assert crud_cli.calls[-1] == (
        "GET",
        f"/api/agent-evaluations/agents/{_AGENT_ID}/tests/{_LOGICAL_ID}",
        None,
    )


def test_tests_edit_builds_body_and_rejects_empty(crud_cli: _FakeClient):
    crud_cli.patch_responses.append(
        _FakeResponse(200, {"logical_test_id": _LOGICAL_ID, "version": 2})
    )
    result = _invoke(
        [
            "tests-edit",
            "my-agent",
            _LOGICAL_ID,
            "--name",
            "Renamed",
            "--disable",
            "--expected-version",
            "1",
        ]
    )
    assert result.exit_code == 0, result.output
    assert crud_cli.calls[-1] == (
        "PATCH",
        f"/api/agent-evaluations/agents/{_AGENT_ID}/tests/{_LOGICAL_ID}",
        {"name": "Renamed", "expected_version": 1, "enabled": False},
    )

    result = _invoke(["tests-edit", "my-agent", _LOGICAL_ID])
    assert result.exit_code == 2
    # Usage error raised before any HTTP call.
    assert len(crud_cli.calls) == 1


def test_tests_run_builds_selections_and_rejects_bad_specs(crud_cli: _FakeClient):
    crud_cli.post_responses.append(
        _FakeResponse(202, {"job_id": "job-1", "reused": False})
    )
    result = _invoke(
        [
            "tests-run",
            "my-agent",
            "--tests",
            f"{_LOGICAL_ID}:2",
            "--repetitions",
            "3",
        ]
    )
    assert result.exit_code == 0, result.output
    assert crud_cli.calls[-1] == (
        "POST",
        f"/api/agent-evaluations/agents/{_AGENT_ID}/tests/run",
        {
            "selections": [{"case_id": _LOGICAL_ID, "case_version": 2}],
            "repetitions_override": 3,
        },
    )

    for bad in ["no-colon-here", f"{_LOGICAL_ID}:0", f"{_LOGICAL_ID}:x", ""]:
        result = _invoke(["tests-run", "my-agent", "--tests", bad or " "])
        assert result.exit_code == 2, bad
    for bad in [f"{_LOGICAL_ID}:1,", f"{_LOGICAL_ID}:1,,{_LOGICAL_ID}:1"]:
        result = _invoke(["tests-run", "my-agent", "--tests", bad])
        assert result.exit_code == 2, bad
    # No HTTP calls for any invalid spec.
    assert len(crud_cli.calls) == 1


def test_tests_results_path(crud_cli: _FakeClient):
    crud_cli.get_responses.append(
        _FakeResponse(200, {"items": [], "total": 0, "limit": 100, "offset": 0})
    )
    result = _invoke(["tests-results", "my-agent"])
    assert result.exit_code == 0, result.output
    assert crud_cli.calls[-1] == (
        "GET",
        f"/api/agent-evaluations/agents/{_AGENT_ID}/tests/latest",
        {"limit": 100, "offset": 0},
    )


def test_tests_generate_builds_body(crud_cli: _FakeClient):
    crud_cli.post_responses.append(
        _FakeResponse(200, {"run_id": "run-1", "status": "queued"})
    )
    result = _invoke(
        [
            "tests-generate",
            "my-agent",
            "--findings",
            f"{_LOGICAL_ID}",
            "--count",
            "2",
            "--goal",
            "Cover the incident",
        ]
    )
    assert result.exit_code == 0, result.output
    assert crud_cli.calls[-1] == (
        "POST",
        f"/api/agent-evaluations/agents/{_AGENT_ID}/tests/generate",
        {
            "finding_ids": [_LOGICAL_ID],
            "requested_count": 2,
            "suite_goal": "Cover the incident",
        },
    )
