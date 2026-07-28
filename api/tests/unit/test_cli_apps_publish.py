from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands.apps import apps_group


class _FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.api_url = "http://test.local"
        self._access_token = "token"
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _response(self, method: str, path: str, body: dict[str, Any], status=200):
        request = httpx.Request(method, f"{self.api_url}{path}")
        return httpx.Response(status, json=body, request=request)

    async def post(self, path: str, *, json: dict[str, Any]):
        self.calls.append(("POST", path, json))
        return self._response(
            "POST",
            path,
            {
                "job_id": "job-1",
                "status": "queued",
                "reused": False,
            },
            status=202,
        )

    async def get(self, path: str):
        self.calls.append(("GET", path, None))
        return self._response("GET", path, self.responses.pop(0))


def test_apps_publish_polls_short_requests_to_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = str(uuid4())
    fake = _FakeClient(
        [
            {
                "status": "queued",
                "progress": {"phase": "Queued", "current": 0, "total": None},
            },
            {
                "status": "running",
                "progress": {
                    "phase": "promoting current bundle",
                    "current": 2,
                    "total": 3,
                },
            },
            {
                "status": "succeeded",
                "progress": {"phase": "Completed", "current": 3, "total": 3},
                "result": {"files_published": 3},
            },
        ]
    )
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("bifrost.commands.apps.asyncio.sleep", sleep)

    result = CliRunner().invoke(
        apps_group,
        ["publish", app_id, "--message", "Release"],
    )

    assert result.exit_code == 0, result.output
    assert fake.calls[0] == (
        "POST",
        f"/api/applications/{app_id}/publish",
        {"message": "Release"},
    )
    assert [call[0] for call in fake.calls].count("GET") == 3
    assert "files_published" in result.output
    assert sleep.await_count == 2


def test_apps_publish_surfaces_persisted_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = str(uuid4())
    fake = _FakeClient(
        [
            {
                "status": "failed",
                "progress": {"phase": "Failed", "current": 0, "total": None},
                "error": {
                    "code": "application_publish_failed",
                    "message": "Bundle build failed during publish: bad import",
                    "retryable": False,
                },
            }
        ]
    )
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    result = CliRunner().invoke(apps_group, ["publish", app_id])

    assert result.exit_code == 1
    assert "bad import" in result.output
    assert "job job-1" in result.output


def test_apps_publish_status_timeout_reports_durable_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = str(uuid4())
    fake = _FakeClient([])

    async def timeout_get(_path: str):
        raise httpx.ReadTimeout("status request timed out")

    monkeypatch.setattr(fake, "get", timeout_get)
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    result = CliRunner().invoke(apps_group, ["publish", app_id])

    assert result.exit_code == 1
    assert "Timed out reading application publish job job-1" in result.output
    assert "may still be running" in result.output
