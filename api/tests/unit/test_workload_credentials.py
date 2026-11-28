"""Only signed-format workload access tokens may be an access-only env tuple."""

import base64
import json
from datetime import datetime, timedelta, timezone

from bifrost.credentials import Credentials, EnvBackend


def test_expired_access_only_session_never_starts_human_login(monkeypatch) -> None:
    import threading
    from unittest.mock import AsyncMock, Mock
    import pytest
    from bifrost import client

    monkeypatch.setattr(client, "_thread_local", threading.local())
    monkeypatch.setattr(client, "get_credentials", lambda *_, **__: {
        "api_url": "https://bifrost.example", "access_token": "expired", "refresh_token": "",
    })
    monkeypatch.setattr(client, "is_token_expired", lambda **_: True)
    login = AsyncMock()
    refresh = Mock()
    monkeypatch.setattr(client, "login_flow", login)
    monkeypatch.setattr(client, "_refresh_connection_access_token_sync", refresh)
    with pytest.raises(RuntimeError, match="Workload credentials expired"):
        client.BifrostClient.get_instance(api_url="https://bifrost.example", require_auth=True)
    login.assert_not_called()
    refresh.assert_not_called()


def _token(payload: object) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


def test_env_backend_accepts_access_only_workload_token(monkeypatch) -> None:
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())
    monkeypatch.setenv("BIFROST_API_URL", "https://bifrost.example")
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", _token({"type": "access", "ver": 1, "fed": {"id": "x"}, "exp": exp}))
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

    credentials = EnvBackend().get("https://bifrost.example")

    assert credentials is not None
    assert credentials.refresh_token == ""
    assert credentials.expires_at != "2099-01-01T00:00:00+00:00"
    assert EnvBackend().list_urls() == ["https://bifrost.example"]


def test_env_backend_still_refuses_access_only_human_token(monkeypatch) -> None:
    monkeypatch.setenv("BIFROST_API_URL", "https://bifrost.example")
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", _token({"type": "access", "ver": 1, "exp": 2_000_000_000}))
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

    assert EnvBackend().get("https://bifrost.example") is None


def test_env_backend_workload_hint_rejects_malformed_or_unrepresentable_expiry(monkeypatch) -> None:
    monkeypatch.setenv("BIFROST_API_URL", "https://bifrost.example")
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
    backend = EnvBackend()

    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", _token(["not", "claims"]))
    assert backend.get("https://bifrost.example") is None

    monkeypatch.setenv(
        "BIFROST_ACCESS_TOKEN", _token({"type": "access", "ver": 1, "fed": {}, "exp": 10**100})
    )
    assert backend.get("https://bifrost.example") is None


def test_env_backend_is_read_only_and_records_expired_workload_expiry(monkeypatch) -> None:
    expired = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())
    monkeypatch.setenv("BIFROST_API_URL", "https://bifrost.example")
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", _token({"type": "access", "ver": 1, "fed": {}, "exp": expired}))
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
    backend = EnvBackend()

    before = backend.get("https://bifrost.example")
    assert before is not None
    backend.save(Credentials("https://another.example", "other", "refresh", "2099-01-01T00:00:00+00:00"))
    backend.clear("https://bifrost.example")

    assert backend.get("https://bifrost.example") == before
    assert backend.list_urls() == ["https://bifrost.example"]
