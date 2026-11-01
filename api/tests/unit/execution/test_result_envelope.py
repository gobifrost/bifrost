"""One engine result contract shared by Linux and companion runtimes."""
from unittest.mock import AsyncMock

import pytest

from src.services.execution.result_envelope import execution_result_envelope


@pytest.mark.parametrize("status,success", [
    ("Success", True), ("CompletedWithErrors", True), ("Failed", False), ("Timeout", False),
])
def test_engine_status_and_result_fields_keep_existing_parent_contract(status, success):
    result = {"status": status, "result": {"value": 70}, "error_message": "detail",
              "error_type": "ValueError", "duration_ms": 12, "logs": [{"message": "proof"}],
              "variables": {"a": 1}, "integration_calls": [{"name": "http"}],
              "roi": {"time_saved": 2}, "cached": True, "cache_expires_at": "expiry",
              "execution_context": {"scope": "fixture"}, "service": {"attempt_id": "attempt"}}
    metrics = {"cpu_total_seconds": None}
    envelope = execution_result_envelope("execution", "worker", result, duration_ms=99, metrics=metrics)
    assert envelope == {"execution_id": "execution", "worker_id": "worker", "success": success,
                        "status": status, "result": {"value": 70}, "error": "detail",
                        "error_type": "ValueError", "duration_ms": 12, "logs": [{"message": "proof"}],
                        "variables": {"a": 1}, "integration_calls": [{"name": "http"}],
                        "roi": {"time_saved": 2}, "cached": True, "cache_expires_at": "expiry",
                        "execution_context": {"scope": "fixture"}, "metrics": metrics,
                        "service": {"attempt_id": "attempt"}}


def test_missing_values_keep_defaults_and_unknown_metrics():
    envelope = execution_result_envelope("execution", "worker", {"service": "invalid"},
                                         duration_ms=99, metrics={"peak_memory_bytes": None})
    assert envelope["status"] == "Failed" and envelope["success"] is False
    assert envelope["duration_ms"] == 99
    assert envelope["metrics"] == {"peak_memory_bytes": None}
    assert envelope["logs"] == [] and envelope["integration_calls"] == []
    assert "service" not in envelope


@pytest.mark.asyncio
async def test_linux_adapter_keeps_resource_accounting_with_shared_envelope(monkeypatch):
    from src.services.execution import simple_worker, worker

    pss = iter([100, 160])
    monkeypatch.setattr(simple_worker, "_get_pss_bytes", lambda: next(pss))
    monkeypatch.setattr(simple_worker, "_get_process_rss", lambda: 20)
    monkeypatch.setattr(simple_worker, "_peak_memory_growth", lambda _start: 7)
    run = AsyncMock(return_value={"status": "Success", "result": 70, "duration_ms": 12,
                                 "metrics": {"cpu_total_seconds": None}})
    monkeypatch.setattr(worker, "run_execution", run)
    result = await simple_worker._execute_async("execution", "worker", {"parameters": {}})
    run.assert_awaited_once_with("execution", {"parameters": {}})
    assert result["success"] is True and result["result"] == 70
    assert result["duration_ms"] == 12
    assert result["metrics"] == {"cpu_total_seconds": None, "peak_memory_bytes": 60,
                                 "peak_memory_growth_bytes": 7}
