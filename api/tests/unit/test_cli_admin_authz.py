"""Unit tests for ``bifrost admin authz legacy-status``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner, Result

from bifrost import client as bifrost_client_module
from bifrost.commands.admin import admin_group

PATH = "/api/maintenance/authz/legacy-status"


class _FakeClient:
    def __init__(self, body: dict[str, Any]) -> None:
        self.api_url = "http://test.local"
        self.body = body
        self.calls: list[str] = []

    async def get(self, path: str) -> httpx.Response:
        self.calls.append(path)
        return httpx.Response(200, json=self.body, request=httpx.Request("GET", f"{self.api_url}{path}"))


def _invoke(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], *args: str) -> tuple[Result, _FakeClient]:
    fake = _FakeClient(body)
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )
    result = CliRunner().invoke(
        admin_group, ["authz", "legacy-status", *args], standalone_mode=False, catch_exceptions=False
    )
    return result, fake


_STATUS = {
    "live_runs": [
        {
            "kind": "service_attempt",
            "id": "11111111-1111-4111-8111-111111111111",
            "name": "22222222-2222-4222-8222-222222222222",
            "status": "running",
            "organization_id": None,
            "started_at": "2026-10-30T10:00:00Z",
        }
    ],
    "launchers": [
        {
            "worker_id": "33333333-3333-4333-8333-333333333333",
            "hostname": "runner-1",
            "state": "ready",
            "credential_protocol": 1,
        }
    ],
}


def test_it_reads_the_legacy_status_and_lists_runs_and_launchers(monkeypatch: pytest.MonkeyPatch) -> None:
    result, fake = _invoke(monkeypatch, _STATUS)

    assert fake.calls == [PATH]
    assert "Live legacy-format runs: 1" in result.output
    assert "service_attempt 11111111-1111-4111-8111-111111111111" in result.output
    assert "Legacy launchers: 1" in result.output
    assert "runner-1 (ready, credential protocol 1)" in result.output


def test_it_says_when_nothing_depends_on_legacy_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = _invoke(monkeypatch, {"live_runs": [], "launchers": []})

    assert result.output.strip() == "No live legacy-format runs and no legacy launchers."


def test_json_output_is_the_api_body(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = _invoke(monkeypatch, _STATUS, "--json")

    assert json.loads(result.output) == _STATUS
