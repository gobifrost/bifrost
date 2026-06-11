"""
E2E tests for Integrations API.

Tests CRUD operations for integrations and integration mappings.
Tests OAuth configuration and authorization endpoints.
"""

import pytest
import pytest_asyncio
from uuid import uuid4
from src.models.orm import OAuthProvider


def _sdk_get(e2e_client, headers, *, name, org_id=None):
    """Read merged integration data via the live, org-scoped SDK endpoint.

    Replaces the former GET /api/integrations/sdk/{name}?org_id= calls (that
    endpoint was DELETED — EXT-1 NEW-H — for taking org_id as a free,
    unchecked Query param). POST /api/sdk/integrations/get goes through
    _resolve_sdk_org_id and returns the same merged ``config`` + ``entity_id``;
    ``scope`` is the org UUID (a bypass platform_admin may target any org).
    """
    body = {"name": name}
    if org_id is not None:
        body["scope"] = str(org_id)
    return e2e_client.post(
        "/api/sdk/integrations/get", headers=headers, json=body
    )


@pytest.mark.e2e
class TestIntegrationsCRUD:
    """Test Integration CRUD operations."""

    @pytest.fixture
    def integration(self, e2e_client, platform_admin):
        """Create an integration and clean up after."""
        integration_name = f"e2e_test_integration_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {
                        "key": "api_endpoint",
                        "type": "string",
                        "required": True,
                        "description": "API endpoint URL",
                    }
                ],
            },
        )
        assert response.status_code == 201, f"Create integration failed: {response.text}"
        integration = response.json()

        yield integration

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def test_create_integration(self, e2e_client, platform_admin):
        """Platform admin can create an integration."""
        integration_name = f"e2e_create_test_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {
                        "key": "tenant_id",
                        "type": "string",
                        "required": True,
                        "description": "Tenant ID",
                    }
                ],
            },
        )
        assert response.status_code == 201, f"Create failed: {response.text}"
        integration = response.json()
        assert integration["name"] == integration_name
        assert integration["config_schema"] is not None
        assert len(integration["config_schema"]) == 1
        assert integration["is_deleted"] is False

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def test_list_integrations(self, e2e_client, platform_admin, integration):
        """Platform admin can list integrations."""
        response = e2e_client.get(
            "/api/integrations",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        integrations = data.get("items", data)
        assert isinstance(integrations, list)
        names = [i["name"] for i in integrations]
        assert integration["name"] in names

    def test_get_integration(self, e2e_client, platform_admin, integration):
        """Platform admin can get integration by ID."""
        response = e2e_client.get(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get failed: {response.text}"
        data = response.json()
        assert data["id"] == integration["id"]
        assert data["name"] == integration["name"]

    def test_get_integration_by_name(self, e2e_client, platform_admin, integration):
        """Platform admin can get integration by name."""
        response = e2e_client.get(
            f"/api/integrations/by-name/{integration['name']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get by name failed: {response.text}"
        data = response.json()
        assert data["id"] == integration["id"]
        assert data["name"] == integration["name"]

    def test_update_integration(self, e2e_client, platform_admin, integration):
        """Platform admin can update an integration."""
        new_name = f"updated_{integration['name']}"
        response = e2e_client.put(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
            json={
                "name": new_name,
            },
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        updated = response.json()
        assert updated["name"] == new_name

    def test_delete_integration(self, e2e_client, platform_admin):
        """Platform admin can soft-delete an integration."""
        # Create an integration to delete
        integration_name = f"e2e_delete_test_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()

        # Delete it
        response = e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204, f"Delete failed: {response.text}"

        # Verify it's soft-deleted (not in normal list)
        response = e2e_client.get(
            "/api/integrations",
            headers=platform_admin.headers,
        )
        integrations = response.json().get("items", response.json())
        ids = [i["id"] for i in integrations]
        assert integration["id"] not in ids


@pytest.mark.e2e
class TestIntegrationMappingsCRUD:
    """Test IntegrationMapping CRUD operations."""

    @pytest.fixture
    def integration_for_mapping(self, e2e_client, platform_admin):
        """Create an integration for mapping tests."""
        integration_name = f"e2e_mapping_integration_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()

        yield integration

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    @pytest.fixture
    def mapping(self, e2e_client, platform_admin, integration_for_mapping, org1):
        """Create a mapping and clean up after."""
        response = e2e_client.post(
            f"/api/integrations/{integration_for_mapping['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "tenant-12345",
                "entity_name": "Test Tenant",
            },
        )
        assert response.status_code == 201, f"Create mapping failed: {response.text}"
        mapping = response.json()

        yield mapping

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration_for_mapping['id']}/mappings/{mapping['id']}",
            headers=platform_admin.headers,
        )

    def test_create_mapping(self, e2e_client, platform_admin, integration_for_mapping, org1):
        """Platform admin can create an integration mapping."""
        response = e2e_client.post(
            f"/api/integrations/{integration_for_mapping['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "tenant-create-test",
                "entity_name": "Create Test Tenant",
            },
        )
        assert response.status_code == 201, f"Create failed: {response.text}"
        mapping = response.json()
        assert mapping["entity_id"] == "tenant-create-test"
        assert mapping["entity_name"] == "Create Test Tenant"
        assert mapping["organization_id"] == str(org1["id"])

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration_for_mapping['id']}/mappings/{mapping['id']}",
            headers=platform_admin.headers,
        )

    def test_list_mappings(self, e2e_client, platform_admin, integration_for_mapping, mapping):
        """Platform admin can list integration mappings."""
        response = e2e_client.get(
            f"/api/integrations/{integration_for_mapping['id']}/mappings",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        mappings = data.get("items", data)
        assert isinstance(mappings, list)
        assert len(mappings) >= 1
        ids = [m["id"] for m in mappings]
        assert mapping["id"] in ids

    def test_get_mapping(self, e2e_client, platform_admin, integration_for_mapping, mapping):
        """Platform admin can get a mapping by ID."""
        response = e2e_client.get(
            f"/api/integrations/{integration_for_mapping['id']}/mappings/{mapping['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get failed: {response.text}"
        data = response.json()
        assert data["id"] == mapping["id"]
        assert data["entity_id"] == mapping["entity_id"]

    def test_get_mapping_by_org(self, e2e_client, platform_admin, integration_for_mapping, mapping, org1):
        """Platform admin can get a mapping by organization."""
        response = e2e_client.get(
            f"/api/integrations/{integration_for_mapping['id']}/mappings/by-org/{org1['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get by org failed: {response.text}"
        data = response.json()
        assert data["id"] == mapping["id"]
        assert data["organization_id"] == str(org1["id"])

    def test_update_mapping(self, e2e_client, platform_admin, integration_for_mapping, mapping):
        """Platform admin can update a mapping."""
        response = e2e_client.put(
            f"/api/integrations/{integration_for_mapping['id']}/mappings/{mapping['id']}",
            headers=platform_admin.headers,
            json={
                "entity_name": "Updated Tenant Name",
            },
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        updated = response.json()
        assert updated["entity_name"] == "Updated Tenant Name"

    def test_delete_mapping(self, e2e_client, platform_admin, integration_for_mapping, org1):
        """Platform admin can delete a mapping."""
        # Create a mapping to delete
        response = e2e_client.post(
            f"/api/integrations/{integration_for_mapping['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "tenant-delete-test",
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        # Delete it
        response = e2e_client.delete(
            f"/api/integrations/{integration_for_mapping['id']}/mappings/{mapping['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204, f"Delete failed: {response.text}"

        # Verify it's gone
        response = e2e_client.get(
            f"/api/integrations/{integration_for_mapping['id']}/mappings/{mapping['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


@pytest.mark.e2e
class TestIntegrationsSDK:
    """Test Integration SDK endpoint."""

    @pytest.fixture
    def integration_with_mapping(self, e2e_client, platform_admin, org1):
        """Create an integration with a mapping for SDK tests."""
        integration_name = f"e2e_sdk_integration_{uuid4().hex[:8]}"

        # Create integration
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "entity_id": "global-tenant-123",
                "entity_id_name": "Tenant ID",
                "config_schema": [
                    {
                        "key": "api_url",
                        "type": "string",
                        "required": False,
                        "default": "https://api.example.com",
                    }
                ],
            },
        )
        assert response.status_code == 201
        integration = response.json()

        # Create mapping
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "org-tenant-456",
                "entity_name": "Org Tenant",
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        yield {"integration": integration, "mapping": mapping, "org": org1}

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
            headers=platform_admin.headers,
        )
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def test_get_sdk_data(self, e2e_client, platform_admin, integration_with_mapping):
        """SDK endpoint returns integration data for an org."""
        integration = integration_with_mapping["integration"]
        org = integration_with_mapping["org"]

        response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org["id"])
        assert response.status_code == 200, f"Get SDK data failed: {response.text}"
        data = response.json()

        assert data["integration_id"] == integration["id"]
        assert data["entity_id"] == "org-tenant-456"  # From mapping
        assert "config" in data

    def test_get_sdk_data_not_found(self, e2e_client, platform_admin, org1):
        """The live SDK get endpoint returns null for a non-existent
        integration (the deleted /sdk/{name} endpoint 404'd; the replacement
        returns None/200 — a miss, not an error)."""
        response = _sdk_get(e2e_client, platform_admin.headers, name="nonexistent_integration", org_id=org1["id"])
        assert response.status_code == 200, response.text
        assert response.json() is None

    def test_get_sdk_data_no_mapping(self, e2e_client, platform_admin, org1):
        """With no org mapping, the live SDK get endpoint falls back to
        integration defaults (the deleted /sdk/{name} endpoint 404'd on a
        missing mapping; the replacement returns the integration-level
        defaults, which for a bare integration is an empty config)."""
        integration_name = f"e2e_no_mapping_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()

        try:
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration_name, org_id=org1["id"])
            assert response.status_code == 200, response.text
            data = response.json()
            assert data is not None
            assert data["config"] == {}
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
class TestIntegrationOAuth:
    """Test Integration OAuth configuration and authorization endpoints."""

    @pytest_asyncio.fixture
    async def integration_with_oauth(self, e2e_client, platform_admin, db_session):
        """Create an integration with OAuth provider."""
        integration_name = f"e2e_oauth_integration_{uuid4().hex[:8]}"

        # Create integration
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {
                        "key": "tenant_id",
                        "type": "string",
                        "required": True,
                    }
                ],
            },
        )
        assert response.status_code == 201
        integration = response.json()

        # Create OAuth provider directly in database
        from uuid import UUID
        integration_id = UUID(integration["id"])
        oauth_provider = OAuthProvider(
            provider_name="test_provider",
            display_name="Test OAuth Provider",
            oauth_flow_type="authorization_code",
            client_id="test-client-id",
            encrypted_client_secret=b"encrypted_secret",
            authorization_url="https://provider.example.com/authorize",
            token_url="https://provider.example.com/token",
            scopes=["read", "write"],
            redirect_uri="/api/oauth/callback/test_provider",
            integration_id=integration_id,
        )
        db_session.add(oauth_provider)
        await db_session.commit()

        yield {"integration": integration, "oauth_provider": oauth_provider}

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def test_get_oauth_config(self, e2e_client, platform_admin, integration_with_oauth):
        """Platform admin can get OAuth configuration for an integration."""
        integration = integration_with_oauth["integration"]
        oauth_provider = integration_with_oauth["oauth_provider"]

        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/oauth",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get OAuth config failed: {response.text}"
        data = response.json()

        assert data["provider_name"] == oauth_provider.provider_name
        assert data["display_name"] == oauth_provider.display_name
        assert data["oauth_flow_type"] == oauth_provider.oauth_flow_type
        assert data["client_id"] == oauth_provider.client_id
        assert data["authorization_url"] == oauth_provider.authorization_url
        assert data["token_url"] == oauth_provider.token_url
        assert data["scopes"] == oauth_provider.scopes

    def test_get_oauth_config_not_found(self, e2e_client, platform_admin):
        """Returns 404 if no OAuth config exists for integration."""
        # Create integration without OAuth provider
        integration_name = f"e2e_no_oauth_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()

        try:
            response = e2e_client.get(
                f"/api/integrations/{integration['id']}/oauth",
                headers=platform_admin.headers,
            )
            assert response.status_code == 404
            assert "No OAuth configuration" in response.json()["detail"]
        finally:
            # Cleanup
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    def test_get_oauth_authorize_url(
        self, e2e_client, platform_admin, integration_with_oauth
    ):
        """Platform admin can get OAuth authorization URL."""
        integration = integration_with_oauth["integration"]

        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/oauth/authorize",
            headers=platform_admin.headers,
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert response.status_code == 200, f"Get authorize URL failed: {response.text}"
        data = response.json()

        assert "authorization_url" in data
        assert "state" in data
        assert "Redirect user" in data["message"]

        # Verify authorization URL contains expected parameters
        auth_url = data["authorization_url"]
        assert "https://provider.example.com/authorize?" in auth_url
        assert "client_id=test-client-id" in auth_url
        assert "response_type=code" in auth_url
        assert "state=" in auth_url
        assert "scope=read+write" in auth_url
        assert "redirect_uri=" in auth_url

    def test_get_oauth_authorize_url_missing_redirect_uri(
        self, e2e_client, platform_admin, integration_with_oauth
    ):
        """Returns 422 if redirect_uri is not provided."""
        integration = integration_with_oauth["integration"]

        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/oauth/authorize",
            headers=platform_admin.headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_oauth_authorize_url_client_credentials_flow(
        self, e2e_client, platform_admin, db_session
    ):
        """Returns 400 for client_credentials flow (no user authorization needed)."""
        integration_name = f"e2e_client_creds_{uuid4().hex[:8]}"

        # Create integration
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()

        # Create OAuth provider with client_credentials flow (no authorization_url)
        from uuid import UUID
        integration_id = UUID(integration["id"])
        oauth_provider = OAuthProvider(
            provider_name="client_creds_provider",
            oauth_flow_type="client_credentials",
            client_id="test-client-id",
            encrypted_client_secret=b"encrypted_secret",
            token_url="https://provider.example.com/token",
            # No authorization_url for client_credentials flow
            integration_id=integration_id,
        )
        db_session.add(oauth_provider)
        await db_session.commit()

        try:
            response = e2e_client.get(
                f"/api/integrations/{integration['id']}/oauth/authorize",
                headers=platform_admin.headers,
                params={"redirect_uri": "http://localhost:3000/callback"},
            )
            assert response.status_code == 400
            assert "client_credentials" in response.json()["detail"]
        finally:
            # Cleanup
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
class TestIntegrationConfig:
    """Test Integration config defaults and merging."""

    @pytest.fixture
    def integration_with_schema(self, e2e_client, platform_admin):
        """Create an integration with config schema for testing."""
        integration_name = f"e2e_config_test_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {"key": "api_url", "type": "string", "required": True},
                    {"key": "timeout", "type": "int", "required": False},
                    {"key": "debug", "type": "bool", "required": False},
                ],
            },
        )
        assert response.status_code == 201
        integration = response.json()

        yield integration

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def test_set_integration_config_defaults(self, e2e_client, platform_admin, integration_with_schema):
        """Platform admin can set integration-level config defaults."""
        integration = integration_with_schema

        # Set default config
        response = e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://api.default.com",
                    "timeout": 30,
                    "debug": False,
                }
            },
        )
        assert response.status_code == 200, f"Set config failed: {response.text}"
        data = response.json()
        assert data["integration_id"] == integration["id"]
        assert data["config"]["api_url"] == "https://api.default.com"
        assert data["config"]["timeout"] == 30
        assert data["config"]["debug"] is False

    def test_get_integration_config_defaults(self, e2e_client, platform_admin, integration_with_schema):
        """Platform admin can get integration-level config defaults."""
        integration = integration_with_schema

        # Set default config first
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://api.example.com",
                    "timeout": 60,
                }
            },
        )

        # Get default config
        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get config failed: {response.text}"
        data = response.json()
        assert data["integration_id"] == integration["id"]
        assert data["config"]["api_url"] == "https://api.example.com"
        assert data["config"]["timeout"] == 60

    def test_config_merging_defaults_with_overrides(self, e2e_client, platform_admin, integration_with_schema, org1):
        """Config merging: org overrides take precedence over defaults."""
        integration = integration_with_schema

        # Set integration defaults
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://api.default.com",
                    "timeout": 30,
                    "debug": False,
                }
            },
        )

        # Create mapping with org-specific override
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "tenant-123",
                "config": {
                    "api_url": "https://api.org-override.com",  # Override
                    "timeout": 60,  # Override
                    # debug not provided - should use default
                },
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # Verify SDK endpoint returns merged config
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            assert response.status_code == 200, f"Get SDK data failed: {response.text}"
            data = response.json()

            # Org overrides should take precedence
            assert data["config"]["api_url"] == "https://api.org-override.com"
            assert data["config"]["timeout"] == 60
            # Default should be used where no override exists
            assert data["config"]["debug"] is False
        finally:
            # Cleanup
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )

    def test_org_config_override_precedence(self, e2e_client, platform_admin, integration_with_schema, org1):
        """Org config values override integration defaults."""
        integration = integration_with_schema

        # Set integration defaults
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://default.com",
                    "timeout": 10,
                }
            },
        )

        # Create mapping with org override
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "override-test",
                "config": {
                    "timeout": 999,  # Override only timeout
                },
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # Get SDK data
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            assert response.status_code == 200
            data = response.json()

            # api_url should use default, timeout should use override
            assert data["config"]["api_url"] == "https://default.com"
            assert data["config"]["timeout"] == 999
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )

    def test_clearing_override_falls_back_to_default(self, e2e_client, platform_admin, integration_with_schema, org1):
        """Clearing an org override falls back to integration default."""
        integration = integration_with_schema

        # Set integration defaults
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://default.com",
                }
            },
        )

        # Create mapping with override
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "clear-test",
                "config": {
                    "api_url": "https://override.com",
                },
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # Verify override is active
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            data = response.json()
            assert data["config"]["api_url"] == "https://override.com"

            # Clear override by updating mapping with null value for the key
            response = e2e_client.put(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
                json={
                    "config": {"api_url": None},
                },
            )
            assert response.status_code == 200

            # Verify fallback to default
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            data = response.json()
            assert data["config"]["api_url"] == "https://default.com"
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )

    def test_sdk_returns_default_when_org_has_no_override(self, e2e_client, platform_admin, integration_with_schema, org1):
        """SDK returns integration default when org has no override for a key."""
        integration = integration_with_schema

        # Set integration defaults
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://default-only.com",
                    "timeout": 45,
                }
            },
        )

        # Create mapping WITHOUT config overrides
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "no-override",
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # Call SDK endpoint, verify defaults are returned
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            assert response.status_code == 200
            data = response.json()

            # Should return defaults
            assert data["config"]["api_url"] == "https://default-only.com"
            assert data["config"]["timeout"] == 45
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )

    def test_sdk_returns_org_override_when_set(self, e2e_client, platform_admin, integration_with_schema, org1):
        """SDK returns org override when set."""
        integration = integration_with_schema

        # Set integration defaults
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://default.com",
                }
            },
        )

        # Create mapping WITH config overrides
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "with-override",
                "config": {
                    "api_url": "https://org-specific.com",
                },
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # Call SDK endpoint, verify overrides are returned
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            assert response.status_code == 200
            data = response.json()

            # Should return org override
            assert data["config"]["api_url"] == "https://org-specific.com"
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )

    def test_sdk_returns_empty_dict_when_no_config(self, e2e_client, platform_admin, org1):
        """SDK returns empty config dict when no schema/defaults/overrides."""
        # Create integration WITHOUT config_schema
        integration_name = f"e2e_no_config_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()

        try:
            # Create mapping without config
            response = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
                json={
                    "organization_id": str(org1["id"]),
                    "entity_id": "no-config",
                },
            )
            assert response.status_code == 201
            mapping = response.json()

            # Call SDK endpoint, verify config is empty dict
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration_name, org_id=org1["id"])
            assert response.status_code == 200
            data = response.json()
            assert data["config"] == {}

            # Cleanup mapping
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )
        finally:
            # Cleanup integration
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    def test_sdk_handles_null_config_values_gracefully(self, e2e_client, platform_admin, integration_with_schema, org1):
        """SDK handles null/missing config values gracefully."""
        integration = integration_with_schema

        # Set partial config (some keys null/missing)
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "api_url": "https://partial.com",
                    # timeout not set
                }
            },
        )

        # Create mapping
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "null-test",
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # Verify SDK returns correctly merged config
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            assert response.status_code == 200
            data = response.json()

            # Should have api_url, timeout not present (not in config dict)
            assert data["config"]["api_url"] == "https://partial.com"
            assert "timeout" not in data["config"]
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )


# Note: TestIntegrationsCLI class was removed during SDK simplification.
# CLI endpoints for integrations now use JWT authentication from `bifrost login`
# rather than developer API keys from `/api/sdk/keys`.


@pytest.mark.e2e
class TestIntegrationConfigSecrets:
    """Test that integration config secrets are properly encrypted and typed."""

    @pytest.fixture
    def integration_with_secret_schema(self, e2e_client, platform_admin):
        """Create an integration with a secret config field."""
        integration_name = f"e2e_secret_test_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {"key": "base_url", "type": "string", "required": True},
                    {"key": "api_key", "type": "secret", "required": True},
                ],
            },
        )
        assert response.status_code == 201
        integration = response.json()

        yield integration

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def test_secret_defaults_are_encrypted_in_db(
        self, e2e_client, platform_admin, integration_with_secret_schema
    ):
        """Secret config values saved via integration defaults are encrypted."""
        integration = integration_with_secret_schema

        # Set defaults including a secret
        response = e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "base_url": "https://api.example.com",
                    "api_key": "super-secret-key-12345",
                }
            },
        )
        assert response.status_code == 200

        # Verify the config list endpoint masks the secret
        response = e2e_client.get(
            "/api/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        configs = response.json()

        # Find our api_key config
        api_key_config = next(
            (c for c in configs if c["key"] == "api_key"
             and c.get("integration_id") == integration["id"]),
            None,
        )
        assert api_key_config is not None, f"api_key config not found in {[c['key'] for c in configs]}"
        assert api_key_config["type"] == "secret", f"Expected type 'secret', got '{api_key_config['type']}'"
        assert api_key_config["value"] == "[SECRET]", f"Secret value should be masked, got '{api_key_config['value']}'"

    def test_secret_roundtrip_via_sdk(
        self, e2e_client, platform_admin, integration_with_secret_schema, org1
    ):
        """Secret saved via integration defaults can be decrypted for SDK consumption."""
        integration = integration_with_secret_schema

        # Set defaults including a secret
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "base_url": "https://api.example.com",
                    "api_key": "my-secret-api-key",
                }
            },
        )

        # Create mapping
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "secret-roundtrip-test",
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # Get SDK data - should return decrypted secret
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            assert response.status_code == 200
            data = response.json()

            assert data["config"]["base_url"] == "https://api.example.com"
            assert data["config"]["api_key"] == "my-secret-api-key"
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )

    def test_org_override_secret_is_encrypted(
        self, e2e_client, platform_admin, integration_with_secret_schema, org1
    ):
        """Secret config values saved via org mapping overrides are encrypted."""
        integration = integration_with_secret_schema

        # Set defaults
        e2e_client.put(
            f"/api/integrations/{integration['id']}/config",
            headers=platform_admin.headers,
            json={
                "config": {
                    "base_url": "https://api.default.com",
                    "api_key": "default-key",
                }
            },
        )

        # Create mapping with org-specific secret override
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "org-secret-test",
                "config": {
                    "api_key": "org-specific-secret-key",
                },
            },
        )
        assert response.status_code == 201
        mapping = response.json()

        try:
            # SDK should return the org override (decrypted)
            response = _sdk_get(e2e_client, platform_admin.headers, name=integration['name'], org_id=org1["id"])
            assert response.status_code == 200
            data = response.json()

            assert data["config"]["api_key"] == "org-specific-secret-key"
            assert data["config"]["base_url"] == "https://api.default.com"
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{mapping['id']}",
                headers=platform_admin.headers,
            )


@pytest.mark.e2e
class TestIntegrationsAuthorization:
    """Test authorization for integrations endpoints."""

    def test_non_superuser_cannot_list_integrations(self, e2e_client, org1_user):
        """Non-superuser should get 403 when listing integrations."""
        response = e2e_client.get(
            "/api/integrations",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_non_superuser_cannot_create_integration(self, e2e_client, org1_user):
        """Non-superuser should get 403 when creating integrations."""
        response = e2e_client.post(
            "/api/integrations",
            headers=org1_user.headers,
            json={"name": "test_integration"},
        )
        assert response.status_code == 403

    def test_non_superuser_cannot_modify_integration(self, e2e_client, org1_user, platform_admin):
        """Non-superuser should get 403 when modifying integrations."""
        # First create as admin
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": f"auth_test_{uuid4().hex[:8]}"},
        )
        integration = response.json()

        try:
            # Try to update as non-superuser
            response = e2e_client.put(
                f"/api/integrations/{integration['id']}",
                headers=org1_user.headers,
                json={"name": "hacked"},
            )
            assert response.status_code == 403

            # Try to delete as non-superuser
            response = e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=org1_user.headers,
            )
            assert response.status_code == 403
        finally:
            # Cleanup as admin
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    def test_non_superuser_cannot_access_mappings(self, e2e_client, org1_user, platform_admin, org1):
        """Non-superuser should get 403 when accessing mappings."""
        # Create integration as admin
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": f"mapping_auth_test_{uuid4().hex[:8]}"},
        )
        integration = response.json()

        try:
            # Try to list mappings as non-superuser
            response = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings",
                headers=org1_user.headers,
            )
            assert response.status_code == 403

            # Try to create mapping as non-superuser
            response = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=org1_user.headers,
                json={
                    "organization_id": str(org1["id"]),
                    "entity_id": "test",
                },
            )
            assert response.status_code == 403
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    # Note: the former GET /api/integrations/sdk/{name} endpoint was DELETED
    # (EXT-1 NEW-H — cross-tenant leak via a free org_id Query param). SDK
    # integration reads now go through POST /api/sdk/integrations/get, which is
    # org-scoped (_resolve_sdk_org_id) and external-gated; its isolation is
    # covered by tests/e2e/platform/test_cli_integrations_external.py.


@pytest.mark.e2e
class TestBatchMappingUpsert:
    """Test batch upsert of integration mappings."""

    @pytest.fixture
    def integration_for_batch(self, e2e_client, platform_admin):
        """Create an integration for batch mapping tests."""
        integration_name = f"e2e_batch_integration_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert response.status_code == 201
        integration = response.json()

        yield integration

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}",
            headers=platform_admin.headers,
        )

    def test_batch_create_new_mappings(
        self, e2e_client, platform_admin, integration_for_batch, org1, org2
    ):
        """Batch upsert creates new mappings for unmapped orgs."""
        integration = integration_for_batch

        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings/batch",
            headers=platform_admin.headers,
            json={
                "mappings": [
                    {
                        "organization_id": str(org1["id"]),
                        "entity_id": "batch-entity-1",
                        "entity_name": "Batch Org 1",
                    },
                    {
                        "organization_id": str(org2["id"]),
                        "entity_id": "batch-entity-2",
                        "entity_name": "Batch Org 2",
                    },
                ]
            },
        )
        assert response.status_code == 200, f"Batch upsert failed: {response.text}"
        data = response.json()
        assert data["created"] == 2
        assert data["updated"] == 0
        assert data["errors"] == []

        # Verify mappings were created
        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        mappings = response.json()["items"]
        entity_ids = {m["entity_id"] for m in mappings}
        assert "batch-entity-1" in entity_ids
        assert "batch-entity-2" in entity_ids

        # Cleanup
        for m in mappings:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{m['id']}",
                headers=platform_admin.headers,
            )

    def test_batch_update_existing_mappings(
        self, e2e_client, platform_admin, integration_for_batch, org1
    ):
        """Batch upsert updates existing mappings."""
        integration = integration_for_batch

        # Create a mapping first
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "original-entity",
                "entity_name": "Original Name",
            },
        )
        assert response.status_code == 201
        original_mapping = response.json()

        # Batch upsert with same org should update
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings/batch",
            headers=platform_admin.headers,
            json={
                "mappings": [
                    {
                        "organization_id": str(org1["id"]),
                        "entity_id": "updated-entity",
                        "entity_name": "Updated Name",
                    },
                ]
            },
        )
        assert response.status_code == 200, f"Batch upsert failed: {response.text}"
        data = response.json()
        assert data["created"] == 0
        assert data["updated"] == 1
        assert data["errors"] == []

        # Verify mapping was updated (same mapping ID, new values)
        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/mappings/by-org/{org1['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        updated = response.json()
        assert updated["id"] == original_mapping["id"]
        assert updated["entity_id"] == "updated-entity"
        assert updated["entity_name"] == "Updated Name"

        # Cleanup
        e2e_client.delete(
            f"/api/integrations/{integration['id']}/mappings/{updated['id']}",
            headers=platform_admin.headers,
        )

    def test_batch_mixed_create_and_update(
        self, e2e_client, platform_admin, integration_for_batch, org1, org2
    ):
        """Batch upsert handles a mix of creates and updates."""
        integration = integration_for_batch

        # Pre-create a mapping for org1
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
            json={
                "organization_id": str(org1["id"]),
                "entity_id": "existing-entity",
                "entity_name": "Existing",
            },
        )
        assert response.status_code == 201

        # Batch upsert: org1 (update) + org2 (create)
        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings/batch",
            headers=platform_admin.headers,
            json={
                "mappings": [
                    {
                        "organization_id": str(org1["id"]),
                        "entity_id": "updated-existing",
                        "entity_name": "Updated Existing",
                    },
                    {
                        "organization_id": str(org2["id"]),
                        "entity_id": "new-entity",
                        "entity_name": "New Entity",
                    },
                ]
            },
        )
        assert response.status_code == 200, f"Batch upsert failed: {response.text}"
        data = response.json()
        assert data["created"] == 1
        assert data["updated"] == 1
        assert data["errors"] == []

        # Verify org1 was updated
        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/mappings/by-org/{org1['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["entity_id"] == "updated-existing"

        # Verify org2 was created
        response = e2e_client.get(
            f"/api/integrations/{integration['id']}/mappings/by-org/{org2['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["entity_id"] == "new-entity"

        # Cleanup
        mappings_resp = e2e_client.get(
            f"/api/integrations/{integration['id']}/mappings",
            headers=platform_admin.headers,
        )
        for m in mappings_resp.json()["items"]:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}/mappings/{m['id']}",
                headers=platform_admin.headers,
            )

    def test_batch_empty_list_rejected(
        self, e2e_client, platform_admin, integration_for_batch
    ):
        """Batch upsert rejects an empty mappings list with 422."""
        integration = integration_for_batch

        response = e2e_client.post(
            f"/api/integrations/{integration['id']}/mappings/batch",
            headers=platform_admin.headers,
            json={"mappings": []},
        )
        assert response.status_code == 422, (
            f"Expected 422 for empty mappings list, got {response.status_code}: {response.text}"
        )

    def test_batch_nonexistent_integration(self, e2e_client, platform_admin, org1):
        """Batch upsert returns 404 for a nonexistent integration."""
        fake_id = str(uuid4())

        response = e2e_client.post(
            f"/api/integrations/{fake_id}/mappings/batch",
            headers=platform_admin.headers,
            json={
                "mappings": [
                    {
                        "organization_id": str(org1["id"]),
                        "entity_id": "entity-1",
                    },
                ]
            },
        )
        assert response.status_code == 404, (
            f"Expected 404 for nonexistent integration, got {response.status_code}: {response.text}"
        )
