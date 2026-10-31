"""Trusted runtime inputs do not require Linux accounting or server settings."""
import sys
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def restore_virtual_import_hook():
    from src.services.execution import virtual_import

    previous = list(sys.meta_path)
    finder = virtual_import._finder
    yield
    sys.meta_path[:] = previous
    virtual_import._finder = finder


async def test_engine_uses_explicit_runtime_url_without_loading_settings(monkeypatch):
    from src.sdk.context import Caller, ExecutionContext
    from src.services.execution.engine import ExecutionRequest, execute

    forbidden = Mock(side_effect=AssertionError("Server settings must not be read"))
    monkeypatch.setattr("src.config.get_settings", forbidden)

    async def workflow(context: ExecutionContext):
        return {"public_url": context.public_url}

    result = await execute(ExecutionRequest(
        execution_id="runtime-metadata-test",
        caller=Caller("user", "user@example.invalid", "User"),
        organization=None,
        func=workflow,
        public_url="https://isolated.example.invalid",
    ))
    assert result.result == {"public_url": "https://isolated.example.invalid"}
    forbidden.assert_not_called()


async def test_worker_preserves_unknown_provider_metrics_without_os_sampling(monkeypatch):
    from src.services.execution import worker

    forbidden = Mock(side_effect=AssertionError("OS metrics unavailable"))
    monkeypatch.setattr(worker, "_get_resource_usage", forbidden)
    monkeypatch.setattr(worker, "_set_process_engine_credentials", lambda _: True)

    class UnknownResources:
        started = False

        def start(self):
            self.started = True

        def finish(self):
            assert self.started
            return worker.ResourceMetrics(None, None, None, None, None)

    resources = UnknownResources()
    result = await worker.run_execution("provider-metrics-test", {
        "caller": {"user_id": "user", "email": "user@example.invalid", "name": "User"},
        "name": "missing-workflow",
    }, resource_observer=resources, public_url="https://isolated.example.invalid")
    assert result["error_type"] == "ExecutableNotFound"
    assert result["metrics"]["peak_memory_bytes"] is None
    assert result["metrics"]["cpu_total_seconds"] is None
    forbidden.assert_not_called()


def test_default_process_observer_measures_real_process_resources():
    from src.services.execution import worker

    observer = worker.ProcessResourceObserver()
    observer.start()
    metrics = observer.finish()
    assert metrics.peak_memory_bytes is not None and metrics.peak_memory_bytes > 0
    assert metrics.cpu_total_seconds is not None and metrics.cpu_total_seconds >= 0
