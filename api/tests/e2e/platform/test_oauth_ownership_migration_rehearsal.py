"""Rehearse credential cleanup against pre-migration data, including MCP ownership."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config,
    _temporary_migration_database_url,
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e
LEGACY_REVISION = "20261010_agent_run_as_user"


async def _seed(database_url: str, *, shared: str) -> dict[str, str]:
    ids = {key: str(uuid4()) for key in (
        "deleted", "live", "deleted_provider", "live_provider", "mcp_provider",
        "deleted_token", "live_token", "mcp_token", "server", "mapping", "org", "connection",
    )}

    async def seed(connection: AsyncConnection) -> None:
        for key, deleted in (("deleted", True), ("live", False)):
            await connection.execute(sa.text(
                "INSERT INTO integrations (id, name, is_deleted) VALUES (:id, :name, :deleted)"
            ), {"id": ids[key], "name": key, "deleted": deleted})
        for key, integration in (("deleted", "deleted"), ("live", "live"), ("mcp", None)):
            await connection.execute(sa.text("""
                INSERT INTO oauth_providers (id, provider_name, oauth_flow_type, client_id,
                    encrypted_client_secret, scopes, token_url_defaults, provider_metadata, integration_id)
                VALUES (:id, :name, 'authorization_code', 'client', '\\x01', '[]', '{}', '{}', :integration)
            """), {"id": ids[f"{key}_provider"], "name": key,
                     "integration": ids[integration] if integration else None})
            await connection.execute(sa.text("""
                INSERT INTO oauth_tokens (id, provider_id, encrypted_access_token, encrypted_refresh_token, scopes)
                VALUES (:id, :provider, '\\x02', '\\x03', '[]')
            """), {"id": ids[f"{key}_token"], "provider": ids[f"{key}_provider"]})
        await connection.execute(sa.text("""
            INSERT INTO integration_mappings (id, integration_id, entity_id, oauth_token_id)
            VALUES (:id, :integration, 'tenant', :token)
        """), {"id": ids["mapping"], "integration": ids["deleted"], "token": ids["deleted_token"]})
        await connection.execute(sa.text("""
            INSERT INTO mcp_servers (id, name, server_url, oauth_provider_id)
            VALUES (:id, 'MCP', 'https://example.com/mcp', :provider)
        """), {"id": ids["server"], "provider": ids["deleted_provider" if shared == "server" else "mcp_provider"]})

        if shared == "service":
            await connection.execute(sa.text("""
                INSERT INTO organizations (id, name, domain, is_active, is_provider, settings, created_by)
                VALUES (:id, 'Org', 'example.com', TRUE, FALSE, '{}', 'rehearsal')
            """), {"id": ids["org"]})
            await connection.execute(sa.text("""
                INSERT INTO mcp_connections (id, server_id, organization_id, client_id,
                    encrypted_client_secret, service_oauth_token_id)
                VALUES (:id, :server, :org, 'client', 'encrypted', :token)
            """), {"id": ids["connection"], "server": ids["server"],
                     "org": ids["org"], "token": ids["deleted_token"]})

    await _run_in_database(database_url, seed)
    return ids


async def _read(database_url: str) -> dict[str, object]:
    async def read(connection: AsyncConnection) -> dict[str, object]:
        return {
            "providers": {str(row[0]) for row in await connection.execute(sa.text("SELECT id FROM oauth_providers"))},
            "tokens": {str(row[0]) for row in await connection.execute(sa.text("SELECT id FROM oauth_tokens"))},
            "mapping_token": (await connection.execute(sa.text("SELECT oauth_token_id FROM integration_mappings"))).scalar_one(),
        }
    return await _run_in_database(database_url, read)


@pytest.mark.parametrize("shared", ["none", "server", "service"])
def test_cleanup_deleted_credentials_preserves_active_consumers(shared: str) -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        ids = asyncio.run(_seed(database_url, shared=shared))
        if shared != "none":
            before = asyncio.run(_read(database_url))
            with pytest.raises(RuntimeError, match="MCP shares their ownership"):
                _upgrade(database_url, "head")
            assert asyncio.run(_read(database_url)) == before
        else:
            _upgrade(database_url, "head")
            remaining = asyncio.run(_read(database_url))
            assert remaining["providers"] == {ids["live_provider"], ids["mcp_provider"]}
            assert remaining["tokens"] == {ids["live_token"], ids["mcp_token"]}
            assert remaining["mapping_token"] is None
            # A repeated upgrade is harmless.
            _upgrade(database_url, "head")
            assert asyncio.run(_read(database_url)) == remaining
            with _temporary_migration_database_url(database_url):
                command.downgrade(_alembic_config(), LEGACY_REVISION)
            assert asyncio.run(_read(database_url)) == remaining
            _upgrade(database_url, "head")
            assert asyncio.run(_read(database_url)) == remaining
    finally:
        asyncio.run(_drop_database(database_name))
