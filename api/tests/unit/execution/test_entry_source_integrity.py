"""Entry source is verified before either worker can execute module code."""

import hashlib
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.execution import worker


@pytest.mark.parametrize("service", [False, True])
@pytest.mark.parametrize("digest", [None, "0" * 64])
async def test_unverified_source_never_reaches_the_loader(monkeypatch, service, digest):
    code = "raise AssertionError('unverified module executed')\n"
    loader = MagicMock(side_effect=AssertionError("loader must not be called"))
    execute = AsyncMock()
    monkeypatch.setattr(worker, "_clear_workspace_modules", lambda: None)
    monkeypatch.setattr(worker, "_set_process_engine_credentials", lambda _: True)
    monkeypatch.setattr("bifrost._service_runtime.install_legacy_service_token", lambda _: None)
    monkeypatch.setattr("src.core.module_cache_sync.get_module_sync", lambda _: {"content": code})
    monkeypatch.setattr("src.services.execution.module_loader.load_workflow_from_db", loader)
    monkeypatch.setattr("src.services.execution.engine.execute", execute)
    context = {
        "caller": {"user_id": str(uuid4()), "email": "test@example.com", "name": "Test"},
        "name": "test", "function_name": "run", "file_path": "workflows/test.py",
        "content_hash": digest,
    }
    if service:
        context["service"] = {"attempt_id": str(uuid4()), "token": "test-token"}

    result = await worker.run_execution(str(uuid4()), context)

    assert result["status"] == "Failed"
    assert "source" in result["error_message"].lower()
    loader.assert_not_called()
    execute.assert_not_awaited()


def test_matching_source_is_accepted():
    code = "VALUE = 'accepted'\n"
    digest = hashlib.sha256(code.encode()).hexdigest()
    assert worker._verify_entry_source(code, {
        "content_hash": digest, "file_path": "workflows/test.py",
    }) == digest


def test_solution_without_entry_pin_retains_existing_install_behavior():
    code = "VALUE = 'solution'\n"
    assert worker._verify_entry_source(code, {
        "solution_id": str(uuid4()), "file_path": "workflows/test.py",
    }) == hashlib.sha256(code.encode()).hexdigest()
