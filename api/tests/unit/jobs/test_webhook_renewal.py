"""Tests for provider webhook renewal fallback behavior."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.jobs.schedulers.webhook_renewal import _renew_or_recreate
from src.services.webhooks.protocol import RenewResult, SubscribeResult


@pytest.mark.asyncio
async def test_keeps_subscription_when_renewal_succeeds():
    renewed = RenewResult(expires_at=datetime.now(timezone.utc))
    adapter = AsyncMock()
    adapter.renew.return_value = renewed

    result, recreated = await _renew_or_recreate(
        adapter,
        {
            "external_id": "existing",
            "state": {},
            "callback_url": "https://example.com/api/hooks/source",
            "config": {},
        },
        integration=None,
    )

    assert result is renewed
    assert recreated is False
    adapter.subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_recreates_subscription_when_renewal_is_rejected():
    replacement = SubscribeResult(external_id="replacement")
    adapter = AsyncMock()
    adapter.renew.return_value = None
    adapter.subscribe.return_value = replacement
    webhook = {
        "external_id": "expired",
        "state": {"client_state": "old"},
        "callback_url": "https://example.com/api/hooks/source",
        "config": {"resource": "/users/1/messages"},
    }

    result, recreated = await _renew_or_recreate(adapter, webhook, integration="auth")

    assert result is replacement
    assert recreated is True
    adapter.subscribe.assert_awaited_once_with(
        callback_url=webhook["callback_url"],
        config=webhook["config"],
        integration="auth",
    )
