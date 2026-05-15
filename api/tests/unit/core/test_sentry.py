"""Tests for optional Sentry initialization."""

import importlib
import sys
from types import SimpleNamespace

import pytest

from src.config import get_settings
from src.core import sentry as sentry_core


class FakeSentrySdk:
    def __init__(self):
        self.calls = []

    def init(self, **kwargs):
        self.calls.append(kwargs)


def settings(**overrides):
    values = {
        "sentry_dsn": None,
        "sentry_traces_sample_rate": 0.0,
        "sentry_profiles_sample_rate": 0.0,
        "sentry_send_default_pii": False,
        "environment": "testing",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_configure_sentry_noops_without_dsn():
    fake_sdk = FakeSentrySdk()

    assert sentry_core.configure_sentry(settings(), sentry_sdk_module=fake_sdk) is False
    assert fake_sdk.calls == []


def test_configure_sentry_uses_privacy_first_defaults():
    fake_sdk = FakeSentrySdk()

    configured = sentry_core.configure_sentry(
        settings(sentry_dsn="https://example@sentry.invalid/1"),
        sentry_sdk_module=fake_sdk,
    )

    assert configured is True
    init_kwargs = fake_sdk.calls[0]
    assert init_kwargs["dsn"] == "https://example@sentry.invalid/1"
    assert init_kwargs["environment"] == "testing"
    assert init_kwargs["send_default_pii"] is False
    assert init_kwargs["enable_logs"] is False
    assert init_kwargs["traces_sample_rate"] == pytest.approx(0.0)
    assert init_kwargs["profile_session_sample_rate"] == pytest.approx(0.0)
    assert callable(init_kwargs["before_send"])


def test_app_startup_configures_sentry_from_env(monkeypatch):
    captured_settings = []

    monkeypatch.setenv("BIFROST_SENTRY_DSN", "https://example@sentry.invalid/1")
    monkeypatch.setenv("BIFROST_SENTRY_TRACES_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("BIFROST_SENTRY_PROFILES_SAMPLE_RATE", "0.10")

    def fake_configure_sentry(settings):
        captured_settings.append(settings)
        return True

    get_settings.cache_clear()
    monkeypatch.setattr(sentry_core, "configure_sentry", fake_configure_sentry)
    sys.modules.pop("src.main", None)

    try:
        main_module = importlib.import_module("src.main")
        assert main_module.app.title == "Bifrost API"
    finally:
        sys.modules.pop("src.main", None)
        get_settings.cache_clear()

    assert len(captured_settings) == 1
    settings = captured_settings[0]
    assert settings.sentry_dsn == "https://example@sentry.invalid/1"
    assert settings.sentry_traces_sample_rate == pytest.approx(0.25)
    assert settings.sentry_profiles_sample_rate == pytest.approx(0.10)
    assert settings.sentry_send_default_pii is False


def test_before_send_scrubs_sensitive_request_data():
    event = {
        "request": {
            "url": "https://bifrost.example/api?access_token=secret-token&ok=1",
            "headers": {
                "Authorization": "Bearer secret-token",
                "Cookie": "access_token=secret-cookie",
                "X-Api-Key": "secret-key",
                "X-Request-ID": "req-123",
            },
            "cookies": {"session": "secret-session"},
            "data": {
                "password": "secret-password",
                "nested": {"refresh_token": "secret-refresh"},
                "safe": "value",
            },
        },
        "extra": {
            "database_url": "postgresql://user:pass@example/db",
            "workflow": {"name": "safe"},
        },
    }

    scrubbed = sentry_core.before_send(event, hint={})

    request = scrubbed["request"]
    assert "secret-token" not in request["url"]
    assert request["headers"]["Authorization"] == "[Filtered]"
    assert request["headers"]["Cookie"] == "[Filtered]"
    assert request["headers"]["X-Api-Key"] == "[Filtered]"
    assert request["headers"]["X-Request-ID"] == "req-123"
    assert request["cookies"] == "[Filtered]"
    assert request["data"]["password"] == "[Filtered]"
    assert request["data"]["nested"]["refresh_token"] == "[Filtered]"
    assert request["data"]["safe"] == "value"
    assert scrubbed["extra"]["database_url"] == "[Filtered]"
    assert scrubbed["extra"]["workflow"]["name"] == "safe"
