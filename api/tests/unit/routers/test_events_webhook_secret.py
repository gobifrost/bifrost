"""``update_source`` treats the webhook signing secret as write-only.

``config["secret"]`` is never persisted in config. An absent key keeps the
stored secret, a value replaces it, and an empty or null value clears it. A
config change resubscribes the adapter, and the stored secret survives that.
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
from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.adapters.microsoft_graph import MicrosoftGraphAdapter
from src.services.webhooks.signing_secret import (
    read_signing_secret,
    with_signing_secret,
)


def _generic_source(secret: str | None):
    ws = SimpleNamespace(
        adapter_name="generic",
        integration_id=None,
        external_id=None,
        state=with_signing_secret({}, secret),
        expires_at=None,
        config={"signature_header": "X-Signature-256"},
        rate_limit_per_minute=60,
        rate_limit_window_seconds=60,
        rate_limit_enabled=True,
        updated_at=None,
        integration=None,
    )
    source = SimpleNamespace(
        id=uuid4(),
        name="hook",
        organization_id=None,
        source_type=EventSourceType.WEBHOOK,
        is_active=True,
        error_message=None,
        solution_id=None,
        webhook_source=ws,
        schedule_source=None,
        subscriptions=[],
        updated_at=None,
    )
    return source, ws


def _fake_db(source):
    db = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.get = AsyncMock(return_value=None)

    async def execute(_statement):
        result = MagicMock()
        result.unique.return_value.scalar_one.return_value = source
        result.unique.return_value.scalar_one_or_none.return_value = source
        return result

    db.execute = AsyncMock(side_effect=execute)
    return db


@pytest.fixture
def registry(monkeypatch):
    adapters = {
        "generic": GenericWebhookAdapter(),
        "microsoft_graph": MicrosoftGraphAdapter(),
    }
    fake = MagicMock()
    fake.get.side_effect = lambda name: adapters.get(name or "generic")
    monkeypatch.setattr("src.routers.events.get_adapter_registry", lambda: fake)
    monkeypatch.setattr(
        "src.routers.events._build_event_source_response",
        AsyncMock(return_value="response"),
    )
    monkeypatch.setattr("src.routers.events.emit_audit", AsyncMock())
    return fake


async def _update(source, body: dict):
    ctx = SimpleNamespace(org_id=None, user=SimpleNamespace(email="admin@example.com"))
    request = EventSourceUpdate.model_validate({"webhook": body})
    return await update_source(source.id, request, ctx, SimpleNamespace(), _fake_db(source))


@pytest.mark.asyncio
async def test_config_change_without_secret_keeps_the_stored_secret(registry):
    source, ws = _generic_source("original")

    await _update(source, {"config": {"signature_header": "X-Hub-Signature-256"}})

    assert ws.config == {"signature_header": "X-Hub-Signature-256"}
    assert read_signing_secret(ws.state) == "original"


@pytest.mark.asyncio
async def test_supplied_secret_replaces_and_never_lands_in_config(registry):
    source, ws = _generic_source("original")

    await _update(source, {"config": {"secret": "replacement"}})

    assert ws.config == {}
    assert "replacement" not in str(ws.state)
    assert read_signing_secret(ws.state) == "replacement"


@pytest.mark.asyncio
@pytest.mark.parametrize("cleared", [None, ""])
async def test_empty_secret_clears_it(registry, cleared):
    source, ws = _generic_source("original")

    await _update(source, {"config": {"secret": cleared}})

    assert ws.config == {}
    assert read_signing_secret(ws.state) is None


@pytest.mark.asyncio
async def test_adapter_without_a_signing_secret_rejects_one(registry):
    source, ws = _generic_source(None)
    source.organization_id = uuid4()
    ws.adapter_name = "microsoft_graph"

    with pytest.raises(HTTPException) as exc_info:
        await _update(source, {"config": {"secret": "unused"}})

    assert exc_info.value.status_code == 422
    assert "signing secret" in exc_info.value.detail
