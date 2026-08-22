"""Credential-boundary tests for isolated execution children."""

import os

import pytest

from bifrost.credentials import resolve_credentials


@pytest.fixture
def set_process_engine_credentials():
    """Import the child entry point without leaking its hook across tests."""
    from src.services.execution.virtual_import import remove_virtual_import_hook
    from src.services.execution.worker import _set_process_engine_credentials

    remove_virtual_import_hook()
    yield _set_process_engine_credentials
    os.environ.pop("BIFROST_ACCESS_TOKEN", None)
    os.environ.pop("BIFROST_REFRESH_TOKEN", None)


def test_handed_down_token_uses_ephemeral_process_backend(
    monkeypatch,
    set_process_engine_credentials,
):
    monkeypatch.setenv("BIFROST_API_URL", "http://api:8000")
    monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

    assert set_process_engine_credentials({"engine_token": "one-shot-token"})

    resolved = resolve_credentials("http://api:8000")
    assert resolved is not None
    assert resolved.source == "process"
    assert resolved.credentials.access_token == "one-shot-token"
    assert resolved.credentials.refresh_token == "one-shot-token"


def test_missing_handed_down_token_does_not_create_process_credentials(
    monkeypatch,
    set_process_engine_credentials,
):
    monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

    assert not set_process_engine_credentials({})
    assert "BIFROST_ACCESS_TOKEN" not in os.environ
    assert "BIFROST_REFRESH_TOKEN" not in os.environ


def test_engine_token_carries_signed_execution_module_scope() -> None:
    from src.core.security import decode_token, mint_engine_token

    solution_id = "12345678-1234-5678-1234-567812345678"
    token, _expires_at = mint_engine_token(
        execution_id="execution-1",
        solution_id=solution_id,
        global_repo_access=True,
        timeout_seconds=120,
    )

    claims = decode_token(token, expected_type="access")
    assert claims is not None
    assert claims["engine_execution_id"] == "execution-1"
    assert claims["engine_solution_id"] == solution_id
    assert claims["engine_global_repo_access"] is True
