"""Tests for provider-managed webhook recovery and cleanup."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.webhooks.lifecycle import (
    resubscribe_provider,
    unsubscribe_provider,
)
from src.services.webhooks.protocol import SubscribeResult


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        organization_id=None,
        error_message=None,
        updated_at=None,
        webhook_source=SimpleNamespace(
            adapter_name="provider",
            integration_id=None,
            external_id="old-subscription",
            state={"client_state": "old"},
            config={"resource": "/users/1/messages"},
            expires_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            updated_at=None,
        ),
    )


@pytest.mark.asyncio
async def test_resubscribe_replaces_provider_state_after_cleanup(monkeypatch):
    source = _source()
    db = MagicMock()
    db.commit = AsyncMock()
    replacement = SubscribeResult(
        external_id="new-subscription",
        state={"client_state": "new"},
        expires_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    adapter = SimpleNamespace(
        requires_integration=None,
        unsubscribe=AsyncMock(),
        subscribe=AsyncMock(return_value=replacement),
    )
    registry = MagicMock()
    registry.get.return_value = adapter
    monkeypatch.setattr(
        "src.services.webhooks.lifecycle.get_adapter_registry",
        lambda: registry,
    )

    await resubscribe_provider(
        db,
        source,
        "https://example.test/api/hooks/source-1",
    )

    adapter.unsubscribe.assert_awaited_once_with(
        external_id="old-subscription",
        state={"client_state": "old"},
        integration=None,
    )
    adapter.subscribe.assert_awaited_once()
    assert source.webhook_source.external_id == "new-subscription"
    assert source.webhook_source.state == {"client_state": "new"}
    assert source.error_message is None
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_resubscribe_retains_source_and_records_recreation_failure(monkeypatch):
    source = _source()
    db = MagicMock()
    db.commit = AsyncMock()
    adapter = SimpleNamespace(
        requires_integration=None,
        unsubscribe=AsyncMock(),
        subscribe=AsyncMock(side_effect=ValueError("callback rejected")),
    )
    registry = MagicMock()
    registry.get.return_value = adapter
    monkeypatch.setattr(
        "src.services.webhooks.lifecycle.get_adapter_registry",
        lambda: registry,
    )

    with pytest.raises(ValueError, match="callback rejected"):
        await resubscribe_provider(
            db,
            source,
            "https://example.test/api/hooks/source-1",
        )

    assert source.webhook_source.external_id is None
    assert source.error_message == (
        "Provider resubscription failed: callback rejected"
    )
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_unsubscribe_propagates_provider_failure(monkeypatch):
    source = _source()
    adapter = SimpleNamespace(
        requires_integration=None,
        unsubscribe=AsyncMock(side_effect=ValueError("Graph unavailable")),
    )
    registry = MagicMock()
    registry.get.return_value = adapter
    monkeypatch.setattr(
        "src.services.webhooks.lifecycle.get_adapter_registry",
        lambda: registry,
    )

    with pytest.raises(ValueError, match="Graph unavailable"):
        await unsubscribe_provider(MagicMock(), source)
