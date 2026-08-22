"""Credential-boundary tests for isolated execution children."""

import os

from bifrost.credentials import resolve_credentials
from src.services.execution.worker import _set_process_engine_credentials


def test_handed_down_token_uses_ephemeral_process_backend(monkeypatch):
    monkeypatch.setenv("BIFROST_API_URL", "http://api:8000")
    monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

    assert _set_process_engine_credentials({"engine_token": "one-shot-token"})

    resolved = resolve_credentials("http://api:8000")
    assert resolved is not None
    assert resolved.source == "process"
    assert resolved.credentials.access_token == "one-shot-token"
    assert resolved.credentials.refresh_token == "one-shot-token"


def test_missing_handed_down_token_does_not_create_process_credentials(monkeypatch):
    monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

    assert not _set_process_engine_credentials({})
    assert "BIFROST_ACCESS_TOKEN" not in os.environ
    assert "BIFROST_REFRESH_TOKEN" not in os.environ
