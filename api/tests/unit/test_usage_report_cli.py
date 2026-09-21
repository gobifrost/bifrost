"""Usage report CLI contracts."""

from __future__ import annotations

import json
from typing import Any

import httpx
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands.usage import usage_group
from shared.models import QualityUsageBreakdownResponse


class _FakeResponse:
    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body
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
        self.get_responses: list[_FakeResponse] = []

    async def get(self, path: str, **kwargs):
        self.calls.append(("GET", path, kwargs.get("params")))
        if not self.get_responses:
            raise AssertionError(f"Unexpected GET {path}")
        return self.get_responses.pop(0)


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


def _invoke(args: list[str]):
    return CliRunner().invoke(usage_group, args, obj={})


def test_usage_report_forwards_filters_and_preserves_decimal_json(monkeypatch):
    fake = _FakeClient()
    fake.get_responses.append(_FakeResponse(200, _usage_report()))
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    result = _invoke([
        "report",
        "--start-date",
        "2026-09-20",
        "--end-date",
        "2026-09-21",
        "--org-id",
        "11111111-1111-1111-1111-111111111111",
        "--source",
        "agents",
        "--purpose",
        "recorded_semantic_judge",
        "--provider",
        "openrouter",
        "--model",
        "gpt-test",
        "--limit",
        "25",
        "--offset",
        "5",
        "--json",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["overall"]["known_cost"] == "0.00040000"
    assert fake.calls[0] == (
        "GET",
        "/api/reports/usage/breakdown",
        {
            "start_date": "2026-09-20",
            "end_date": "2026-09-21",
            "org_id": "11111111-1111-1111-1111-111111111111",
            "source": "agents",
            "purpose": "recorded_semantic_judge",
            "provider": "openrouter",
            "model": "gpt-test",
            "limit": 25,
            "offset": 5,
        },
    )


def test_usage_report_human_output_includes_breakdown(monkeypatch):
    fake = _FakeClient()
    fake.get_responses.append(_FakeResponse(200, _usage_report()))
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    result = _invoke([
        "report",
        "--start-date",
        "2026-09-20",
        "--end-date",
        "2026-09-20",
    ])

    assert result.exit_code == 0, result.output
    assert "recorded_semantic_judge" in result.output
    assert "calls=1" in result.output
    assert "known_cost=0.00040000" in result.output
    assert "openrouter/gpt-test" in result.output
    assert "2 omitted by pagination" in result.output
    assert "purpose=unknown" not in result.output
    assert "unknown: calls=" not in result.output
    assert "None" not in result.output


def test_usage_report_rejects_reversed_dates_locally(monkeypatch):
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: _FakeClient()),
    )
    result = _invoke([
        "report",
        "--start-date",
        "2026-09-21",
        "--end-date",
        "2026-09-20",
    ])

    assert result.exit_code != 0
    assert "--end-date must be on or after --start-date" in result.output
