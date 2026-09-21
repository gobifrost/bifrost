"""Agent review CLI local contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost import refs as refs_module
from bifrost.commands import agent_reviews as agent_reviews_module
from bifrost.commands.agent_reviews import _csv_uuid_values, agent_reviews_group
from shared.models import AgentReviewDefinitionCreate, AgentReviewRunCreate, AgentReviewVersionCreate

_AGENT_ID = "11111111-1111-1111-1111-111111111111"
_REVIEW_ID = "22222222-2222-2222-2222-222222222222"
_RUN_ID = "33333333-3333-3333-3333-333333333333"
_REVIEW_RUN_ID = "44444444-4444-4444-4444-444444444444"
_JOB_ID = "55555555-5555-5555-5555-555555555555"


class _FakeResponse:
    def __init__(self, status: int, body: Any, *, headers: dict[str, str] | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.request = httpx.Request("GET", "http://test.local")

    @property
    def text(self) -> str:
        return str(self._body)

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=httpx.Response(self.status_code))


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.post_responses: list[_FakeResponse] = []
        self.get_responses: list[_FakeResponse] = []
        self.patch_responses: list[_FakeResponse] = []

    async def post(self, path: str, *, json: dict[str, Any] | None = None, **_kwargs):
        self.calls.append(("POST", path, json))
        if not self.post_responses:
            raise AssertionError(f"Unexpected POST {path}")
        return self.post_responses.pop(0)

    async def get(self, path: str, **_kwargs):
        self.calls.append(("GET", path, _kwargs.get("params")))
        if not self.get_responses:
            raise AssertionError(f"Unexpected GET {path}")
        return self.get_responses.pop(0)

    async def patch(self, path: str, *, json: dict[str, Any] | None = None, **_kwargs):
        self.calls.append(("PATCH", path, json))
        if not self.patch_responses:
            raise AssertionError(f"Unexpected PATCH {path}")
        return self.patch_responses.pop(0)


@pytest.fixture
def review_cli(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
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
    return CliRunner().invoke(agent_reviews_group, args, catch_exceptions=True)


def _accepted() -> _FakeResponse:
    return _FakeResponse(202, {"review_run_id": _REVIEW_RUN_ID, "job_id": _JOB_ID, "reused": False, "notification_id": None})


def test_agent_reviews_cli_declares_request_dto_fields():
    create_fields = {"agent_id", "organization_id", "name", "review_statement", "evidence_format_instructions", "model_profile_id"}
    version_fields = {"review_statement", "evidence_format_instructions", "model_profile_id"}
    run_fields = {"run_ids"}
    assert set(AgentReviewDefinitionCreate.model_fields) == create_fields
    assert set(AgentReviewVersionCreate.model_fields) == version_fields
    assert set(AgentReviewRunCreate.model_fields) == run_fields


def test_review_create_payload_from_flags_and_file(review_cli: _FakeClient, tmp_path: Path):
    body_file = tmp_path / "review.yaml"
    body_file.write_text("name: from-file\nreview_statement: old\n", encoding="utf-8")
    review_cli.post_responses.append(_FakeResponse(201, {"id": _REVIEW_ID}))

    result = _invoke([
        "--json",
        "create",
        "--file", str(body_file),
        "--agent", "my-agent",
        "--name", "from-flags",
        "--statement", "Check the run.",
        "--evidence-format", "Cite evidence.",
        "--org-id", "global",
    ])

    assert result.exit_code == 0, result.output
    assert review_cli.calls[0] == ("POST", "/api/agent-reviews", {
        "name": "from-flags",
        "review_statement": "Check the run.",
        "agent_id": _AGENT_ID,
        "evidence_format_instructions": "Cite evidence.",
        "organization_id": None,
    })


def test_review_cli_malformed_file_is_usage_error(review_cli: _FakeClient, tmp_path: Path):
    missing = tmp_path / "missing.yaml"
    result = _invoke(["create", "--file", str(missing)])
    assert result.exit_code == 2

    bad = tmp_path / "bad.yaml"
    bad.write_text("- not\n- object\n", encoding="utf-8")
    result = _invoke(["create", "--file", str(bad)])
    assert result.exit_code == 2




def test_review_cli_local_usage_errors_are_json_stdout_and_exit_two(review_cli: _FakeClient, tmp_path: Path):
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("name: ok\nreview_statement: ok\nextra: nope\n", encoding="utf-8")
    result = _invoke(["--json", "create", "--file", str(unknown)])
    assert result.exit_code == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"] == "usage_error"
    assert "unknown field" in payload["message"]

    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("from file", encoding="utf-8")
    result = _invoke([
        "--json", "version", _REVIEW_ID,
        "--statement", "literal",
        "--statement-file", str(statement_file),
    ])
    assert result.exit_code == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"] == "usage_error"

    result = _invoke(["--json", "update", _REVIEW_ID])
    assert result.exit_code == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"] == "usage_error"


def test_review_cli_missing_required_fields_are_local_usage_errors(review_cli: _FakeClient, tmp_path: Path):
    create_file = tmp_path / "missing-create.yaml"
    create_file.write_text("name: missing agent\nreview_statement: statement\n", encoding="utf-8")
    result = _invoke(["--json", "create", "--file", str(create_file)])
    assert result.exit_code == 2
    assert "agent_id" in json.loads(result.stdout)["message"]
    assert review_cli.calls == []

    version_file = tmp_path / "missing-version.yaml"
    version_file.write_text("evidence_format_instructions: cite\n", encoding="utf-8")
    result = _invoke(["--json", "version", _REVIEW_ID, "--file", str(version_file)])
    assert result.exit_code == 2
    assert "review_statement" in json.loads(result.stdout)["message"]
    assert review_cli.calls == []

    result = _invoke(["--json", "run", _REVIEW_ID])
    assert result.exit_code == 2
    assert "run_ids" in json.loads(result.stdout)["message"]
    assert review_cli.calls == []


def test_review_cli_maps_server_validation_to_invocation_error(review_cli: _FakeClient, tmp_path: Path):
    body_file = tmp_path / "invalid-run.yaml"
    body_file.write_text("run_ids: not-a-list\n", encoding="utf-8")
    review_cli.post_responses.append(_FakeResponse(422, {"detail": "run_ids must be a list"}))

    result = _invoke(["--json", "run", _REVIEW_ID, "--file", str(body_file)])

    assert result.exit_code == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"] == "usage_error"
    assert "run_ids must be a list" in payload["message"]
    assert review_cli.calls == [("POST", f"/api/agent-reviews/{_REVIEW_ID}/runs", {"run_ids": "not-a-list"})]


def test_review_run_without_wait_outputs_parseable_accepted(review_cli: _FakeClient):
    review_cli.post_responses.append(_accepted())
    result = _invoke(["--json", "run", _REVIEW_ID, "--runs", _RUN_ID])
    assert result.exit_code == 0, result.output
    assert review_cli.calls[0] == ("POST", f"/api/agent-reviews/{_REVIEW_ID}/runs", {"run_ids": [_RUN_ID]})
    assert _REVIEW_RUN_ID in result.output


def test_review_run_rejects_malformed_csv_locally(review_cli: _FakeClient):
    with pytest.raises(click.UsageError):
        _csv_uuid_values("not-a-uuid", "--runs")
    result = _invoke(["run", _REVIEW_ID, "--runs", "not-a-uuid"])
    assert result.exit_code == 2


def test_review_run_wait_success_fetches_results(review_cli: _FakeClient, monkeypatch: pytest.MonkeyPatch):
    review_cli.post_responses.append(_accepted())
    review_cli.get_responses.append(_FakeResponse(200, {"review_run": {"id": _REVIEW_RUN_ID}, "findings": []}))

    async def _poll(*_args, **_kwargs):
        return {"id": _JOB_ID, "status": "succeeded"}

    monkeypatch.setattr(agent_reviews_module, "poll_platform_job", _poll)
    result = _invoke(["--json", "run", _REVIEW_ID, "--runs", _RUN_ID, "--wait"])
    assert result.exit_code == 0, result.output
    assert review_cli.calls[-1] == ("GET", f"/api/agent-reviews/runs/{_REVIEW_RUN_ID}/results", None)


def test_review_run_wait_failed_cancelled_and_timeout_exits(review_cli: _FakeClient, monkeypatch: pytest.MonkeyPatch):
    async def _failed(*_args, **_kwargs):
        return {"id": _JOB_ID, "status": "failed"}

    monkeypatch.setattr(agent_reviews_module, "poll_platform_job", _failed)
    review_cli.post_responses.append(_accepted())
    result = _invoke(["--json", "run", _REVIEW_ID, "--runs", _RUN_ID, "--wait"])
    assert result.exit_code == 3

    async def _timeout(*_args, **_kwargs):
        raise click.ClickException("timed out")

    monkeypatch.setattr(agent_reviews_module, "poll_platform_job", _timeout)
    review_cli.post_responses.append(_accepted())
    result = _invoke(["--json", "run", _REVIEW_ID, "--runs", _RUN_ID, "--wait"])
    assert result.exit_code == 4


def test_review_results_unavailable_exits_four(review_cli: _FakeClient):
    review_cli.get_responses.append(_FakeResponse(404, {"detail": "Review run not found."}))
    result = _invoke(["--json", "results", _REVIEW_RUN_ID])
    assert result.exit_code == 4


def test_review_usage_status_cancel_paths(review_cli: _FakeClient):
    usage = {"overall": {}, "coverage": {}, "by_purpose": {}, "by_provider_model": {}, "by_profile": {}, "by_organization": {}, "by_operation": {}}
    review_cli.get_responses.append(_FakeResponse(200, usage))
    result = _invoke(["--json", "usage", _REVIEW_RUN_ID, "--limit", "10", "--offset", "1"])
    assert result.exit_code == 0, result.output
    assert review_cli.calls[-1] == ("GET", f"/api/agent-reviews/runs/{_REVIEW_RUN_ID}/usage", {"limit": 10, "offset": 1})

    review_cli.get_responses.append(_FakeResponse(200, {"id": _JOB_ID, "status": "queued"}))
    result = _invoke(["--json", "status", _JOB_ID])
    assert result.exit_code == 0

    review_cli.post_responses.append(_FakeResponse(200, {"accepted": True}))
    result = _invoke(["--json", "cancel", _JOB_ID])
    assert result.exit_code == 0
