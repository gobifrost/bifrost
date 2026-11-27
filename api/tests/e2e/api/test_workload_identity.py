"""Live API coverage for host-managed integration workload identity."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.external_mcp import MCPConnection, MCPServer
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization


@pytest.mark.e2e
def test_workload_identity_returns_plain_config_without_oauth_tokens(
    e2e_client, platform_admin, secrets_reader_platform_admin
):
    name = f"workload-identity-{uuid4().hex[:8]}"
    integration_id = None
    try:
        integration = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert integration.status_code == 201, integration.text
        integration_id = integration.json()["id"]

        created = e2e_client.post(
            "/api/oauth/connections",
            headers=platform_admin.headers,
            json={
                "integration_id": integration_id,
                "oauth_flow_type": "workload_identity",
                "client_id": "runtime-client-id",
                "tenant_id": "runtime-tenant-id",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["oauth_flow_type"] == "workload_identity"
        assert created.json()["tenant_id"] == "runtime-tenant-id"
        assert created.json()["token_url"] == ""

        sdk = e2e_client.post(
            "/api/sdk/integrations/get",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert sdk.status_code == 200, sdk.text
        payload = sdk.json()
        assert payload["oauth"] is None
        assert payload["config"] == {
            "client_id": "runtime-client-id",
            "tenant_id": "runtime-tenant-id",
        }

        authorize = e2e_client.post(
            f"/api/oauth/connections/{name}/authorize",
            headers=platform_admin.headers,
            params={"redirect_uri": "https://example.invalid/callback"},
        )
        assert authorize.status_code == 400
        assert "does not obtain or refresh OAuth tokens" in authorize.json()["detail"]

        refresh = e2e_client.post(
            f"/api/oauth/connections/{name}/refresh",
            headers=platform_admin.headers,
        )
        assert refresh.status_code == 400
        assert "does not obtain or refresh OAuth tokens" in refresh.json()["detail"]

        credentials = e2e_client.get(
            f"/api/oauth/credentials/{name}", headers=platform_admin.headers
        )
        assert credentials.status_code == 200, credentials.text
        assert credentials.json()["credentials"] is None
        assert credentials.json()["status"] == "not_connected"
    finally:
        if integration_id:
            e2e_client.delete(
                f"/api/oauth/connections/{name}", headers=platform_admin.headers
            )
            e2e_client.delete(
                f"/api/integrations/{integration_id}", headers=platform_admin.headers
            )


@pytest.mark.e2e
async def test_workload_identity_transition_refuses_shared_mcp_tokens_then_clears_stale_oauth(
    db_session, e2e_client, platform_admin
):
    """A mode switch cannot silently invalidate MCP, and cannot retain OAuth state."""
    name = f"workload-transition-{uuid4().hex[:8]}"
    integration = Integration(name=name)
    db_session.add(integration)
    await db_session.flush()
    provider = OAuthProvider(
        provider_name=name,
        integration_id=integration.id,
        oauth_flow_type="authorization_code",
        client_id="legacy-client-id",
        encrypted_client_secret=b"legacy-secret",
        authorization_url="https://provider.example.invalid/authorize",
        token_url="https://provider.example.invalid/token",
        scopes=["read"],
    )
    db_session.add(provider)
    await db_session.flush()
    token = OAuthToken(
        provider_id=provider.id,
        encrypted_access_token=b"stale-access-token",
        encrypted_refresh_token=b"stale-refresh-token",
        status="completed",
    )
    db_session.add(token)
    db_session.add(IntegrationMapping(integration_id=integration.id, entity_id="test-tenant", oauth_token_id=token.id))
    organization = Organization(name=f"workload-mcp-{uuid4().hex[:8]}", created_by="test")
    server = MCPServer(
        name=f"workload-server-{uuid4().hex[:8]}",
        server_url="https://mcp.example.invalid",
    )
    db_session.add_all([organization, server])
    await db_session.flush()
    mcp_connection = MCPConnection(
        server_id=server.id,
        organization_id=organization.id,
        client_id="mcp-client",
        encrypted_client_secret="encrypted-mcp-secret",
        service_oauth_token_id=token.id,
    )
    db_session.add(mcp_connection)
    await db_session.commit()

    provider_id = provider.id
    integration_id = integration.id
    server_id = server.id
    organization_id = organization.id
    mcp_connection_id = mcp_connection.id

    try:
        refused = e2e_client.put(
            f"/api/oauth/connections/{name}",
            headers=platform_admin.headers,
            json={"oauth_flow_type": "workload_identity"},
        )
        assert refused.status_code == 409, refused.text
        assert "used by MCP credentials" in refused.json()["detail"]

        await db_session.execute(delete(MCPConnection).where(MCPConnection.id == mcp_connection_id))
        await db_session.commit()

        switched = e2e_client.put(
            f"/api/oauth/connections/{name}",
            headers=platform_admin.headers,
            json={"oauth_flow_type": "workload_identity"},
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["oauth_flow_type"] == "workload_identity"
        assert switched.json()["token_url"] == ""

        db_session.expire_all()
        refreshed_provider = await db_session.get(OAuthProvider, provider_id)
        assert refreshed_provider is not None
        assert refreshed_provider.encrypted_client_secret == b""
        assert refreshed_provider.authorization_url is None
        assert refreshed_provider.token_url is None
        assert (
            await db_session.scalar(
                select(OAuthToken.id).where(OAuthToken.provider_id == provider_id)
            )
            is None
        )
        mapping = await db_session.scalar(
            select(IntegrationMapping).where(IntegrationMapping.integration_id == integration_id)
        )
        assert mapping is not None and mapping.oauth_token_id is None

        # Imported legacy rows may retain OAuth fields. The flow guard must still win.
        refreshed_provider.authorization_url = "https://provider.example.invalid/stale-authorize"
        stale_token = OAuthToken(
            provider_id=provider_id,
            encrypted_access_token=b"stale-after-switch",
            status="completed",
        )
        db_session.add(stale_token)
        await db_session.commit()

        authorize = e2e_client.post(
            f"/api/oauth/connections/{name}/authorize",
            headers=platform_admin.headers,
            params={"redirect_uri": "https://example.invalid/callback"},
        )
        assert authorize.status_code == 400
        assert "does not obtain or refresh OAuth tokens" in authorize.json()["detail"]
        credentials = e2e_client.get(
            f"/api/oauth/credentials/{name}", headers=platform_admin.headers
        )
        assert credentials.status_code == 200
        assert credentials.json()["credentials"] is None
    finally:
        await db_session.execute(
            delete(IntegrationMapping).where(IntegrationMapping.integration_id == integration_id)
        )
        await db_session.execute(delete(MCPConnection).where(MCPConnection.server_id == server_id))
        await db_session.execute(delete(MCPServer).where(MCPServer.id == server_id))
        await db_session.execute(delete(Organization).where(Organization.id == organization_id))
        await db_session.execute(delete(OAuthToken).where(OAuthToken.provider_id == provider_id))
        await db_session.execute(delete(OAuthProvider).where(OAuthProvider.id == provider_id))
        await db_session.execute(delete(Integration).where(Integration.id == integration_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_existing_oauth_shell_can_still_be_edited_incrementally(
    db_session, e2e_client, platform_admin
):
    """Workload Identity's optional fields must not tighten existing shell edits."""
    name = f"oauth-shell-{uuid4().hex[:8]}"
    integration = Integration(name=name)
    db_session.add(integration)
    await db_session.flush()
    integration_id = integration.id
    provider = OAuthProvider(
        provider_name=name, integration_id=integration_id,
        oauth_flow_type="authorization_code", client_id="",
        encrypted_client_secret=b"",
    )
    db_session.add(provider)
    await db_session.commit()
    try:
        response = e2e_client.put(
            f"/api/oauth/connections/{name}", headers=platform_admin.headers,
            json={"name": "Renamed OAuth shell"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Renamed OAuth shell"
        invalid = e2e_client.put(
            f"/api/oauth/connections/{name}", headers=platform_admin.headers,
            json={"client_id": ""},
        )
        assert invalid.status_code == 400
    finally:
        await db_session.execute(delete(OAuthProvider).where(OAuthProvider.integration_id == integration_id))
        await db_session.execute(delete(Integration).where(Integration.id == integration_id))
        await db_session.commit()
