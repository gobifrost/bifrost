"""Recorded evaluation CLI local contracts."""

from __future__ import annotations

from uuid import uuid4

import click
import pytest

import json
from typing import Any

import httpx
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands import agent_tests as agent_tests_module
from bifrost.commands.agent_tests import agent_tests_group

from bifrost.commands.agent_tests import _csv_uuid_values, _verdict_exit_code
from shared.models import QualityUsageBreakdownResponse, RecordedEvaluationCreate


def test_recorded_evaluate_cli_declares_every_create_dto_field():
    mapped_fields = {
        "agent_id",  # --agent via RefResolver
        "run_ids",  # --runs CSV
        "case_ids",  # --tests CSV unless --tests all
        "all_tests",  # --tests all
        "applicability",  # --applicability
        "judge_mode",  # --judge
        "applicability_overrides",  # --applicability-file
    }
    assert set(RecordedEvaluationCreate.model_fields) == mapped_fields


def test_recorded_cli_rejects_malformed_uuid_csv_locally():
    with pytest.raises(click.UsageError):
        _csv_uuid_values("not-a-uuid", "--runs")

    with pytest.raises(click.UsageError):
        _csv_uuid_values("", "--runs")

    value = uuid4()
    assert _csv_uuid_values(str(value), "--runs") == [str(value)]


def test_recorded_cli_verdict_exit_codes_preserve_failure_precedence():
    assert _verdict_exit_code({"aggregate": {"gate_passed": True, "counts": {}}}) == 0
    assert _verdict_exit_code({"aggregate": {"counts": {"error": 1, "failed": 1}}}) == 3
    assert _verdict_exit_code({"aggregate": {"counts": {"failed": 1}}}) == 1
    assert _verdict_exit_code({"aggregate": {"counts": {"pending_judge": 1}}}) == 4
    assert _verdict_exit_code({"aggregate": {"counts": {"insufficient_evidence": 1}}}) == 4
    assert _verdict_exit_code({"aggregate": {"counts": {"not_applicable": 2}, "all_inapplicable": True}}) == 4


_AGENT_ID = "11111111-1111-1111-1111-111111111111"
_CASE_ID = "22222222-2222-2222-2222-222222222222"
_RUN_ID = "33333333-3333-3333-3333-333333333333"
_EVAL_ID = "44444444-4444-4444-4444-444444444444"
_JOB_ID = "55555555-5555-5555-5555-555555555555"
_EXECUTION_ID = "66666666-6666-6666-6666-666666666666"
_DESIGNER_RUN_ID = "77777777-7777-7777-7777-777777777777"


class _FakeResponse:
    def __init__(self, status: int, body: Any, *, headers: dict[str, str] | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.request = httpx.Request("GET", "http://test.local")

    @property
    def text(self) -> str:
        return json.dumps(self._body)

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=self.request, response=httpx.Response(self.status_code)
            )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.post_responses: list[_FakeResponse] = []
        self.get_responses: list[_FakeResponse] = []

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


@pytest.fixture
def recorded_cli(monkeypatch: pytest.MonkeyPatch):
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

    from bifrost import refs as refs_module

    monkeypatch.setattr(refs_module.RefResolver, "resolve", _resolve)
    return fake


def _invoke_recorded(args: list[str]):
    return CliRunner().invoke(agent_tests_group, args, catch_exceptions=True)


def _accepted_response() -> _FakeResponse:
    return _FakeResponse(
        202,
        {"job_id": _JOB_ID, "status": "pending", "reused": False},
        headers={"X-Recorded-Evaluation-Id": _EVAL_ID},
    )


def _results(outcome: str, *, complete: bool = True) -> dict[str, Any]:
    counts = {
        "passed": 0,
        "failed": 0,
        "error": 0,
        "pending_judge": 0,
        "insufficient_evidence": 0,
        "not_applicable": 0,
    }
    counts[outcome] = 1
    return {
        "evaluation_id": _EVAL_ID,
        "aggregate": {
            "counts": counts,
            "total": 1,
            "all_inapplicable": outcome == "not_applicable",
            "complete": complete,
            "gate_passed": outcome == "passed" and complete,
        },
        "limit": 50,
        "offset": 0,
        "total": 1,
        "results": [{"outcome": outcome, "complete": complete}],
    }


def _usage_report(
    *,
    purpose: str = "recorded_semantic_judge",
    provider: str = "openrouter",
    model: str = "gpt-test",
) -> dict[str, Any]:
    totals = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 1,
        "cache_write_tokens": 0,
        "call_count": 1,
        "duration_ms": 0,
        "duration_missing_count": 1,
        "observed_provider_cost": "0.00040000",
        "estimated_cost": "0",
        "known_cost": "0.00040000",
        "missing_cost_call_count": 0,
        "legacy_call_count": 0,
    }
    coverage = {
        "started_attempt_count": 1,
        "unobserved_attempt_count": 0,
        "missing_cost_call_count": 0,
        "unassigned_operation_call_count": 0,
        "legacy_coverage_unknown": True,
        "legacy_call_count": 0,
    }
    empty_page = {
        "items": [],
        "total_groups": 0,
        "limit": 50,
        "offset": 0,
        "omitted_group_count": 0,
    }
    payload = {
        "overall": totals,
        "coverage": coverage,
        "by_purpose": {
            "items": [{"purpose": purpose, "totals": totals, "coverage": coverage}],
            "total_groups": 3,
            "limit": 1,
            "offset": 0,
            "omitted_group_count": 2,
        },
        "by_provider_model": {
            "items": [
                {
                    "purpose": purpose,
                    "provider": provider,
                    "model": model,
                    "totals": totals,
                    "coverage": coverage,
                }
            ],
            "total_groups": 1,
            "limit": 50,
            "offset": 0,
            "omitted_group_count": 0,
        },
        "by_profile": empty_page,
        "by_organization": empty_page,
        "by_operation": empty_page,
    }
    return QualityUsageBreakdownResponse.model_validate(payload).model_dump(mode="json")


def test_recorded_evaluate_enqueue_only_outputs_json(recorded_cli: _FakeClient):
    recorded_cli.post_responses.append(_accepted_response())

    result = _invoke_recorded([
        "evaluate",
        "--agent",
        "my-agent",
        "--tests",
        _CASE_ID,
        "--runs",
        _RUN_ID,
        "--json",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["job_id"] == _JOB_ID
    assert payload["evaluation_id"] == _EVAL_ID
    assert recorded_cli.calls[0] == (
        "POST",
        "/api/agent-evaluations/recorded-evaluations",
        {
            "agent_id": _AGENT_ID,
            "run_ids": [_RUN_ID],
            "applicability": "unknown",
            "judge_mode": "exact",
            "applicability_overrides": [],
            "case_ids": [_CASE_ID],
        },
    )


def test_recorded_evaluate_semantic_judge_flag_is_forwarded(recorded_cli: _FakeClient):
    recorded_cli.post_responses.append(_accepted_response())

    result = _invoke_recorded([
        "evaluate",
        "--agent",
        "my-agent",
        "--tests",
        _CASE_ID,
        "--runs",
        _RUN_ID,
        "--judge",
        "semantic",
        "--json",
    ])

    assert result.exit_code == 0, result.output
    assert recorded_cli.calls[0][2]["judge_mode"] == "semantic"


def test_recorded_usage_command_outputs_json_and_forwards_pagination(recorded_cli: _FakeClient):
    recorded_cli.get_responses.append(_FakeResponse(200, _usage_report()))

    result = _invoke_recorded([
        "recorded-usage",
        _EVAL_ID,
        "--limit",
        "25",
        "--offset",
        "5",
        "--json",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["overall"]["known_cost"] == "0.00040000"
    assert recorded_cli.calls[0] == (
        "GET",
        f"/api/agent-evaluations/recorded-evaluations/{_EVAL_ID}/usage",
        {"limit": 25, "offset": 5},
    )


def test_synthetic_execution_usage_command_outputs_json_and_forwards_pagination(recorded_cli: _FakeClient):
    recorded_cli.get_responses.append(_FakeResponse(200, _usage_report()))

    result = _invoke_recorded([
        "usage",
        _EXECUTION_ID,
        "--limit",
        "25",
        "--offset",
        "5",
        "--json",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["overall"]["known_cost"] == "0.00040000"
    assert recorded_cli.calls[0] == (
        "GET",
        f"/api/agent-evaluations/executions/{_EXECUTION_ID}/usage",
        {"limit": 25, "offset": 5},
    )


def test_synthetic_execution_usage_human_output_includes_breakdown(recorded_cli: _FakeClient):
    recorded_cli.get_responses.append(
        _FakeResponse(
            200,
            _usage_report(
                purpose="synthetic_semantic_judge",
                provider="openrouter",
                model="judge-model",
            ),
        )
    )

    result = _invoke_recorded(["usage", _EXECUTION_ID])

    assert result.exit_code == 0, result.output
    assert "synthetic_semantic_judge" in result.output
    assert "calls=1" in result.output
    assert "known_cost=0.00040000" in result.output
    assert "openrouter/judge-model" in result.output
    assert "legacy_coverage_unknown" in result.output
    assert "purpose=unknown" not in result.output
    assert "unknown: calls=" not in result.output
    assert "None" not in result.output


def test_designer_usage_command_outputs_json_and_forwards_pagination(recorded_cli: _FakeClient):
    recorded_cli.get_responses.append(_FakeResponse(200, _usage_report()))

    result = _invoke_recorded([
        "designer-usage",
        _DESIGNER_RUN_ID,
        "--limit",
        "10",
        "--offset",
        "2",
        "--json",
    ])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["overall"]["call_count"] == 1
    assert recorded_cli.calls[0] == (
        "GET",
        f"/api/agent-evaluations/designer-runs/{_DESIGNER_RUN_ID}/usage",
        {"limit": 10, "offset": 2},
    )


def test_designer_usage_human_output_includes_designer_breakdown(recorded_cli: _FakeClient):
    recorded_cli.get_responses.append(
        _FakeResponse(
            200,
            _usage_report(
                purpose="test_designer", provider="anthropic", model="designer-model"
            ),
        )
    )

    result = _invoke_recorded(["designer-usage", _DESIGNER_RUN_ID])

    assert result.exit_code == 0, result.output
    assert "test_designer" in result.output
    assert "anthropic/designer-model" in result.output
    assert "calls=1" in result.output
    assert "purpose=unknown" not in result.output
    assert "unknown: calls=" not in result.output
    assert "None" not in result.output


@pytest.mark.parametrize(
    ("outcome", "expected_exit"),
    [("passed", 0), ("failed", 1), ("insufficient_evidence", 4), ("pending_judge", 4)],
)
def test_recorded_evaluate_wait_outputs_results_and_verdict_exit(
    monkeypatch: pytest.MonkeyPatch,
    recorded_cli: _FakeClient,
    outcome: str,
    expected_exit: int,
):
    recorded_cli.post_responses.append(_accepted_response())
    recorded_cli.get_responses.append(_FakeResponse(200, _results(outcome)))

    async def _poll(*_args, **_kwargs):
        return {"id": _JOB_ID, "status": "succeeded"}

    monkeypatch.setattr(agent_tests_module, "poll_platform_job", _poll)

    result = _invoke_recorded([
        "evaluate",
        "--agent",
        "my-agent",
        "--tests",
        _CASE_ID,
        "--runs",
        _RUN_ID,
        "--wait",
        "--json",
    ])

    assert result.exit_code == expected_exit, result.output
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "succeeded"
    assert payload["results"]["results"][0]["outcome"] == outcome


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_recorded_evaluate_wait_job_terminal_failure_is_json_exit_three(
    monkeypatch: pytest.MonkeyPatch,
    recorded_cli: _FakeClient,
    terminal_status: str,
):
    recorded_cli.post_responses.append(_accepted_response())

    async def _poll(*_args, **_kwargs):
        return {"id": _JOB_ID, "status": terminal_status, "error": {"message": "boom"}}

    monkeypatch.setattr(agent_tests_module, "poll_platform_job", _poll)

    result = _invoke_recorded([
        "evaluate",
        "--agent",
        "my-agent",
        "--tests",
        _CASE_ID,
        "--runs",
        _RUN_ID,
        "--wait",
        "--json",
    ])

    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == terminal_status
    assert payload["evaluation_id"] == _EVAL_ID


def test_recorded_evaluate_wait_deadline_is_json_exit_three(
    monkeypatch: pytest.MonkeyPatch,
    recorded_cli: _FakeClient,
):
    recorded_cli.post_responses.append(_accepted_response())

    async def _poll(*_args, **_kwargs):
        raise click.ClickException("deadline")

    monkeypatch.setattr(agent_tests_module, "poll_platform_job", _poll)

    result = _invoke_recorded([
        "evaluate",
        "--agent",
        "my-agent",
        "--tests",
        _CASE_ID,
        "--runs",
        _RUN_ID,
        "--wait",
        "--json",
    ])

    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "wait_failed"
    assert payload["message"] == "deadline"


@pytest.mark.asyncio
async def test_platform_job_poll_preserves_default_failure_behavior():
    from bifrost.platform_jobs import poll_platform_job

    class PollClient:
        async def get(self, path: str):
            assert path == f"/api/platform-jobs/{_JOB_ID}"
            return _FakeResponse(200, {"id": _JOB_ID, "status": "failed", "error": {"message": "boom"}})

    with pytest.raises(click.ClickException):
        await poll_platform_job(PollClient(), _JOB_ID, label="Recorded", timeout_seconds=0)

    returned = await poll_platform_job(
        PollClient(),
        _JOB_ID,
        label="Recorded",
        timeout_seconds=0,
        return_terminal_failures=True,
    )
    assert returned["status"] == "failed"
