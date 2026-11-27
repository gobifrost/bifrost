"""Host-managed workload identity resolution stays plain-config and scoped."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


def _mapping_with_workload_identity():
    provider = SimpleNamespace(
        id=uuid4(),
        oauth_flow_type="workload_identity",
        client_id="provider-client-id",
        provider_metadata={"tenant_id": "provider-tenant-id"},
    )
    integration = SimpleNamespace(
        id=uuid4(),
        default_entity_id=None,
        config_schema=[],
        oauth_provider=provider,
    )
    return SimpleNamespace(
        integration_id=integration.id,
        integration=integration,
        entity_id="mapped-entity",
        entity_name="Mapped Entity",
    )


@pytest.mark.asyncio
async def test_mapping_configuration_overrides_workload_identity_defaults(monkeypatch):
    from shared.sdk_integrations import get_sdk_integration_dict

    mapping = _mapping_with_workload_identity()
    repo = SimpleNamespace(
        get_integration_for_org=AsyncMock(return_value=mapping),
        get_config_for_mapping=AsyncMock(
            return_value={"client_id": "mapping-client-id", "tenant_id": "mapping-tenant-id"}
        ),
    )
    monkeypatch.setattr(
        "src.repositories.integrations.IntegrationsRepository", lambda _: repo
    )
    monkeypatch.setattr("shared.sdk_integrations.require_permission", lambda *_: None)
    monkeypatch.setattr("shared.sdk_integrations.allows_permission", lambda *_: False)
    monkeypatch.setattr(
        "shared.sdk_integrations.external_credential_restrictions", lambda _: False
    )

    result = await get_sdk_integration_dict(
        AsyncMock(), name="Workload", org_id=uuid4(), caller=SimpleNamespace()
    )

    assert result is not None
    assert result["oauth"] is None
    assert result["config"] == {
        "client_id": "mapping-client-id",
        "tenant_id": "mapping-tenant-id",
    }


@pytest.mark.asyncio
async def test_external_mapping_does_not_receive_global_workload_identity_defaults(
    monkeypatch,
):
    from shared.sdk_integrations import get_sdk_integration_dict

    mapping = _mapping_with_workload_identity()
    repo = SimpleNamespace(
        get_integration_for_org=AsyncMock(return_value=mapping),
        get_config_for_mapping=AsyncMock(return_value={"org_only": "value"}),
    )
    monkeypatch.setattr(
        "src.repositories.integrations.IntegrationsRepository", lambda _: repo
    )
    monkeypatch.setattr("shared.sdk_integrations.require_permission", lambda *_: None)
    monkeypatch.setattr("shared.sdk_integrations.allows_permission", lambda *_: False)
    monkeypatch.setattr(
        "shared.sdk_integrations.external_credential_restrictions", lambda _: True
    )

    result = await get_sdk_integration_dict(
        AsyncMock(), name="Workload", org_id=uuid4(), caller=SimpleNamespace()
    )

    assert result is not None
    assert result["oauth"] is None
    assert result["config"] == {"org_only": "value"}
