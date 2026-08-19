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
        # Clear any cookies from previous tests and make unauthenticated request
        e2e_client.cookies.clear()
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
        # Create a secret config
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_secret_partial", "my-original-secret", "secret",
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
        # Value should still be the encrypted secret (not empty/null)
        assert data["value"] is not None
        assert data["value"] != ""

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, config_id)

    def test_update_secret_with_empty_string_preserves_existing(self, e2e_client, platform_admin):
        """Sending empty string for secret value keeps existing value."""
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_secret_empty", "original-secret-value", "secret",
        )
        config_id = created["id"]
        original_value = created["value"]

        # Update with empty string value
        response = e2e_client.put(
            f"/api/config/{config_id}",
            headers=platform_admin.headers,
            json={"value": ""},
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        data = response.json()
        # The encrypted value should be unchanged
        assert data["value"] == original_value

        # Cleanup
        _delete_config(e2e_client, platform_admin.headers, config_id)

    def test_update_secret_with_new_value_re_encrypts(self, e2e_client, platform_admin):
        """Providing a new value for a secret config re-encrypts it."""
        created = _create_config(
            e2e_client, platform_admin.headers,
            "e2e_secret_reencrypt", "original-secret", "secret",
        )
        config_id = created["id"]
        original_value = created["value"]

        # Update with a new secret value
        response = e2e_client.put(
            f"/api/config/{config_id}",
            headers=platform_admin.headers,
            json={"value": "new-secret-value"},
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        data = response.json()
        # The encrypted value should be different now
        assert data["value"] != original_value
        assert data["value"] is not None

        # Cleanup
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


@pytest.mark.e2e
class TestConfigValueTypeRoundTrip:
    """Store-then-read round trip for every ``ConfigType``.

    The write surface (``POST /api/config``) takes ``value`` as a **string**
    for all five types — that is the wire contract every caller uses. The
    read surface (``POST /api/sdk/config/get``) is what coerces the stored
    string back into a typed value using ``config_type``.

    These tests pin both halves together so the string-in/coerce-out contract
    cannot drift silently. Without them, ``int`` and ``bool`` coercion had no
    coverage at all.
    """

    @staticmethod
    def _read_typed(e2e_client, headers, key):
        """Read a config through the SDK path that applies type coercion."""
        response = e2e_client.post(
            "/api/sdk/config/get",
            headers=headers,
            json={"key": key},
        )
        assert response.status_code == 200, \
            f"SDK config read for '{key}' failed: {response.text}"
        return response.json()

    def test_string_round_trip(self, e2e_client, platform_admin):
        """A string config reads back as the same string."""
        key = f"e2e_rt_string_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers, key, "hello world", "string",
        )
        try:
            entry = self._read_typed(e2e_client, platform_admin.headers, key)
            assert entry["config_type"] == "string"
            assert entry["value"] == "hello world"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_int_round_trip(self, e2e_client, platform_admin):
        """An int config is written as a string and reads back as an int."""
        key = f"e2e_rt_int_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers, key, "5", "int",
        )
        try:
            entry = self._read_typed(e2e_client, platform_admin.headers, key)
            assert entry["config_type"] == "int"
            assert entry["value"] == 5, f"expected coercion to int, got {entry['value']!r}"
            assert not isinstance(entry["value"], str)
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_bool_round_trip(self, e2e_client, platform_admin):
        """A bool config is written as a string and reads back as a bool."""
        key = f"e2e_rt_bool_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers, key, "true", "bool",
        )
        try:
            entry = self._read_typed(e2e_client, platform_admin.headers, key)
            assert entry["config_type"] == "bool"
            assert entry["value"] is True, \
                f"expected coercion to bool, got {entry['value']!r}"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_json_round_trip(self, e2e_client, platform_admin):
        """A json config is written as a JSON string and reads back parsed.

        This is the case that makes ``value: str`` the correct write-side
        contract: the object travels as a serialized string and is parsed on
        read, exactly like int and bool are coerced.
        """
        key = f"e2e_rt_json_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers,
            key, '{"enabled": true, "level": 3}', "json",
        )
        try:
            entry = self._read_typed(e2e_client, platform_admin.headers, key)
            assert entry["config_type"] == "json"
            assert entry["value"] == {"enabled": True, "level": 3}, \
                f"expected parsed object, got {entry['value']!r}"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_secret_round_trip(self, e2e_client, platform_admin):
        """A secret config is stored encrypted and reads back decrypted.

        The list surface masks it (covered by TestConfigSecurity); the SDK
        read surface is the one that decrypts.
        """
        key = f"e2e_rt_secret_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers,
            key, "secret-api-key-12345", "secret",
        )
        try:
            entry = self._read_typed(e2e_client, platform_admin.headers, key)
            assert entry["config_type"] == "secret"
            assert entry["value"] == "secret-api-key-12345", \
                "secret should be decrypted on the SDK read path"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_stored_envelope_is_unwrapped_on_list(self, e2e_client, platform_admin):
        """The JSONB ``{"value": X}`` storage envelope never reaches a caller.

        Config rows persist as a single-key JSONB envelope. Every read path
        unwraps it, so the API surface exposes the scalar. This pins that the
        envelope is a storage detail, not part of any contract.
        """
        key = f"e2e_rt_envelope_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers, key, "unwrapped", "string",
        )
        try:
            assert created["value"] == "unwrapped", \
                f"create response leaked the storage envelope: {created['value']!r}"

            response = e2e_client.get("/api/config", headers=platform_admin.headers)
            assert response.status_code == 200, response.text
            listed = next(
                (c for c in response.json() if c["key"] == key), None
            )
            assert listed is not None, f"config '{key}' missing from list"
            assert listed["value"] == "unwrapped", \
                f"list response leaked the storage envelope: {listed['value']!r}"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])


@pytest.mark.e2e
class TestConfigGetById:
    """``GET /api/config/{config_id}`` — the by-ID read endpoint.

    Before this endpoint existed, both the CLI (``bifrost configs get``) and
    the MCP ``get_config`` tool fetched the whole ``GET /api/config`` list and
    filtered client-side. These tests pin the endpoint those surfaces now use.
    """

    def test_get_by_id_returns_the_config(self, e2e_client, platform_admin):
        """A known UUID returns that config."""
        key = f"e2e_byid_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers, key, "by-id-value", "string",
        )
        try:
            response = e2e_client.get(
                f"/api/config/{created['id']}", headers=platform_admin.headers,
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["id"] == created["id"]
            assert data["key"] == key
            assert data["value"] == "by-id-value"
            assert data["type"] == "string"
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_get_by_id_matches_the_list_payload(self, e2e_client, platform_admin):
        """The by-ID row is identical to the same row from the list.

        This is the contract the CLI and MCP relied on when they filtered the
        list client-side, so switching them to this endpoint must not change
        what a caller sees.
        """
        key = f"e2e_byid_parity_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers, key, "parity", "string",
            description="parity check",
        )
        try:
            single = e2e_client.get(
                f"/api/config/{created['id']}", headers=platform_admin.headers,
            )
            assert single.status_code == 200, single.text

            listed = e2e_client.get("/api/config", headers=platform_admin.headers)
            assert listed.status_code == 200, listed.text
            from_list = next(
                (c for c in listed.json() if c["id"] == created["id"]), None
            )
            assert from_list is not None, "config missing from list payload"
            assert single.json() == from_list, (
                "by-ID payload diverged from the list payload"
            )
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_get_by_id_masks_secret_value(self, e2e_client, platform_admin):
        """A secret config's value is masked, exactly as the list masks it.

        A by-ID reader must not become a way to read a stored secret that the
        list endpoint refuses to show.
        """
        key = f"e2e_byid_secret_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers,
            key, "super-secret-value", "secret",
        )
        try:
            response = e2e_client.get(
                f"/api/config/{created['id']}", headers=platform_admin.headers,
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["value"] == "[SECRET]", \
                f"secret leaked through the by-ID read: {data['value']!r}"
            assert "super-secret-value" not in response.text
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])

    def test_get_by_id_unknown_uuid_404s(self, e2e_client, platform_admin):
        """An unknown but well-formed UUID is a 404, not a 500 or empty 200."""
        response = e2e_client.get(
            f"/api/config/{uuid4()}", headers=platform_admin.headers,
        )
        assert response.status_code == 404, \
            f"expected 404 for unknown config id, got {response.status_code}"

    def test_get_by_id_malformed_uuid_422s(self, e2e_client, platform_admin):
        """A non-UUID path segment is rejected by validation."""
        response = e2e_client.get(
            "/api/config/not-a-uuid", headers=platform_admin.headers,
        )
        assert response.status_code == 422, \
            f"expected 422 for malformed config id, got {response.status_code}"

    def test_get_by_id_requires_auth(self, e2e_client):
        """Unauthenticated reads are rejected."""
        e2e_client.cookies.clear()
        response = e2e_client.get(f"/api/config/{uuid4()}")
        assert response.status_code == 401

    def test_org_user_cannot_get_config_by_id(
        self, e2e_client, platform_admin, org1_user
    ):
        """A non-superuser cannot read a config by ID.

        The list endpoint is superuser-gated; the by-ID endpoint must carry the
        same gate rather than opening a narrower read to regular users.
        """
        key = f"e2e_byid_authz_{uuid4().hex[:8]}"
        created = _create_config(
            e2e_client, platform_admin.headers, key, "admin-only", "string",
        )
        try:
            response = e2e_client.get(
                f"/api/config/{created['id']}", headers=org1_user.headers,
            )
            assert response.status_code == 403, \
                f"org user should not read config by ID: {response.status_code}"
            assert "admin-only" not in response.text
        finally:
            _delete_config(e2e_client, platform_admin.headers, created["id"])
