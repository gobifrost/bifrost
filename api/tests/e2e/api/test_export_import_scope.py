"""E2E coverage for export/import exact-scope org handling."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.tables import Table


def _json_upload(payload: dict) -> dict:
    return {
        "file": (
            "import.json",
            json.dumps(payload),
            "application/json",
        )
    }


def _multipart_auth_headers(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.access_token}"}


@pytest.mark.e2e
class TestExportImportExactScope:
    async def test_config_import_updates_org_row_without_touching_global(
        self,
        e2e_client,
        platform_admin,
        org1,
        db_session: AsyncSession,
    ):
        key = f"exact_scope_cfg_{uuid4().hex[:8]}"
        org_id = UUID(org1["id"])

        db_session.add_all(
            [
                Config(
                    key=key,
                    value={"value": "global-old"},
                    config_type=ConfigType.STRING,
                    organization_id=None,
                    updated_by="seed",
                ),
                Config(
                    key=key,
                    value={"value": "org-old"},
                    config_type=ConfigType.STRING,
                    organization_id=org_id,
                    updated_by="seed",
                ),
            ]
        )
        await db_session.commit()

        payload = {
            "entity_type": "configs",
            "item_count": 1,
            "items": [
                {
                    "key": key,
                    "value": "org-new",
                    "config_type": "string",
                    "organization_name": org1["name"],
                }
            ],
        }
        response = e2e_client.post(
            "/api/export-import/import/configs",
            headers=_multipart_auth_headers(platform_admin),
            files=_json_upload(payload),
            data={"replace_existing": "true"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["updated"] == 1
        assert body["created"] == 0
        assert body["errors"] == 0

        db_session.expire_all()
        rows = (
            await db_session.execute(select(Config).where(Config.key == key))
        ).scalars().all()
        by_org = {row.organization_id: row for row in rows}

        assert by_org[None].value == {"value": "global-old"}
        assert by_org[org_id].value == {"value": "org-new"}

    async def test_table_import_forced_global_updates_global_not_org(
        self,
        e2e_client,
        platform_admin,
        org1,
        db_session: AsyncSession,
    ):
        name = f"exact_scope_table_{uuid4().hex[:8]}"
        org_id = UUID(org1["id"])

        db_session.add_all(
            [
                Table(
                    name=name,
                    description="global-old",
                    schema={"fields": [{"name": "global"}]},
                    organization_id=None,
                    created_by="seed",
                ),
                Table(
                    name=name,
                    description="org-old",
                    schema={"fields": [{"name": "org"}]},
                    organization_id=org_id,
                    created_by="seed",
                ),
            ]
        )
        await db_session.commit()

        payload = {
            "entity_type": "tables",
            "item_count": 1,
            "items": [
                {
                    "name": name,
                    "description": "global-new",
                    "schema": {"fields": [{"name": "updated"}]},
                    "organization_id": str(org_id),
                    "documents": [],
                }
            ],
        }
        response = e2e_client.post(
            "/api/export-import/import/tables",
            headers=_multipart_auth_headers(platform_admin),
            files=_json_upload(payload),
            data={
                "replace_existing": "true",
                "target_organization_id": "global",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["updated"] == 1
        assert body["created"] == 0
        assert body["errors"] == 0

        db_session.expire_all()
        rows = (
            await db_session.execute(select(Table).where(Table.name == name))
        ).scalars().all()
        by_org = {row.organization_id: row for row in rows}

        assert by_org[None].description == "global-new"
        assert by_org[None].schema == {"fields": [{"name": "updated"}]}
        assert by_org[org_id].description == "org-old"
        assert by_org[org_id].schema == {"fields": [{"name": "org"}]}

    async def test_integration_mapping_import_updates_exact_org_and_preserves_token(
        self,
        e2e_client,
        platform_admin,
        org1,
        db_session: AsyncSession,
    ):
        integration_name = f"ExactScopeIntegration{uuid4().hex[:8]}"
        org_id = UUID(org1["id"])

        integration = Integration(name=integration_name, is_deleted=False)
        db_session.add(integration)
        await db_session.flush()

        provider = OAuthProvider(
            provider_name=f"exact-provider-{uuid4().hex[:8]}",
            client_id="client-id",
            encrypted_client_secret=b"secret",
            integration_id=integration.id,
            organization_id=org_id,
            created_by=str(platform_admin.user_id),
        )
        db_session.add(provider)
        await db_session.flush()

        token = OAuthToken(
            organization_id=org_id,
            provider_id=provider.id,
            encrypted_access_token=b"access",
            encrypted_refresh_token=b"refresh",
            status="connected",
        )
        db_session.add(token)
        await db_session.flush()
        integration_id = integration.id
        token_id = token.id

        db_session.add_all(
            [
                IntegrationMapping(
                    integration_id=integration_id,
                    organization_id=None,
                    entity_id="global-old",
                    entity_name="Global Old",
                ),
                IntegrationMapping(
                    integration_id=integration_id,
                    organization_id=org_id,
                    entity_id="org-old",
                    entity_name="Org Old",
                    oauth_token_id=token_id,
                ),
            ]
        )
        await db_session.commit()

        payload = {
            "entity_type": "integrations",
            "item_count": 1,
            "items": [
                {
                    "name": integration_name,
                    "mappings": [
                        {
                            "organization_name": org1["name"],
                            "entity_id": "org-new",
                            "entity_name": "Org New",
                        }
                    ],
                }
            ],
        }
        response = e2e_client.post(
            "/api/export-import/import/integrations",
            headers=_multipart_auth_headers(platform_admin),
            files=_json_upload(payload),
            data={"replace_existing": "true"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["updated"] == 1
        assert body["errors"] == 0

        db_session.expire_all()
        rows = (
            await db_session.execute(
                select(IntegrationMapping).where(
                    IntegrationMapping.integration_id == integration_id
                )
            )
        ).scalars().all()
        by_org = {row.organization_id: row for row in rows}

        assert by_org[None].entity_id == "global-old"
        assert by_org[None].entity_name == "Global Old"
        assert by_org[org_id].entity_id == "org-new"
        assert by_org[org_id].entity_name == "Org New"
        assert by_org[org_id].oauth_token_id == token_id
