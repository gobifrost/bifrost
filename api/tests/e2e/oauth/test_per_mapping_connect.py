"""E2E: per-mapping OAuth authorize endpoint returns a URL with our state token."""

from urllib.parse import urlparse, parse_qs
from uuid import UUID

import pytest
import pytest_asyncio

from src.models.orm import OAuthProvider, OAuthToken


@pytest.mark.e2e
class TestPerMappingAuthorize:
    """Test per-mapping OAuth authorize endpoint."""

    @pytest_asyncio.fixture
    async def integration_with_oauth(self, e2e_client, platform_admin, db_session):
        """Create an integration with an authorization_code OAuth provider."""
        from uuid import uuid4

        integration_name = f"e2e_per_mapping_oauth_{uuid4().hex[:8]}"

        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201, f"Create integration failed: {response.text}"
        integration = response.json()

        integration_id = UUID(integration["id"])
        oauth_provider = OAuthProvider(
            provider_name=f"test_provider_{uuid4().hex[:6]}",
            display_name="Test OAuth Provider",
            oauth_flow_type="authorization_code",
            client_id="test-client-id",
            encrypted_client_secret=b"encrypted_secret",
            authorization_url="https://login.example.com/authorize",
            token_url="https://login.example.com/token",
            scopes=["read", "write"],
            redirect_uri="/api/oauth/callback/test_provider",
            integration_id=integration_id,
        )
        db_session.add(oauth_provider)
        await db_session.commit()
        await db_session.refresh(oauth_provider)

        yield {"integration": integration, "oauth_provider": oauth_provider}

        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    @pytest.mark.asyncio
    async def test_authorize_for_mapping_returns_signed_state(
        self, e2e_client, platform_admin, integration_with_oauth, org1
    ):
        """Per-mapping authorize endpoint returns a URL with a signed state token."""
        integration = integration_with_oauth["integration"]
        oauth_provider = integration_with_oauth["oauth_provider"]

        # Create a mapping (no token yet)
        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "test-entity-123",
                "entity_name": "Test Entity",
            },
        )
        assert mapping_resp.status_code == 201, f"Create mapping failed: {mapping_resp.text}"
        mapping_id = mapping_resp.json()["id"]

        try:
            # Request authorize URL for this mapping
            resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}/oauth/authorize",
                headers=platform_admin.headers,
                json={"redirect_uri": "http://localhost:3000/callback"},
            )
            assert resp.status_code == 200, f"Authorize failed: {resp.text}"
            body = resp.json()
            assert "authorization_url" in body

            parsed = urlparse(body["authorization_url"])
            qs = parse_qs(parsed.query)
            assert "state" in qs, "state param missing from authorization URL"
            # The state must be our signed token (contains a "." separating body + sig)
            state_token = qs["state"][0]
            assert "." in state_token, "state token is not a signed token (missing '.')"

            # And it must round-trip back to our mapping
            from src.services.oauth_state import decode_state

            payload = decode_state(state_token)
            assert payload["mapping_id"] == mapping_id
            assert payload["provider_id"] == str(oauth_provider.id)
        finally:
            # Cleanup mapping
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_authorize_for_mapping_404_on_missing_mapping(
        self, e2e_client, platform_admin, integration_with_oauth
    ):
        """Returns 404 when mapping does not exist."""
        integration = integration_with_oauth["integration"]
        from uuid import uuid4

        fake_mapping_id = str(uuid4())
        resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings/{fake_mapping_id}/oauth/authorize",
            headers=platform_admin.headers,
            json={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_authorize_for_mapping_404_on_missing_integration(
        self, e2e_client, platform_admin
    ):
        """Returns 404 when integration does not exist."""
        from uuid import uuid4

        fake_integration_id = str(uuid4())
        fake_mapping_id = str(uuid4())
        resp = e2e_client.post(
            f"/api/integrations/{fake_integration_id}/mappings/{fake_mapping_id}/oauth/authorize",
            headers=platform_admin.headers,
            json={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_authorize_for_mapping_400_client_credentials(
        self, e2e_client, platform_admin, db_session, org1
    ):
        """Returns 400 for client_credentials flow (no authorization_url)."""
        from uuid import UUID, uuid4

        integration_name = f"e2e_cc_mapping_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()
        integration_id = UUID(integration["id"])

        oauth_provider = OAuthProvider(
            provider_name=f"cc_provider_{uuid4().hex[:6]}",
            oauth_flow_type="client_credentials",
            client_id="test-client-id",
            encrypted_client_secret=b"encrypted_secret",
            token_url="https://login.example.com/token",
            integration_id=integration_id,
        )
        db_session.add(oauth_provider)
        await db_session.commit()

        # Create a mapping
        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "cc-entity",
                "entity_name": "CC Entity",
            },
        )
        assert mapping_resp.status_code == 201
        mapping_id = mapping_resp.json()["id"]

        try:
            resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}/oauth/authorize",
                headers=platform_admin.headers,
                json={"redirect_uri": "http://localhost:3000/callback"},
            )
            assert resp.status_code == 400
            assert "client_credentials" in resp.json()["detail"]
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
class TestPerMappingDisconnect:
    """Test per-mapping OAuth disconnect endpoint."""

    @pytest_asyncio.fixture
    async def integration_with_oauth(self, e2e_client, platform_admin, db_session):
        """Create an integration with an authorization_code OAuth provider."""
        from uuid import uuid4

        integration_name = f"e2e_disconnect_oauth_{uuid4().hex[:8]}"

        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201, f"Create integration failed: {response.text}"
        integration = response.json()

        integration_id = UUID(integration["id"])
        oauth_provider = OAuthProvider(
            provider_name=f"test_provider_{uuid4().hex[:6]}",
            display_name="Test OAuth Provider",
            oauth_flow_type="authorization_code",
            client_id="test-client-id",
            encrypted_client_secret=b"encrypted_secret",
            authorization_url="https://login.example.com/authorize",
            token_url="https://login.example.com/token",
            scopes=["read", "write"],
            redirect_uri="/api/oauth/callback/test_provider",
            integration_id=integration_id,
        )
        db_session.add(oauth_provider)
        await db_session.commit()
        await db_session.refresh(oauth_provider)

        yield {"integration": integration, "oauth_provider": oauth_provider}

        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    @pytest.mark.asyncio
    async def test_disconnect_mapping_clears_token_link_and_deletes_token(
        self, e2e_client, platform_admin, db_session, org1, integration_with_oauth
    ):
        """Disconnect clears oauth_token_id and deletes the OAuthToken row."""
        integration = integration_with_oauth["integration"]

        # Create a mapping
        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "disconnect-entity-123",
                "entity_name": "Disconnect Test Entity",
            },
        )
        assert mapping_resp.status_code == 201, f"Create mapping failed: {mapping_resp.text}"
        mapping = mapping_resp.json()
        mapping_id = mapping["id"]

        try:
            # Create an OAuthToken and link it to the mapping directly via db_session
            oauth_provider = integration_with_oauth["oauth_provider"]
            token = OAuthToken(
                provider_id=oauth_provider.id,
                encrypted_access_token=b"encrypted_access_token",
                scopes=["read", "write"],
            )
            db_session.add(token)
            await db_session.commit()
            await db_session.refresh(token)
            token_id = token.id

            # Link the token to the mapping via direct DB update
            from sqlalchemy import update
            from src.models.orm import IntegrationMapping

            await db_session.execute(
                update(IntegrationMapping)
                .where(IntegrationMapping.id == UUID(mapping_id))
                .values(oauth_token_id=token_id)
            )
            await db_session.commit()

            # Verify link is in place via GET
            get_resp = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
            assert get_resp.status_code == 200, f"GET mapping failed: {get_resp.text}"
            assert get_resp.json()["oauth_token_id"] == str(token_id)

            # POST disconnect
            disconnect_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}/oauth/disconnect",
                headers=platform_admin.headers,
            )
            assert disconnect_resp.status_code == 204, f"Disconnect failed: {disconnect_resp.text}"
            assert disconnect_resp.content == b""

            # GET mapping — oauth_token_id must be None
            get_after = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
            assert get_after.status_code == 200, f"GET after disconnect failed: {get_after.text}"
            assert get_after.json()["oauth_token_id"] is None

            # Verify the OAuthToken row is deleted
            db_session.expire_all()
            deleted_token = await db_session.get(OAuthToken, token_id)
            assert deleted_token is None, "OAuthToken row should have been deleted"

        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_mapping_list_includes_connection_status(
        self, e2e_client, platform_admin, db_session, org1, integration_with_oauth
    ):
        """Mapping list response includes connection_status from the per-mapping OAuth token."""
        integration = integration_with_oauth["integration"]

        # Create a mapping
        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "status-entity-456",
                "entity_name": "Status Test Entity",
            },
        )
        assert mapping_resp.status_code == 201, f"Create mapping failed: {mapping_resp.text}"
        mapping = mapping_resp.json()
        mapping_id = mapping["id"]

        try:
            # Create an OAuthToken with status="completed" and link it to the mapping
            oauth_provider = integration_with_oauth["oauth_provider"]
            token = OAuthToken(
                provider_id=oauth_provider.id,
                encrypted_access_token=b"encrypted_access_token",
                scopes=["read", "write"],
                status="completed",
                status_message="Connected successfully",
            )
            db_session.add(token)
            await db_session.commit()
            await db_session.refresh(token)
            token_id = token.id

            # Link the token to the mapping via direct DB update
            from sqlalchemy import update
            from src.models.orm import IntegrationMapping

            await db_session.execute(
                update(IntegrationMapping)
                .where(IntegrationMapping.id == UUID(mapping_id))
                .values(oauth_token_id=token_id)
            )
            await db_session.commit()

            # GET /api/integrations/{integration_id}/mappings
            list_resp = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
            )
            assert list_resp.status_code == 200, f"List mappings failed: {list_resp.text}"
            items = list_resp.json()["items"]

            # Find our mapping in the list
            our_mapping = next((m for m in items if m["id"] == mapping_id), None)
            assert our_mapping is not None, f"Mapping {mapping_id} not found in list"
            assert our_mapping["connection_status"] == "completed"
            assert our_mapping["connection_message"] == "Connected successfully"

        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
