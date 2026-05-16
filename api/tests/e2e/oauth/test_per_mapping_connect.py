"""E2E: per-mapping OAuth authorize endpoint returns a URL with our state token."""

from urllib.parse import urlparse, parse_qs
from uuid import UUID

import pytest
import pytest_asyncio

from src.models.orm import OAuthProvider


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
