"""CLI contract tests for workflow execution-history commands."""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
from click.testing import CliRunner

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.workflows import workflows_group  # noqa: E402


def _response(path: str, body: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json=body,
        request=httpx.Request("GET", f"https://bifrost.test{path}"),
    )


def _invoke(args: list[str], responses: dict[str, dict], captured: list[dict]):
    async def get(path: str, params=None):  # type: ignore[no-untyped-def]
        captured.append({"path": path, "params": params})
        return _response(path, responses[path])

    client = mock.AsyncMock()
    client.get = get
    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        return CliRunner().invoke(workflows_group, args)


def test_list_forwards_complete_execution_filters() -> None:
    captured: list[dict] = []
    result = _invoke(
        [
            "list-executions",
            "--scope",
            "global",
            "--workflow-name",
            "Daily Sync",
            "--workflow-id",
            "00000000-0000-0000-0000-000000000001",
            "--status",
            "Success,Failed",
            "--start-date",
            "2026-08-01",
            "--end-date",
            "2026-08-17",
            "--include-local",
            "--limit",
            "50",
            "--continuation-token",
            "next-page",
            "--json",
        ],
        {"/api/executions": {"executions": [], "continuation_token": None}},
        captured,
    )

    assert result.exit_code == 0, result.output
    assert captured == [
        {
            "path": "/api/executions",
            "params": {
                "excludeLocal": False,
                "limit": 50,
                "scope": "global",
                "workflowName": "Daily Sync",
                "workflowId": "00000000-0000-0000-0000-000000000001",
                "status": "Success,Failed",
                "startDate": "2026-08-01",
                "endDate": "2026-08-17",
                "continuationToken": "next-page",
            },
        }
    ]


def test_get_reads_one_execution() -> None:
    captured: list[dict] = []
    result = _invoke(
        ["get-execution", "00000000-0000-0000-0000-000000000002", "--json"],
        {
            "/api/executions/00000000-0000-0000-0000-000000000002": {
                "execution_id": "00000000-0000-0000-0000-000000000002",
                "status": "Success",
            }
        },
        captured,
    )

    assert result.exit_code == 0, result.output
    assert captured == [
        {
            "path": "/api/executions/00000000-0000-0000-0000-000000000002",
            "params": None,
        }
    ]
    assert '"status": "Success"' in result.output
