"""Integration shortcuts survive the shared manifest/DB import boundary."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from bifrost.manifest import ManifestIntegration
from src.models.orm.integrations import Integration
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
