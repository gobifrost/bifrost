"""Mapped integration admission for authenticated webhook deliveries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.events.processor import EventProcessor
from src.services.webhooks.protocol import Deliver, Rejected


def _adapter(deliver: Deliver) -> SimpleNamespace:
    return SimpleNamespace(
        name="mapped_adapter",
        mapping_integration_name="Microsoft Teams Bot",
        handle_request=AsyncMock(return_value=deliver),
        get_mapping_entity_id=lambda _deliver, _config: "customer-tenant",
    )


def _source(integration: SimpleNamespace) -> tuple[SimpleNamespace, SimpleNamespace]:
    event_source = SimpleNamespace(id=uuid4())
    webhook_source = SimpleNamespace(
        adapter_name="mapped_adapter",
        config={"tenant_admission": "integration_mappings"},
        state={},
        integration=integration,
    )
    return event_source, webhook_source


def _mapping_result(*organization_ids):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(organization_ids)
    return result


@pytest.mark.asyncio
async def test_mapped_delivery_is_scoped_to_exact_organization(monkeypatch):
    integration = SimpleNamespace(
        id=uuid4(), name="Microsoft Teams Bot", is_deleted=False
    )
    organization_id = uuid4()
    session = MagicMock()
    session.execute = AsyncMock(return_value=_mapping_result(organization_id))
    processor = EventProcessor(session)
    deliver = Deliver(data={"tenant_id": "customer-tenant"})
    adapter = _adapter(deliver)
    monkeypatch.setattr(
        "src.services.events.processor.get_adapter", lambda _name: adapter
    )
    processor._process_delivery = AsyncMock(return_value=deliver)
    event_source, webhook_source = _source(integration)

    result = await processor.process_webhook(
        event_source,
        webhook_source,
        SimpleNamespace(),
    )

    assert result is deliver
    assert deliver.organization_id == organization_id
    processor._process_delivery.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_ids", [(), (uuid4(), uuid4())])
async def test_unknown_or_ambiguous_mapping_is_rejected(
    monkeypatch, organization_ids
):
    integration = SimpleNamespace(
        id=uuid4(), name="Microsoft Teams Bot", is_deleted=False
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=_mapping_result(*organization_ids))
    processor = EventProcessor(session)
    deliver = Deliver(data={"tenant_id": "customer-tenant"})
    adapter = _adapter(deliver)
    monkeypatch.setattr(
        "src.services.events.processor.get_adapter", lambda _name: adapter
    )
    processor._process_delivery = AsyncMock()
    event_source, webhook_source = _source(integration)

    result = await processor.process_webhook(
        event_source,
        webhook_source,
        SimpleNamespace(),
    )

    assert isinstance(result, Rejected)
    assert result.status_code == 403
    processor._process_delivery.assert_not_awaited()
