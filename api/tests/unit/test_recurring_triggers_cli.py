"""Recurring triggers CLI local contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost import refs as refs_module
from bifrost.commands.recurring_triggers import recurring_triggers_group
from shared.models import RecurringTriggerCreate, RecurringTriggerUpdate

_TRIGGER_ID = "11111111-1111-1111-1111-111111111111"
_REVIEW_ID = "22222222-2222-2222-2222-222222222222"


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
def schedules_cli(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    async def _resolve(self, kind: str, value: str) -> str:
        return value

    monkeypatch.setattr(refs_module.RefResolver, "resolve", _resolve)
    return fake


def _invoke(args: list[str]):
    return CliRunner().invoke(recurring_triggers_group, args, catch_exceptions=True)


def test_schedules_cli_declares_request_dto_fields():
    assert set(RecurringTriggerCreate.model_fields) == {
        "operation_type",
        "operation_id",
        "operation_params",
        "cron_expression",
        "timezone",
        "overlap_policy",
    }
    assert set(RecurringTriggerUpdate.model_fields) == {
        "cron_expression",
        "timezone",
        "enabled",
        "operation_params",
    }


def test_schedules_create_payload_from_flags_and_files(
    schedules_cli: _FakeClient, tmp_path: Path
):
    params_file = tmp_path / "params.json"
    params_file.write_text(
        json.dumps({"review_id": _REVIEW_ID, "lookback_days": 3}), encoding="utf-8"
    )
    schedules_cli.post_responses.append(_FakeResponse(201, {"id": _TRIGGER_ID}))
    result = _invoke(
        [
            "--json",
            "create",
            "--operation",
            "agent_review",
            "--operation-id",
            _REVIEW_ID,
            "--params-file",
            str(params_file),
            "--cron",
            "*/15 * * * *",
            "--timezone",
            "UTC",
        ]
    )
    assert result.exit_code == 0, result.output
    assert schedules_cli.calls[0] == (
        "POST",
        "/api/recurring-triggers",
        {
            "operation_type": "agent_review",
            "operation_id": _REVIEW_ID,
            "operation_params": {"review_id": _REVIEW_ID, "lookback_days": 3},
            "cron_expression": "*/15 * * * *",
            "timezone": "UTC",
        },
    )


def test_schedules_cli_usage_errors_are_json_stdout_and_exit_two(
    schedules_cli: _FakeClient, tmp_path: Path
):
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("cron_expression: x\nbogus: 1\n", encoding="utf-8")
    result = _invoke(["--json", "update", _TRIGGER_ID, "--file", str(unknown)])
    assert result.exit_code == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"] == "usage_error"

    result = _invoke(
        ["--json", "update", _TRIGGER_ID, "--enable", "--disable"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "usage_error"

    result = _invoke(["--json", "create", "--operation", "agent_review"])
    assert result.exit_code == 2
    assert "operation_id" in json.loads(result.stdout)["message"]
    assert schedules_cli.calls == []


def test_schedules_cli_maps_server_validation_to_invocation_error(
    schedules_cli: _FakeClient,
):
    schedules_cli.post_responses.append(
        _FakeResponse(422, {"detail": "cron_expression must be valid"})
    )
    result = _invoke(
        [
            "--json",
            "create",
            "--operation",
            "agent_review",
            "--operation-id",
            _REVIEW_ID,
            "--cron",
            "bogus",
        ]
    )
    assert result.exit_code == 2
    assert result.stderr == ""
    assert "cron_expression must be valid" in json.loads(result.stdout)["message"]


def test_schedules_list_get_update_disable_fires_paths(schedules_cli: _FakeClient):
    schedules_cli.get_responses.append(
        _FakeResponse(200, {"items": [], "total": 0, "limit": 50, "offset": 0})
    )
    result = _invoke(
        ["--json", "list", "--operation", "agent_review", "--enabled", "--limit", "10"]
    )
    assert result.exit_code == 0, result.output
    assert schedules_cli.calls[-1] == (
        "GET",
        "/api/recurring-triggers",
        {"limit": 10, "offset": 0, "operation_type": "agent_review", "enabled": True},
    )

    schedules_cli.get_responses.append(_FakeResponse(200, {"id": _TRIGGER_ID}))
    result = _invoke(["--json", "get", _TRIGGER_ID])
    assert result.exit_code == 0, result.output

    schedules_cli.patch_responses.append(_FakeResponse(200, {"id": _TRIGGER_ID}))
    result = _invoke(["--json", "update", _TRIGGER_ID, "--cron", "0 * * * *"])
    assert result.exit_code == 0, result.output
    assert schedules_cli.calls[-1] == (
        "PATCH",
        f"/api/recurring-triggers/{_TRIGGER_ID}",
        {"cron_expression": "0 * * * *"},
    )

    schedules_cli.patch_responses.append(
        _FakeResponse(200, {"id": _TRIGGER_ID, "enabled": False})
    )
    result = _invoke(["--json", "disable", _TRIGGER_ID])
    assert result.exit_code == 0, result.output
    assert schedules_cli.calls[-1] == (
        "PATCH",
        f"/api/recurring-triggers/{_TRIGGER_ID}",
        {"enabled": False},
    )

    schedules_cli.get_responses.append(
        _FakeResponse(200, {"items": [], "total": 0, "limit": 50, "offset": 0})
    )
    result = _invoke(["--json", "fires", _TRIGGER_ID])
    assert result.exit_code == 0, result.output
    assert schedules_cli.calls[-1] == (
        "GET",
        f"/api/recurring-triggers/{_TRIGGER_ID}/fires",
        {"limit": 50, "offset": 0},
    )
