"""Studio CLI tests: request shapes, @file loaders, wait wording, exit codes."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

import bifrost.client as client_mod
from bifrost.commands.agent_tests import agent_tests_group


class _FakeResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.get_payload: Any = {}
        self.post_payload: Any = {}
        self.job_polls: list[dict[str, Any]] = []

    async def get(self, path: str, params: dict | None = None) -> _FakeResponse:
        self.calls.append(("GET", path, params))
        if path.startswith("/api/platform-jobs/"):
            body = (
                self.job_polls.pop(0)
                if self.job_polls
                else {"status": "succeeded", "result": {}}
            )
            return _FakeResponse(body)
        return _FakeResponse(self.get_payload)

    async def post(self, path: str, json: Any = None) -> _FakeResponse:
        self.calls.append(("POST", path, json))
        return _FakeResponse(
            self.post_payload,
            status_code=202 if path.endswith("/executions") else 200,
            headers=getattr(self, "post_headers", {}),
        )

    async def put(self, path: str, json: Any = None) -> _FakeResponse:
        self.calls.append(("PUT", path, json))
        return _FakeResponse(self.post_payload)

    async def delete(self, path: str) -> _FakeResponse:
        self.calls.append(("DELETE", path, None))
        return _FakeResponse({})


@pytest.fixture
def fake_client(monkeypatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **_kwargs: fake),
    )
    return fake


@pytest.fixture
def fake_resolver(monkeypatch):
    class _FakeResolver:
        def __init__(self, client) -> None:
            self._client = client

        async def resolve(self, kind: str, value: str) -> str:
            return f"uuid-{kind}-{value}"

    monkeypatch.setattr(
        "bifrost.commands.base.RefResolver",
        _FakeResolver,
    )
    return _FakeResolver


def _invoke(args: list[str]):
    return CliRunner().invoke(agent_tests_group, args)


def test_suites_list_and_get_request_shapes(fake_client):
    fake_client.get_payload = [{"id": "s1", "name": "nightly"}]
    result = _invoke(["suites-list"])
    assert result.exit_code == 0, result.output
    assert ("GET", "/api/agent-evaluations/suites", None) in fake_client.calls

    result = _invoke(["suites-get", "s1"])
    assert result.exit_code == 0, result.output
    assert ("GET", "/api/agent-evaluations/suites/s1", None) in fake_client.calls


def test_suites_create_resolves_agent_ref(fake_client, fake_resolver):
    fake_client.post_payload = {"id": "s1", "name": "nightly"}
    result = _invoke(["suites-create", "--name", "nightly", "--agent", "support"])
    assert result.exit_code == 0, result.output
    assert fake_client.calls[0][1] == "/api/agent-evaluations/suites"
    assert fake_client.calls[0][2]["agent_id"] == "uuid-agent-support"


def test_cases_create_loads_at_file_payloads(fake_client, tmp_path):
    fixture_file = tmp_path / "fixture.json"
    fixture_file.write_text(json.dumps({"entities": {}, "allowed_tools": ["get_ticket"]}))
    assertions_file = tmp_path / "assertions.yaml"
    assertions_file.write_text("- type: no_real_tools\n  params: {}\n")
    fake_client.post_payload = {"id": "c1", "name": "greeting"}
    result = _invoke(
        [
            "cases-create", "suite-1", "--name", "greeting",
            "--fixture", f"@{fixture_file}",
            "--assertions", f"@{assertions_file}",
            "--expected-tools", "get_ticket",
        ]
    )
    assert result.exit_code == 0, result.output
    body = fake_client.calls[0][2]
    assert body["fixture"]["allowed_tools"] == ["get_ticket"]
    assert body["assertions"] == [{"type": "no_real_tools", "params": {}}]
    assert body["expected_tools"] == ["get_ticket"]


def test_cases_create_rejects_non_array_assertions(fake_client):
    result = _invoke(
        ["cases-create", "suite-1", "--name", "x",
         "--fixture", '{"entities": {}}', "--assertions", '{"type": "x"}']
    )
    assert result.exit_code != 0


def test_candidates_create_overlays_and_prompt_file(fake_client, fake_resolver, tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Be brief.")
    fake_client.post_payload = {"id": "cand-1", "evaluation_only": True}
    result = _invoke(
        [
            "candidates-create", "--agent", "support",
            "--overlays", '{"max_iterations": 3}',
            "--system-prompt", f"@{prompt_file}",
        ]
    )
    assert result.exit_code == 0, result.output
    body = fake_client.calls[0][2]
    assert body["base_agent_id"] == "uuid-agent-support"
    assert body["overlays"]["system_prompt"] == "Be brief."
    assert body["overlays"]["max_iterations"] == 3


def test_run_without_wait_prints_accepted_job(fake_client):
    fake_client.post_payload = {"job_id": "job-1", "status": "queued", "reused": False}
    fake_client.post_headers = {"X-Evaluation-Execution-Id": "exec-1"}
    result = _invoke(["run", "--suite", "suite-1"])
    assert result.exit_code == 0, result.output
    assert "job-1" in result.output
    assert "exec-1" in result.output


def test_run_wait_polls_shared_job_and_reports_deadline(fake_client, monkeypatch):
    fake_client.post_payload = {"job_id": "job-1", "status": "queued", "reused": False}
    fake_client.post_headers = {"X-Evaluation-Execution-Id": "exec-1"}
    fake_client.job_polls = [
        {"status": "running", "progress": {"phase": "Dispatching", "current": 0, "total": 4}},
        {"status": "succeeded", "result": {"passed": 4}},
    ]
    monkeypatch.setattr("bifrost.platform_jobs.asyncio.sleep", AsyncMock())
    result = _invoke(["run", "--suite", "suite-1", "--wait", "--timeout", "600"])
    assert result.exit_code == 0, result.output
    assert ("GET", "/api/platform-jobs/job-1", None) in fake_client.calls


def test_run_wait_timeout_wording_says_server_continues(fake_client, monkeypatch):
    import bifrost.platform_jobs as platform_jobs_mod

    fake_client.post_payload = {"job_id": "job-1", "status": "queued", "reused": False}
    fake_client.post_headers = {}
    fake_client.job_polls = [{"status": "running"}] * 100
    monkeypatch.setattr(platform_jobs_mod.asyncio, "sleep", AsyncMock())
    ticks = iter([0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(
        platform_jobs_mod.time, "monotonic", lambda: next(ticks, 100.0)
    )
    result = _invoke(["run", "--suite", "suite-1", "--wait", "--timeout", "1"])
    assert result.exit_code != 0
    assert "may still be running" in result.output


def test_compare_summarizes_regressions_and_runs(fake_client):
    fake_client.get_payload = [
        {
            "case_id": "case-1",
            "status": "failed",
            "baseline_run_id": "run-b",
            "candidate_run_id": "run-c",
            "comparison": {
                "verdict": "regression",
                "regressions": ["terminal_status"],
                "usage_delta": {"tokens": {"delta": 50}},
            },
        },
        {
            "case_id": "case-2",
            "status": "passed",
            "baseline_run_id": "run-b2",
            "candidate_run_id": "run-c2",
            "comparison": {"verdict": "unchanged"},
        },
    ]
    result = _invoke(["compare", "exec-1"])
    assert result.exit_code == 0, result.output
    assert "regressions" in result.output
    assert "run-b" in result.output or "case-1" in result.output


def test_status_cancel_results_shapes(fake_client):
    fake_client.get_payload = {"id": "exec-1", "status": "running"}
    result = _invoke(["status", "exec-1"])
    assert result.exit_code == 0, result.output
    assert ("GET", "/api/agent-evaluations/executions/exec-1/results", None) in fake_client.calls

    fake_client.post_payload = {"id": "exec-1", "status": "cancelled"}
    result = _invoke(["cancel", "exec-1"])
    assert result.exit_code == 0, result.output
    assert ("POST", "/api/agent-evaluations/executions/exec-1/cancel", None) in fake_client.calls

    result = _invoke(["designer-accept", "suite-1", "draft-1"])
    assert result.exit_code == 0, result.output
