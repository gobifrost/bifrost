"""
E2E tests for configuration management.

Tests CRUD operations for different config types (string, int, bool, json, secret).
"""

import logging
from uuid import uuid4

import pytest


logger = logging.getLogger(__name__)

def _create_config(e2e_client, headers, key, value, type_="string", **kwargs):
    """Create a config and return the response JSON with id."""
    response = e2e_client.post(
        "/api/config",
        headers=headers,
        json={"key": key, "value": value, "type": type_, **kwargs},
    )
    assert response.status_code == 201, f"Create config '{key}' failed: {response.text}"
    return response.json()


def _delete_config(e2e_client, headers, config_id):
    """Delete a config by UUID."""
    e2e_client.delete(f"/api/config/{config_id}", headers=headers)


def _sdk_config_value(e2e_client, headers, key, scope=None):
    """Read a config through the engine-facing SDK path (decrypts secrets)."""
    body = {"key": key}
    if scope is not None:
        body["scope"] = scope
    response = e2e_client.post("/api/sdk/config/get", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["value"]


@pytest.mark.e2e
class TestConfigCRUD:
    """Test configuration CRUD operations."""

    def test_set_global_config_string(self, e2e_client, platform_admin):
        """Platform admin creates STRING config in GLOBAL scope."""
        response = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={
                "key": "e2e_test_timeout",
                "value": "30",
                "type": "string",
                "description": "E2E test config",
            },
        )
        assert response.status_code == 201, f"Create config failed: {response.text}"
        data = response.json()
        assert data["key"] == "e2e_test_timeout"
        assert data["value"] == "30"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, data["id"])

    def test_set_int_config(self, e2e_client, platform_admin):
        """Platform admin creates INT config."""
        data = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_max_retries", "5", "int", description="Max retries setting",
        )

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, data["id"])

    def test_set_bool_config(self, e2e_client, platform_admin):
        """Platform admin creates BOOL config."""
        data = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_feature_flag", "true", "bool", description="Feature flag",
        )

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, data["id"])

    def test_set_json_config(self, e2e_client, platform_admin):
        """Platform admin creates JSON config."""
        data = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_settings", '{"enabled": true, "level": 3}', "json",
            description="JSON settings",
        )

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, data["id"])

    def test_set_secret_config(self, e2e_client, platform_admin):
        """Platform admin creates SECRET config (encrypted)."""
        data = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_api_key", "secret-api-key-12345", "secret",
            description="Test API key",
        )

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, data["id"])


@pytest.mark.e2e
class TestConfigSecurity:
    """Test configuration security features."""

    def test_list_config_masks_secrets(self, e2e_client, platform_admin):
        """Listing configs shows [SECRET] for encrypted values."""
        # Create a secret first
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_test_secret", "super-secret-value", "secret",
            description="Test secret",
        )

        # List configs and verify masking
        response = e2e_client.get(
            "/api/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List config failed: {response.text}"
        configs = response.json()

        # Find the secret config
        secret_config = next((c for c in configs if c["key"] == "e2e_test_secret"), None)
        assert secret_config is not None, "Secret config not found"
        assert secret_config["value"] == "[SECRET]", "Secret should be masked"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, created["id"])


@pytest.mark.e2e
class TestConfigGetById:
    """Test GET /api/config/{config_id}."""

    def test_admin_can_read_any_org_config_by_id(self, e2e_client, platform_admin, org2):
        """A platform admin can read a config scoped to a different org by id.

        No org cascade on an ID lookup — matches PUT/DELETE, which also key
        on ``id`` alone (see ``ConfigRepository.get_config_by_id``).
        """
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_get_by_id_other_org", "other-org-value", "string",
            organization_id=org2["id"],
        )
        try:
            response = e2e_client.get(
                f"/api/config/{created['id']}", headers=platform_admin.headers
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["id"] == created["id"]
            assert body["key"] == "e2e_get_by_id_other_org"
            assert body["value"] == "other-org-value"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_get_by_id_masks_secret(self, e2e_client, platform_admin):
        """A secret-type config's value stays masked through the by-id read."""
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_get_by_id_secret", "super-secret-value", "secret",
        )
        try:
            response = e2e_client.get(
                f"/api/config/{created['id']}", headers=platform_admin.headers
            )
            assert response.status_code == 200, response.text
            assert response.json()["value"] == "[SECRET]"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_get_by_id_not_found(self, e2e_client, platform_admin):
        response = e2e_client.get(
            "/api/config/00000000-0000-0000-0000-000000000000",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_org_user_cannot_get_config_by_id(self, e2e_client, platform_admin, org1_user):
        """Config routes are superuser-only — the new GET is no exception."""
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_get_by_id_org_user", "value", "string",
        )
        try:
            response = e2e_client.get(
                f"/api/config/{created['id']}", headers=org1_user.headers
            )
            assert response.status_code == 403
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])


@pytest.mark.e2e
class TestConfigAccess:
    """Test configuration access control."""

    def test_org_user_cannot_manage_config(self, e2e_client, org1_user):
        """Org user cannot create config (403)."""
        response = e2e_client.post(
            "/api/config",
            headers=org1_user.headers,
            json={
                "key": "hacker_config",
                "value": "evil",
                "type": "string",
            },
        )
        assert response.status_code == 403, \
            f"Org user should not create config: {response.status_code}"

    def test_config_list_requires_auth(self, e2e_client):
        """Config listing requires authentication."""
        response = e2e_client.get("/api/config")
        assert response.status_code == 401

    def test_delete_config(self, e2e_client, platform_admin):
        """Platform admin can delete config."""
        # Create config to delete
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_delete_test", "to_be_deleted", description="Config to delete",
        )

        # Delete the config by UUID
        response = e2e_client.delete(
            f"/api/config/{created['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204, f"Delete config failed: {response.status_code}"

        # Verify it's gone
        response = e2e_client.get(
            "/api/config",
            headers=platform_admin.headers,
        )
        configs = response.json()
        deleted_config = next((c for c in configs if c["key"] == "e2e_delete_test"), None)
        assert deleted_config is None, "Config should be deleted"

    def test_org_user_cannot_modify_config(self, e2e_client, platform_admin, org1_user):
        """Org user cannot PUT/update config (403 or 405 if PUT not supported)."""
        # Admin creates a config
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_modify_test", "original",
        )

        # Org user tries to update it
        response = e2e_client.put(
            "/api/config/e2e_modify_test",
            headers=org1_user.headers,
            json={"value": "hacked"},
        )
        # 403 = forbidden, 404 = route doesn't exist (PUT not implemented), 405 = method not allowed
        assert response.status_code in [403, 404, 405], \
            f"Org user should not modify config: {response.status_code}"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_org_user_cannot_delete_config(self, e2e_client, platform_admin, org1_user):
        """Org user cannot DELETE config (403)."""
        # Admin creates a config
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_nodelete_test", "protected",
        )

        # Org user tries to delete it
        response = e2e_client.delete(
            f"/api/config/{created['id']}",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, \
            f"Org user should not delete config: {response.status_code}"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, created["id"])


@pytest.mark.e2e
class TestConfigPartialUpdate:
    """Test partial update (PUT) for config entries, especially secrets."""

    def test_update_secret_without_value_preserves_existing(self, e2e_client, platform_admin):
        """Updating a secret config without providing a value keeps the existing encrypted value."""
        key = f"e2e_secret_partial_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers,
            key, "my-original-secret", "secret",
            description="Secret for partial update test",
        )
        config_id = created["id"]

        # Update only the description, sending null for value
        response = e2e_client.put(
            f"/api/config/{config_id}",
            headers=platform_admin.headers,
            json={"description": "Updated description"},
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        data = response.json()
        assert data["description"] == "Updated description"
        assert data["type"] == "secret"
        assert data["value"] == "[SECRET]"
        assert _sdk_config_value(e2e_client, platform_admin.headers, key) == "my-original-secret"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, config_id)

    def test_update_secret_with_empty_string_preserves_existing(self, e2e_client, platform_admin):
        """Sending empty string for secret value keeps existing value."""
        key = f"e2e_secret_empty_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers,
            key, "original-secret-value", "secret",
        )
        config_id = created["id"]

        # Update with empty string value
        response = e2e_client.put(
            f"/api/config/{config_id}",
            headers=platform_admin.headers,
            json={"value": ""},
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        assert response.json()["value"] == "[SECRET]"
        assert _sdk_config_value(e2e_client, platform_admin.headers, key) == "original-secret-value"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, config_id)

    def test_update_secret_with_new_value_re_encrypts(self, e2e_client, platform_admin):
        """Providing a new value for a secret config re-encrypts it."""
        key = f"e2e_secret_reencrypt_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers,
            key, "original-secret", "secret",
        )
        config_id = created["id"]

        # Update with a new secret value
        response = e2e_client.put(
            f"/api/config/{config_id}",
            headers=platform_admin.headers,
            json={"value": "new-secret-value"},
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        assert response.json()["value"] == "[SECRET]"
        assert _sdk_config_value(e2e_client, platform_admin.headers, key) == "new-secret-value"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, config_id)

    def test_secret_write_echoes_are_redacted(self, e2e_client, platform_admin):
        """POST and PUT echo a masked value — neither plaintext nor ciphertext —
        while the engine-facing SDK read still decrypts the stored value.

        Global scope: global writes HSET the stored value into the shared
        cache, so this also proves the cache never receives the mask."""
        key = f"e2e_secret_echo_{uuid4().hex[:8]}"
        plaintext = f"plain-{uuid4().hex}"
        rotated = f"rotated-{uuid4().hex}"

        create = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={"key": key, "value": plaintext, "type": "secret", "organization_id": None},
        )
        assert create.status_code == 201, create.text
        config_id = create.json()["id"]
        try:
            assert create.json()["value"] == "[SECRET]"
            assert plaintext not in create.text
            assert _sdk_config_value(e2e_client, platform_admin.headers, key, "global") == plaintext

            update = e2e_client.put(
                f"/api/config/{config_id}",
                headers=platform_admin.headers,
                json={"value": rotated},
            )
            assert update.status_code == 200, update.text
            assert update.json()["value"] == "[SECRET]"
            assert rotated not in update.text
            assert _sdk_config_value(e2e_client, platform_admin.headers, key, "global") == rotated
        finally:
            _delete_config(e2e_client, platform_admin.headers, config_id)

    def test_update_non_secret_still_requires_value_concept(self, e2e_client, platform_admin):
        """Non-secret configs can be partially updated too (only provided fields change)."""
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_string_partial", "original-value", "string",
            description="Original description",
        )
        config_id = created["id"]

        # Update only description
        response = e2e_client.put(
            f"/api/config/{config_id}",
            headers=platform_admin.headers,
            json={"description": "New description"},
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        data = response.json()
        assert data["description"] == "New description"
        # Value should be preserved
        assert data["value"] == "original-value"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, config_id)

    def test_update_config_not_found(self, e2e_client, platform_admin):
        """Updating a non-existent config returns 404."""
        response = e2e_client.put(
            "/api/config/00000000-0000-0000-0000-000000000000",
            headers=platform_admin.headers,
            json={"description": "ghost"},
        )
        assert response.status_code == 404


@pytest.mark.e2e
class TestConfigScoping:
    """Test configuration scoping (global vs org-scoped)."""

    def test_config_with_org_scope(self, e2e_client, platform_admin, org1):
        """Platform admin can create org-scoped config."""
        response = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={
                "key": "e2e_org_config",
                "value": "org-specific-value",
                "type": "string",
                "description": "Org-scoped config",
                "organization_id": org1["id"],
            },
        )
        assert response.status_code == 201, \
            f"Create org config failed: {response.status_code} - {response.text}"

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, response.json()["id"])


@pytest.mark.e2e
class TestConfigScopeFiltering:
    """Test config scope filtering works correctly."""

    @pytest.fixture
    def scoped_configs(self, e2e_client, platform_admin, org1, org2):
        """Create configs in different scopes for testing."""
        configs = {}

        # Create global config (no organization_id / scope=global)
        response = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={
                "key": "scope_test_global",
                "value": "global-value",
                "type": "string",
                "description": "Global config for scope testing",
                "organization_id": None,
            },
        )
        assert response.status_code == 201, f"Failed to create global config: {response.text}"
        configs["global"] = response.json()

        # Create org1 config
        response = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={
                "key": "scope_test_org1",
                "value": "org1-value",
                "type": "string",
                "description": "Org1 config for scope testing",
                "organization_id": org1["id"],
            },
        )
        assert response.status_code == 201, f"Failed to create org1 config: {response.text}"
        configs["org1"] = response.json()

        # Create org2 config
        response = e2e_client.post(
            "/api/config",
            headers=platform_admin.headers,
            json={
                "key": "scope_test_org2",
                "value": "org2-value",
                "type": "string",
                "description": "Org2 config for scope testing",
                "organization_id": org2["id"],
            },
        )
        assert response.status_code == 201, f"Failed to create org2 config: {response.text}"
        configs["org2"] = response.json()

        yield configs

        # Cleanup using UUIDs
        for cfg in configs.values():
            try:
                _delete_config(e2e_client, platform_admin.headers, cfg["id"])
            except Exception as e:
                # Best-effort fixture cleanup; teardown shouldn't fail the test
                logger.debug(f"config fixture cleanup error: {e}")

    def test_platform_admin_no_scope_sees_all(
        self, e2e_client, platform_admin, scoped_configs
    ):
        """Platform admin with no scope sees ALL configs."""
        response = e2e_client.get(
            "/api/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        config_keys = [c["key"] for c in response.json()]

        assert scoped_configs["global"]["key"] in config_keys, "Should see global config"
        assert scoped_configs["org1"]["key"] in config_keys, "Should see org1 config"
        assert scoped_configs["org2"]["key"] in config_keys, "Should see org2 config"

    def test_platform_admin_scope_global_sees_only_global(
        self, e2e_client, platform_admin, scoped_configs
    ):
        """Platform admin with scope=global sees ONLY global configs."""
        response = e2e_client.get(
            "/api/config",
            params={"scope": "global"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        config_keys = [c["key"] for c in response.json()]

        assert scoped_configs["global"]["key"] in config_keys, "Should see global config"
        assert scoped_configs["org1"]["key"] not in config_keys, "Should NOT see org1 config"
        assert scoped_configs["org2"]["key"] not in config_keys, "Should NOT see org2 config"

    def test_platform_admin_scope_org_sees_only_that_org(
        self, e2e_client, platform_admin, org1, scoped_configs
    ):
        """Platform admin with scope={org1} sees ONLY org1 configs (NOT global)."""
        response = e2e_client.get(
            "/api/config",
            params={"scope": org1["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        config_keys = [c["key"] for c in response.json()]

        # KEY ASSERTION: Global should NOT be included when filtering by org
        assert scoped_configs["global"]["key"] not in config_keys, "Should NOT see global config"
        assert scoped_configs["org1"]["key"] in config_keys, "Should see org1 config"
        assert scoped_configs["org2"]["key"] not in config_keys, "Should NOT see org2 config"
