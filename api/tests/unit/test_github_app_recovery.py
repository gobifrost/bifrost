"""Provider recovery bounds and uncertain-send fencing without external writes."""

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared import github_app_recovery as module
from shared.github_app_auth import GitHubAppError
from shared.github_app_client import AppCredentials, DeliveryPage


@pytest.fixture
def recovery(monkeypatch):
    row = SimpleNamespace(recovery_cursor=None, recovery_gap=False, redelivery_requests={})
    receipts = {}
    db = SimpleNamespace(get=AsyncMock(side_effect=lambda model, identity: receipts.get(identity)),
                         execute=AsyncMock(), commit=AsyncMock())

    @asynccontextmanager
    async def database():
        yield db

    client = SimpleNamespace(request=AsyncMock(), delivery_history=AsyncMock())
    monkeypatch.setattr(module, "get_db_context", database)
    monkeypatch.setattr(module, "credentials", AsyncMock(return_value=AppCredentials(123, "generation", "unused")))
    monkeypatch.setattr(module, "fence", AsyncMock(return_value=row))
    monkeypatch.setattr(module, "provider_client", lambda snapshot: client)
    return SimpleNamespace(row=row, db=db, receipts=receipts, client=client,
                           context=SimpleNamespace(report=AsyncMock()))


def failed_delivery(identity=1, guid="stable-guid"):
    return {"id": identity, "guid": guid, "delivered_at": datetime.now(timezone.utc).isoformat(), "status_code": 500}


@pytest.mark.asyncio
async def test_uncertain_post_is_durable_before_send_and_never_repeated_for_a_new_attempt(recovery):
    async def request(method, path):
        assert recovery.row.redelivery_requests["1"]["state"] == "sending"
        assert recovery.db.commit.await_count >= 2
        raise GitHubAppError("transport", "GitHub could not be reached", 502)

    recovery.client.request.side_effect = request
    recovery.client.delivery_history.return_value = DeliveryPage([failed_delivery()], None)
    with pytest.raises(GitHubAppError, match="reached"):
        await module.recover_provider_deliveries(recovery.context)
    assert recovery.row.redelivery_requests["1"]["state"] == "uncertain"
    assert recovery.row.recovery_gap is True
    # A provider retry has another numeric attempt id but the original GUID.
    recovery.client.request.reset_mock()
    recovery.client.delivery_history.return_value = DeliveryPage([failed_delivery(2)], None)
    result = await module.recover_provider_deliveries(recovery.context)
    assert result["redeliveries_requested"] == 0
    recovery.client.request.assert_not_awaited()
    assert list(recovery.row.redelivery_requests) == ["1"]


@pytest.mark.asyncio
async def test_local_acceptance_suppresses_provider_redelivery_after_lost_response(recovery):
    recovery.receipts[(123, "stable-guid")] = object()
    recovery.client.delivery_history.return_value = DeliveryPage([failed_delivery()], None)
    result = await module.recover_provider_deliveries(recovery.context)
    assert result == {"redeliveries_requested": 0, "history_scan_complete": True}
    recovery.client.request.assert_not_awaited()
    assert recovery.row.redelivery_requests == {}


@pytest.mark.asyncio
async def test_cancellation_during_post_retains_an_uncertain_send_fence(recovery):
    recovery.client.delivery_history.return_value = DeliveryPage([failed_delivery()], None)
    recovery.client.request.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await module.recover_provider_deliveries(recovery.context)
    assert recovery.row.redelivery_requests["1"]["state"] == "uncertain"
    assert recovery.row.recovery_gap is True


@pytest.mark.asyncio
async def test_overlap_recovers_a_failure_first_visible_below_the_previous_cursor(recovery):
    cursor = datetime.now(timezone.utc)
    recovery.row.recovery_cursor = cursor
    delivery = {**failed_delivery(), "delivered_at": (cursor - timedelta(minutes=2)).isoformat()}
    recovery.client.delivery_history.return_value = DeliveryPage([delivery], None)
    result = await module.recover_provider_deliveries(recovery.context)
    assert result["redeliveries_requested"] == 1
    recovery.client.request.assert_awaited_once_with("POST", "/app/hook/deliveries/1/attempts")


@pytest.mark.asyncio
async def test_full_ledger_cannot_advance_cursor_even_on_a_short_history_page(recovery):
    cursor = datetime.now(timezone.utc) - timedelta(hours=1)
    recovery.row.recovery_cursor = cursor
    recovery.row.redelivery_requests = {str(i): {"guid": f"old-{i}", "state": "requested",
        "requested_at": cursor.isoformat()} for i in range(1000)}
    recovery.client.delivery_history.return_value = DeliveryPage([failed_delivery(2000)], None)
    result = await module.recover_provider_deliveries(recovery.context)
    assert result == {"redeliveries_requested": 0, "history_scan_complete": False}
    assert recovery.row.recovery_cursor == cursor
    assert recovery.row.recovery_gap is True
    recovery.client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_page_budget_exposes_gap_and_preserves_cursor(recovery):
    cursor = datetime.now(timezone.utc) - timedelta(hours=1)
    recovery.row.recovery_cursor = cursor
    recovery.client.delivery_history.side_effect = [DeliveryPage(
        [{**failed_delivery(i, f"guid-{i}"), "status_code": 202} for i in range(100)], f"cursor-{page}")
        for page in range(module.MAX_RECOVERY_PAGES)]
    result = await module.recover_provider_deliveries(recovery.context)
    assert result["history_scan_complete"] is False
    assert recovery.client.delivery_history.await_count == module.MAX_RECOVERY_PAGES
    assert recovery.row.recovery_cursor == cursor
    assert recovery.row.recovery_gap is True
