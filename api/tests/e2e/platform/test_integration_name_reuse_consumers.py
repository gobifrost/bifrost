"""Consumers resolve only active integrations after a name is reused."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from bifrost.manifest import Manifest, ManifestIntegration, ManifestIntegrationConfigSchema
from src.models.orm.config import Config
from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
from src.services.manifest_import import ManifestResolver


pytestmark = pytest.mark.e2e


async def test_manifest_import_ignores_deleted_name_and_keeps_its_values(
    db_session, tmp_path
) -> None:
    name = f"manifest-reused-{uuid4().hex[:8]}"
    deleted = Integration(id=uuid4(), name=name, is_deleted=True)
    db_session.add(deleted)
    await db_session.flush()
    old_schema = IntegrationConfigSchema(
        integration_id=deleted.id,
        key="retired_endpoint",
        type="string",
    )
    db_session.add(old_schema)
    await db_session.flush()
    db_session.add_all([
        Config(
            key="retired_endpoint",
            value={"value": "https://retired.example"},
            integration_id=deleted.id,
            config_schema_id=old_schema.id,
            updated_by="test",
        ),
        IntegrationMapping(integration_id=deleted.id, entity_id="retired-tenant"),
    ])
    await db_session.flush()

    fresh_id = uuid4()
    manifest = Manifest(
        integrations={
            name: ManifestIntegration(
                id=str(fresh_id),
                name=name,
                config_schema=[
                    ManifestIntegrationConfigSchema(key="active_endpoint", type="string")
                ],
            )
        }
    )

    await ManifestResolver(db_session).plan_import(manifest, work_dir=tmp_path)
    await db_session.flush()

    rows = (
        await db_session.execute(select(Integration).where(Integration.name == name))
    ).scalars().all()
    assert len(rows) == 2
    active = next(row for row in rows if not row.is_deleted)
    assert active.id == fresh_id
    assert (
        await db_session.execute(
            select(Config).where(Config.integration_id == active.id)
        )
    ).scalars().all() == []
    assert (
        await db_session.execute(
            select(IntegrationMapping).where(IntegrationMapping.integration_id == active.id)
        )
    ).scalars().all() == []
    assert (
        await db_session.execute(
            select(Config).where(Config.integration_id == deleted.id)
        )
    ).scalars().all()
