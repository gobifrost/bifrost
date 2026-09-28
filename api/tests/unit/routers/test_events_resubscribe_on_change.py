"""``update_source`` resubscribes the provider webhook when the adapter,
integration, or config changes (RBAC R1b batch 4, events domain).

Order matters: the new adapter/integration/config is subscribed FIRST. Only
after that succeeds does the old one get unsubscribed (best-effort, logged
on failure). This means a provider failure on the new subscribe leaves the
Event Source's webhook fields completely untouched — no half-applied state —
and the request fails with a clear 502. See
``_resubscribe_webhook_on_change`` in ``src/routers/events.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.events import EventSourceUpdate
from src.models.enums import EventSourceType
from src.routers.events import update_source
from src.services.webhooks.protocol import SubscribeResult


def _fake_webhook_source(*, adapter_name="old_adapter", integration_id=None):
    return SimpleNamespace(
        adapter_name=adapter_name,
        integration_id=integration_id,
        external_id="old-ext-id",
        state={"secret": "old"},
        expires_at=None,
        config={},
        rate_limit_per_minute=60,
        rate_limit_window_seconds=60,
        rate_limit_enabled=True,
        updated_at=None,
        integration=None,
    )


def _fake_source(webhook_source):
    return SimpleNamespace(
        id=uuid4(),
        name="src",
        organization_id=None,
        source_type=EventSourceType.WEBHOOK,
        is_active=True,
        error_message=None,
        solution_id=None,
        webhook_source=webhook_source,
        schedule_source=None,
        subscriptions=[],
        updated_at=None,
    )


def _fake_db(source):
    db = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.get = AsyncMock(return_value=None)  # no Integration lookup needed by default

    async def execute(_statement):
        result = MagicMock()
        result.unique.return_value.scalar_one.return_value = source
        result.unique.return_value.scalar_one_or_none.return_value = source
        return result

    db.execute = AsyncMock(side_effect=execute)
    return db


def _ctx():
    return SimpleNamespace(org_id=None, user=SimpleNamespace(email="admin@example.com"))


@pytest.mark.asyncio
async def test_resubscribe_success_subscribes_new_then_unsubscribes_old(monkeypatch):
    ws = _fake_webhook_source(adapter_name="old_adapter")
    source = _fake_source(ws)
    db = _fake_db(source)

    calls: list[str] = []

    old_adapter = SimpleNamespace(
        requires_integration=False,
        unsubscribe=AsyncMock(side_effect=lambda **_kw: calls.append("unsubscribe_old")),
    )
    new_adapter = SimpleNamespace(
        requires_integration=False,
        subscribe=AsyncMock(
            side_effect=lambda **_kw: calls.append("subscribe_new")
            or SubscribeResult(external_id="new-ext-id", state={"secret": "new"})
        ),
    )
    registry = MagicMock()
    registry.get.side_effect = lambda name: {"old_adapter": old_adapter, "new_adapter": new_adapter}[name]
    monkeypatch.setattr("src.routers.events.get_adapter_registry", lambda: registry)
    monkeypatch.setattr(
        "src.routers.events._build_event_source_response",
        AsyncMock(return_value="response"),
    )

    request = EventSourceUpdate.model_validate(
        {"webhook": {"adapter_name": "new_adapter"}}
    )
    result = await update_source(source.id, request, _ctx(), SimpleNamespace(), db)

    assert result == "response"
    # New adapter subscribed BEFORE the old one was unsubscribed.
    assert calls == ["subscribe_new", "unsubscribe_old"]
    assert ws.adapter_name == "new_adapter"
    assert ws.external_id == "new-ext-id"
    assert ws.state == {"secret": "new"}
    assert source.error_message is None


@pytest.mark.asyncio
async def test_resubscribe_new_failure_leaves_webhook_source_untouched(monkeypatch):
    ws = _fake_webhook_source(adapter_name="old_adapter")
    source = _fake_source(ws)
    db = _fake_db(source)

    old_adapter = SimpleNamespace(requires_integration=False, unsubscribe=AsyncMock())
    new_adapter = SimpleNamespace(
        requires_integration=False,
        subscribe=AsyncMock(side_effect=RuntimeError("provider rejected")),
    )
    registry = MagicMock()
    registry.get.side_effect = lambda name: {"old_adapter": old_adapter, "new_adapter": new_adapter}[name]
    monkeypatch.setattr("src.routers.events.get_adapter_registry", lambda: registry)

    request = EventSourceUpdate.model_validate(
        {"webhook": {"adapter_name": "new_adapter"}}
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_source(source.id, request, _ctx(), SimpleNamespace(), db)

    assert exc_info.value.status_code == 502
    # The failed subscribe happened before any ORM attribute was mutated —
    # the webhook source is exactly as it was, and the old adapter was never
    # torn down.
    assert ws.adapter_name == "old_adapter"
    assert ws.external_id == "old-ext-id"
    old_adapter.unsubscribe.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resubscribe_old_unsubscribe_failure_is_logged_and_request_still_succeeds(
    monkeypatch, caplog
):
    ws = _fake_webhook_source(adapter_name="old_adapter")
    source = _fake_source(ws)
    db = _fake_db(source)

    old_adapter = SimpleNamespace(
        requires_integration=False,
        unsubscribe=AsyncMock(side_effect=RuntimeError("provider already gone")),
    )
    new_adapter = SimpleNamespace(
        requires_integration=False,
        subscribe=AsyncMock(
            return_value=SubscribeResult(external_id="new-ext-id", state={})
        ),
    )
    registry = MagicMock()
    registry.get.side_effect = lambda name: {"old_adapter": old_adapter, "new_adapter": new_adapter}[name]
    monkeypatch.setattr("src.routers.events.get_adapter_registry", lambda: registry)
    monkeypatch.setattr(
        "src.routers.events._build_event_source_response",
        AsyncMock(return_value="response"),
    )

    request = EventSourceUpdate.model_validate(
        {"webhook": {"adapter_name": "new_adapter"}}
    )

    with caplog.at_level("WARNING"):
        result = await update_source(source.id, request, _ctx(), SimpleNamespace(), db)

    # The request still succeeds — an old-unsubscribe failure is
    # best-effort/logged, not fatal (the reference lifecycle helper's
    # tolerance for a provider that already dropped the registration).
    assert result == "response"
    assert ws.adapter_name == "new_adapter"
    assert ws.external_id == "new-ext-id"
    assert any("unsubscribe" in r.message.lower() for r in caplog.records)
