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


@pytest.mark.e2e
class TestPerMappingRefresh:
    """POST /mappings/{id}/oauth/refresh proactively refreshes a per-row token."""

    @pytest.mark.asyncio
    async def test_refresh_404_when_no_mapping(self, e2e_client, platform_admin):
        from uuid import uuid4

        resp = e2e_client.post(
            f"/api/integrations/{uuid4()}/mappings/{uuid4()}/oauth/refresh",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_refresh_400_when_mapping_has_no_token(
        self, e2e_client, platform_admin, org1
    ):
        from uuid import uuid4

        # Create a bare integration + mapping with no oauth_token_id
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": f"e2e_refresh_no_token_{uuid4().hex[:8]}"},
        )
        assert integ_resp.status_code == 201
        integration = integ_resp.json()

        try:
            mapping_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
                json={"organization_id": str(org1["id"]), "entity_id": "x"},
            )
            assert mapping_resp.status_code == 201
            mapping_id = mapping_resp.json()["id"]

            refresh_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}/oauth/refresh",
                headers=platform_admin.headers,
            )
            assert refresh_resp.status_code == 400
            assert "no per-row oauth connection" in refresh_resp.json()["detail"].lower()
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
class TestEmptyEntityId:
    """Mappings can be created and updated with an empty entity_id.

    This supports the per-mapping OAuth Connect flow: a user can create a
    mapping ahead of time (or implicitly by clicking Connect) and the OAuth
    callback fills entity_id from the provider's entity_id_source config.
    """

    @pytest.mark.asyncio
    async def test_create_mapping_with_empty_entity_id(
        self, e2e_client, platform_admin, org1
    ):
        """POST /mappings accepts entity_id="" (was previously blocked by min_length=1)."""
        from uuid import uuid4

        integration_name = f"e2e_empty_entity_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert integ_resp.status_code == 201
        integration = integ_resp.json()

        try:
            mapping_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
                json={
                    "organization_id": str(org1["id"]),
                    "entity_id": "",
                    "entity_name": "",
                },
            )
            assert mapping_resp.status_code == 201, mapping_resp.text
            mapping = mapping_resp.json()
            assert mapping["entity_id"] == ""
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_update_mapping_clears_entity_id(
        self, e2e_client, platform_admin, org1
    ):
        """PUT /mappings/{id} accepts entity_id="" so a user can clear the field."""
        from uuid import uuid4

        integration_name = f"e2e_clear_entity_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert integ_resp.status_code == 201
        integration = integ_resp.json()

        try:
            mapping_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
                json={
                    "organization_id": str(org1["id"]),
                    "entity_id": "initial-value",
                    "entity_name": "Initial",
                },
            )
            assert mapping_resp.status_code == 201
            mapping_id = mapping_resp.json()["id"]

            put_resp = e2e_client.put(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
                json={"entity_id": ""},
            )
            assert put_resp.status_code == 200, put_resp.text
            assert put_resp.json()["entity_id"] == ""
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_batch_upsert_accepts_empty_entity_id(
        self, e2e_client, platform_admin, org1
    ):
        """POST /mappings/batch accepts entity_id="" entries."""
        from uuid import uuid4

        integration_name = f"e2e_batch_empty_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert integ_resp.status_code == 201
        integration = integ_resp.json()

        try:
            batch_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings/batch",
                headers=platform_admin.headers,
                json={
                    "mappings": [
                        {"organization_id": str(org1["id"]), "entity_id": ""}
                    ]
                },
            )
            assert batch_resp.status_code == 200, batch_resp.text
            assert batch_resp.json()["created"] == 1
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
@pytest.mark.skip(
    reason="Requires cross-process mock of upstream token endpoint; covered by manual smoke + the unit-level callback test."
)
class TestPerMappingCallbackTokenScope:
    """Regression: per-mapping callback must store the token with the mapping's
    organization_id, not stomp the integration-level (org_id=NULL) token row.
    """

    @pytest.mark.asyncio
    async def test_callback_scopes_token_to_mapping_org(
        self, e2e_client, platform_admin, db_session, org1
    ):
        """End-to-end: authorize → mock token exchange → assert token.organization_id == mapping.org_id."""
        from unittest.mock import patch
        from uuid import uuid4
        from sqlalchemy import select

        # 1. Create an integration + OAuth provider
        integration_name = f"e2e_token_scope_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert integ_resp.status_code == 201
        integration = integ_resp.json()
        integration_id = UUID(integration["id"])

        provider = OAuthProvider(
            provider_name=f"prov_{uuid4().hex[:6]}",
            display_name="Test Provider",
            oauth_flow_type="authorization_code",
            client_id="test-client-id",
            encrypted_client_secret=b"encrypted_secret",
            authorization_url="https://login.example.com/authorize",
            token_url="https://login.example.com/token",
            scopes=["read"],
            redirect_uri="/api/oauth/callback/test",
            integration_id=integration_id,
        )
        db_session.add(provider)
        await db_session.commit()
        await db_session.refresh(provider)
        provider_id = provider.id  # capture before any session expiry

        try:
            # 2. Seed a pre-existing integration-level (org_id=NULL) token so we
            #    can prove the per-mapping callback doesn't overwrite it.
            existing_global = OAuthToken(
                provider_id=provider_id,
                organization_id=None,
                encrypted_access_token=b"GLOBAL-ORIGINAL",
                expires_at=None,
                scopes=[],
            )
            db_session.add(existing_global)
            await db_session.commit()
            await db_session.refresh(existing_global)
            global_token_id = existing_global.id

            # 3. Create a mapping for org1
            mapping_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
                json={"organization_id": str(org1["id"]), "entity_id": ""},
            )
            assert mapping_resp.status_code == 201, mapping_resp.text
            mapping_id = mapping_resp.json()["id"]

            # 4. Get a signed state token via the per-mapping authorize endpoint
            authz_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}/oauth/authorize",
                headers=platform_admin.headers,
                json={"redirect_uri": f"http://localhost:3000/oauth/callback/{integration['id']}"},
            )
            assert authz_resp.status_code == 200, authz_resp.text
            from urllib.parse import urlparse, parse_qs
            state_token = parse_qs(urlparse(authz_resp.json()["authorization_url"]).query)["state"][0]

            # 5. Mock the upstream token exchange and POST to the callback
            async def fake_make_token_request(*args, **kwargs):
                return True, {
                    "access_token": "ORG-SCOPED-ACCESS",
                    "refresh_token": "ORG-SCOPED-REFRESH",
                    "expires_in": 3600,
                    "scope": "read",
                }

            with patch(
                "src.services.oauth_provider.OAuthProviderClient._make_token_request",
                side_effect=fake_make_token_request,
            ):
                cb_resp = e2e_client.post(
                    f"/api/oauth/callback/{integration['id']}",
                    headers=platform_admin.headers,
                    json={
                        "code": "fake-auth-code",
                        "state": state_token,
                        "redirect_uri": f"http://localhost:3000/oauth/callback/{integration['id']}",
                    },
                )
            assert cb_resp.status_code == 200, cb_resp.text

            # 6. Assert the global token is UNCHANGED
            db_session.expire_all()
            still_global = await db_session.get(OAuthToken, global_token_id)
            assert still_global is not None, "Existing global token was deleted!"
            assert still_global.organization_id is None
            assert still_global.encrypted_access_token == b"GLOBAL-ORIGINAL", (
                "Per-mapping callback overwrote the integration-level token "
                "(the original bug — every per-mapping connect stomped the global one)"
            )

            # Diagnostic — list every token for this provider
            all_tokens = await db_session.execute(
                select(OAuthToken).where(OAuthToken.provider_id == provider_id)
            )
            all_list = list(all_tokens.scalars().all())
            diagnostic = [
                (str(t.id), str(t.organization_id), t.encrypted_access_token[:20])
                for t in all_list
            ]

            # 7. Assert a NEW token exists scoped to org1
            org_tokens = await db_session.execute(
                select(OAuthToken).where(
                    OAuthToken.provider_id == provider_id,
                    OAuthToken.organization_id == org1["id"],
                )
            )
            org_token = org_tokens.scalar_one_or_none()
            assert org_token is not None, f"No org-scoped token was created. All tokens for provider: {diagnostic}"
            assert org_token.id != global_token_id

            # 8. Assert the mapping is linked to the NEW org-scoped token
            mapping_get = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
            assert mapping_get.status_code == 200
            assert mapping_get.json()["oauth_token_id"] == str(org_token.id)
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
class TestEntityIdSourceConfig:
    """PATCH /oauth/entity_id_source persists the picker selection."""

    @pytest.mark.asyncio
    async def test_patch_sets_entity_id_source(
        self, e2e_client, platform_admin, db_session
    ):
        from uuid import uuid4
        from src.models.orm import OAuthProvider as _OP

        integration_name = f"e2e_eid_source_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert integ_resp.status_code == 201
        integration = integ_resp.json()
        integration_id = UUID(integration["id"])

        provider = _OP(
            provider_name=f"prov_{uuid4().hex[:6]}",
            oauth_flow_type="authorization_code",
            client_id="x",
            encrypted_client_secret=b"x",
            token_url="https://example.com/token",
            integration_id=integration_id,
        )
        db_session.add(provider)
        await db_session.commit()
        await db_session.refresh(provider)
        provider_id = provider.id

        try:
            resp = e2e_client.patch(
                f"/api/integrations/{integration['id']}/oauth/entity_id_source",
                headers=platform_admin.headers,
                json={"type": "id_token_claim", "key": "tid"},
            )
            assert resp.status_code == 200, resp.text

            db_session.expire_all()
            refetched = await db_session.get(_OP, provider_id)
            assert refetched.entity_id_source == {"type": "id_token_claim", "key": "tid"}
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_patch_with_apply_to_mapping_backfills_entity_id(
        self, e2e_client, platform_admin, db_session, org1
    ):
        from uuid import uuid4
        from src.models.orm import OAuthProvider as _OP

        integration_name = f"e2e_eid_apply_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        integration = integ_resp.json()
        integration_id = UUID(integration["id"])

        provider = _OP(
            provider_name=f"prov_{uuid4().hex[:6]}",
            oauth_flow_type="authorization_code",
            client_id="x",
            encrypted_client_secret=b"x",
            token_url="https://example.com/token",
            integration_id=integration_id,
        )
        db_session.add(provider)
        await db_session.commit()

        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={"organization_id": str(org1["id"]), "entity_id": ""},
        )
        assert mapping_resp.status_code == 201
        mapping_id = mapping_resp.json()["id"]

        try:
            resp = e2e_client.patch(
                f"/api/integrations/{integration['id']}/oauth/entity_id_source",
                headers=platform_admin.headers,
                json={
                    "type": "id_token_claim",
                    "key": "tid",
                    "apply_to_mapping_id": mapping_id,
                    "apply_value": "tenant-uuid-from-picker",
                },
            )
            assert resp.status_code == 200, resp.text

            get_resp = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
            assert get_resp.json()["entity_id"] == "tenant-uuid-from-picker"
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
class TestClearEntityIdSource:
    """DELETE /oauth/entity_id_source nulls the source and optionally clears mappings."""

    @pytest.mark.asyncio
    async def test_delete_clears_source_only_by_default(
        self, e2e_client, platform_admin, db_session, org1
    ):
        from uuid import uuid4
        from src.models.orm import OAuthProvider as _OP

        integration_name = f"e2e_clear_eid_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert integ_resp.status_code == 201
        integration = integ_resp.json()
        integration_id = UUID(integration["id"])

        provider = _OP(
            provider_name=f"prov_{uuid4().hex[:6]}",
            oauth_flow_type="authorization_code",
            client_id="x",
            encrypted_client_secret=b"x",
            token_url="https://example.com/token",
            integration_id=integration_id,
            entity_id_source={"type": "id_token_claim", "key": "tid"},
        )
        db_session.add(provider)
        await db_session.commit()
        await db_session.refresh(provider)
        provider_id = provider.id

        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={"organization_id": str(org1["id"]), "entity_id": "tenant-abc"},
        )
        assert mapping_resp.status_code == 201
        mapping_id = mapping_resp.json()["id"]

        try:
            resp = e2e_client.delete(
                f"/api/integrations/{integration['id']}/oauth/entity_id_source",
                headers=platform_admin.headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["entity_id_source"] is None
            assert resp.json()["cleared_mapping_count"] == 0

            db_session.expire_all()
            refetched = await db_session.get(_OP, provider_id)
            assert refetched.entity_id_source is None

            # Mapping value untouched by default
            get_resp = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
            assert get_resp.json()["entity_id"] == "tenant-abc"
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_delete_with_clear_mappings_blanks_entity_ids(
        self, e2e_client, platform_admin, db_session, org1
    ):
        from uuid import uuid4
        from src.models.orm import OAuthProvider as _OP

        integration_name = f"e2e_clear_eid_full_{uuid4().hex[:8]}"
        integ_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        integration = integ_resp.json()
        integration_id = UUID(integration["id"])

        provider = _OP(
            provider_name=f"prov_{uuid4().hex[:6]}",
            oauth_flow_type="authorization_code",
            client_id="x",
            encrypted_client_secret=b"x",
            token_url="https://example.com/token",
            integration_id=integration_id,
            entity_id_source={"type": "id_token_claim", "key": "tid"},
        )
        db_session.add(provider)
        await db_session.commit()

        mapping_resp = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={"organization_id": str(org1["id"]), "entity_id": "tenant-xyz"},
        )
        mapping_id = mapping_resp.json()["id"]

        try:
            resp = e2e_client.delete(
                f"/api/integrations/{integration['id']}/oauth/entity_id_source"
                "?clear_mappings=true",
                headers=platform_admin.headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["entity_id_source"] is None
            assert resp.json()["cleared_mapping_count"] == 1

            get_resp = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings/{mapping_id}",
                headers=platform_admin.headers,
            )
            assert get_resp.json()["entity_id"] == ""
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )
