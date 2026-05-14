"""Tests for event webhook callback URL construction."""

from types import SimpleNamespace
from uuid import uuid4

from src.routers import events


def test_build_callback_url_uses_public_url(monkeypatch):
    source_id = uuid4()
    monkeypatch.setattr(
        events,
        "get_settings",
        lambda: SimpleNamespace(public_url="https://bifrost.example.com/"),
    )

    assert (
        events._build_callback_url(source_id)
        == f"https://bifrost.example.com/api/hooks/{source_id}"
    )
