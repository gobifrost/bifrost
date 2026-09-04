import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


def _execution_payload(execution_id: str, workflow_name: str) -> dict[str, object]:
    return {
        "execution_id": execution_id,
        "workflow_name": workflow_name,
        "org_id": None,
        "form_id": None,
        "executed_by": "user-1",
        "executed_by_name": "Test User",
        "status": "Success",
        "input_data": {},
        "result": None,
        "result_type": None,
        "error_message": None,
        "duration_ms": None,
        "started_at": None,
        "completed_at": None,
        "logs": None,
        "variables": None,
        "session_id": None,
        "peak_memory_bytes": None,
        "process_rss_bytes": None,
        "cpu_total_seconds": None,
    }


@pytest.mark.asyncio
async def test_list_uses_snake_case_filters_and_returns_continuation_token(monkeypatch):
    module = importlib.import_module("bifrost.executions")
    response = MagicMock()
    response.json.return_value = {
        "executions": [
            _execution_payload("exec-1", "workflow-a"),
            _execution_payload("exec-2", "workflow-b"),
        ],
        "continuation_token": "token-2",
    }
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    result = await module.executions.list(
        workflow_id="wf-123",
        workflow_name="ignored-name",
        status="Success",
        start_date="2026-09-01T00:00:00",
        end_date="2026-09-02T00:00:00",
        exclude_local=True,
        continuation_token="token-1",
        limit=2500,
    )

    assert isinstance(result, list)
    assert [execution.execution_id for execution in result] == ["exec-1", "exec-2"]
    assert result.continuation_token == "token-2"
    client.get.assert_awaited_once_with(
        "/api/executions",
        params={
            "workflow_id": "wf-123",
            "status": "Success",
            "start_date": "2026-09-01T00:00:00",
            "end_date": "2026-09-02T00:00:00",
            "exclude_local": "true",
            "continuation_token": "token-1",
            "limit": 1000,
        },
    )


@pytest.mark.asyncio
async def test_list_preserves_legacy_workflow_name_filter(monkeypatch):
    module = importlib.import_module("bifrost.executions")
    response = MagicMock()
    response.json.return_value = {"executions": [], "continuation_token": None}
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    await module.executions.list(workflow_name="legacy-name", limit=10)

    client.get.assert_awaited_once_with(
        "/api/executions",
        params={"workflow_name": "legacy-name", "limit": 10},
    )
