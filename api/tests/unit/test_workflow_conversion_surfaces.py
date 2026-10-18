"""CLI and MCP use the shared conversion REST contract without local mutations."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from click.testing import CliRunner

from bifrost.commands.workflows import workflows_group
from src.services.mcp_server.tools import workflow_conversion as mcp


JOB = "00000000-0000-0000-0000-000000000111"
WORKFLOW = "00000000-0000-0000-0000-000000000222"


def response(method: str, path: str, data: dict, status: int = 202) -> httpx.Response:
    return httpx.Response(status, json=data, request=httpx.Request(method, "https://example.test" + path))


def test_cli_analysis_preserves_selected_scope_and_returns_durable_job() -> None:
    client = AsyncMock()
    client.post.return_value = response("POST", "/api/workflow-conversions", {"job_id": JOB, "status": "queued"})
    with patch("bifrost.client.BifrostClient.get_instance", return_value=client), patch(
        "bifrost.refs.RefResolver.resolve", AsyncMock(return_value=WORKFLOW),
    ):
        result = CliRunner().invoke(workflows_group, ["convert", "analyze", "--workflow", "example", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["job_id"] == JOB
    client.post.assert_awaited_once_with("/api/workflow-conversions", json={
        "scope": "all", "platform_cutover": False, "workflow_ids": [WORKFLOW],
    })


def test_cli_platform_analysis_refuses_a_partial_selection() -> None:
    with patch("bifrost.client.BifrostClient.get_instance", return_value=AsyncMock()):
        result = CliRunner().invoke(workflows_group, ["convert", "analyze", "--platform-cutover", "--workflow", WORKFLOW])
    assert result.exit_code != 0
    assert "whole platform" in result.output


def test_cli_report_reads_one_page() -> None:
    client = AsyncMock()
    client.get.return_value = response("GET", "/report", {"items": [], "total": 0}, 200)
    with patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        result = CliRunner().invoke(workflows_group, ["convert", "report", JOB, "--offset", "50", "--limit", "25", "--json"])
    assert result.exit_code == 0, result.output
    client.get.assert_awaited_once_with(f"/api/workflow-conversions/{JOB}/report", params={"offset": 50, "limit": 25})


def test_cli_apply_sends_exact_review_file(tmp_path) -> None:
    body = {"selections": [{"workflow_id": WORKFLOW, "source_fingerprint": "f" * 64, "identity_strategy": "dedicated"}]}
    review = tmp_path / "review.json"
    review.write_text(json.dumps(body))
    client = AsyncMock()
    client.post.return_value = response("POST", "/apply", {"job_id": JOB})
    with patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        result = CliRunner().invoke(workflows_group, ["convert", "apply", JOB, "--review-file", str(review)])
    assert result.exit_code == 0, result.output
    client.post.assert_awaited_once_with(f"/api/workflow-conversions/{JOB}/apply", json=body)


def test_cli_cutover_needs_explicit_acknowledgement() -> None:
    client = AsyncMock()
    with patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        result = CliRunner().invoke(workflows_group, ["convert", "cutover", JOB])
    assert result.exit_code != 0
    client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("function,kwargs,method,path,extra", [
    (mcp.bifrost_workflow_conversion_create, {"workflow_ids": []}, "POST", "/api/workflow-conversions", {"json_body": {"workflow_ids": [], "scope": "all", "platform_cutover": False}}),
    (mcp.bifrost_workflow_conversion_get, {"job_id": JOB}, "GET", f"/api/workflow-conversions/{JOB}/report", {"params": {"offset": 0, "limit": 50}}),
    (mcp.bifrost_workflow_conversion_update, {"job_id": JOB, "selections": []}, "POST", f"/api/workflow-conversions/{JOB}/apply", {"json_body": {"selections": [], "acknowledged_solution_requests": False}}),
    (mcp.bifrost_workflow_permission_default_update, {"job_id": JOB}, "POST", f"/api/workflow-conversions/{JOB}/cutover", {"json_body": {"acknowledged_remaining_exceptions": False}}),
])
async def test_mcp_forwards_to_canonical_rest(function, kwargs, method, path, extra) -> None:
    context = object()
    with patch.object(mcp, "call_rest", AsyncMock(return_value=(403, {"detail": "Denied"}))) as bridge:
        await function(context, **kwargs)
    bridge.assert_awaited_once_with(context, method, path, **extra)


@pytest.mark.asyncio
async def test_mcp_invalid_job_id_does_not_make_a_request() -> None:
    with patch.object(mcp, "call_rest", AsyncMock()) as bridge:
        await mcp.bifrost_workflow_conversion_get(object(), "../different-resource")
    bridge.assert_not_awaited()
