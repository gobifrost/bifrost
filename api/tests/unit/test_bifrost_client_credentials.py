"""Tests for BifrostClient credential resolution edge cases."""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def isolated_credentials(tmp_path, monkeypatch):
    from bifrost import credentials as creds_mod

    monkeypatch.setattr(
        creds_mod,
        "get_credentials_path",
        lambda: tmp_path / "credentials.json",
    )
    monkeypatch.setattr(creds_mod, "get_config_path", lambda: tmp_path / "config.json")
    creds_mod._reset_persistent_backend_for_tests()
    monkeypatch.setattr(creds_mod, "_persistent_backend", creds_mod.JsonBackend())
    monkeypatch.delenv("BIFROST_API_URL", raising=False)
    monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
    yield creds_mod
    creds_mod._reset_persistent_backend_for_tests()


def test_get_instance_does_not_report_default_ambiguity_when_env_selects_url(
    isolated_credentials, monkeypatch
) -> None:
    from bifrost import client as client_mod

    if hasattr(client_mod._thread_local, "bifrost_client"):
        delattr(client_mod._thread_local, "bifrost_client")

    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    isolated_credentials.save_credentials(
        "https://first.example.com", "at1", "rt1", expired_at
    )
    isolated_credentials.save_credentials(
        "https://second.example.com", "at2", "rt2", expired_at
    )
    monkeypatch.setenv("BIFROST_API_URL", "https://second.example.com")

    async def refresh_fails() -> bool:
        return False

    async def login_fails(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(client_mod, "refresh_tokens", refresh_fails)
    monkeypatch.setattr(client_mod, "login_flow", login_fails)

    with pytest.raises(RuntimeError, match="Not logged in"):
        client_mod.BifrostClient.get_instance(require_auth=True)
