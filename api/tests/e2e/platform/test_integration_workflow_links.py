"""Integration shortcuts survive the shared manifest/DB import boundary."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from bifrost.manifest import ManifestIntegration, ManifestOAuthProvider
from src.models.orm.integrations import Integration
from src.models.orm.oauth import OAuthProvider
from src.services.manifest_import import ManifestResolver
from src.services.manifest_generator import serialize_integration


@pytest.mark.e2e
async def test_manifest_import_inserts_and_updates_integration_workflow_links(db_session):
    integration_id = uuid4()
    name = f"linked-manifest-{uuid4()}"
    link = {"workflow_id": str(uuid4()), "label": "Sync Now", "pinned": True}
    manifest = ManifestIntegration(id=str(integration_id), name=name, workflow_links=[link])
    resolver = ManifestResolver(db_session)
    await resolver._resolve_integration(name, manifest)
    db_session.expire_all()
    integration = (await db_session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one()
    assert integration.workflow_links == [link]
    exported = serialize_integration(integration)
    assert exported.workflow_links[0].model_dump() == link
    manifest.workflow_links = []
    await resolver._resolve_integration(name, manifest)
    db_session.expire_all()
    integration = (await db_session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one()
    assert integration.workflow_links == []


async def test_workload_identity_manifest_round_trip_preserves_empty_client_id_and_metadata(
    db_session,
):
    """System-assigned workload identity must not become the OAuth setup sentinel."""
    integration_id = uuid4()
    name = f"workload-manifest-{uuid4()}"
    manifest = ManifestIntegration(
        id=str(integration_id),
        name=name,
        oauth_provider=ManifestOAuthProvider(
            provider_name=name,
            oauth_flow_type="workload_identity",
            client_id="",
            tenant_id="tenant-one",
        ),
    )
    resolver = ManifestResolver(db_session)
    await resolver._resolve_integration(name, manifest)
    await db_session.flush()

    provider = (
        await db_session.execute(
            select(OAuthProvider).where(OAuthProvider.integration_id == integration_id)
        )
    ).scalar_one()
    assert provider.client_id == ""
    assert provider.provider_metadata == {"tenant_id": "tenant-one"}

    exported = ManifestIntegration.from_row(
        (await db_session.execute(select(Integration).where(Integration.id == integration_id))).scalar_one(),
        oauth_provider=provider,
    )
    assert exported.oauth_provider is not None
    assert exported.oauth_provider.client_id == ""
    assert exported.oauth_provider.tenant_id == "tenant-one"

    provider.client_id = "stale-client-id"
    provider.provider_metadata = {"existing": "preserved", "tenant_id": "tenant-one"}
    await db_session.flush()
    await resolver._resolve_integration(name, manifest)
    await db_session.flush()
    await db_session.refresh(provider)
    assert provider.client_id == ""
    assert provider.provider_metadata == {
        "existing": "preserved",
        "tenant_id": "tenant-one",
    }
