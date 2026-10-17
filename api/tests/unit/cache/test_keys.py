"""
Unit tests for cache key generation.

Tests the Redis key generation functions to ensure consistent patterns.
"""


from src.core.cache.keys import (
    TTL_CONFIG,
    TTL_FORMS,
    TTL_ORGS,
    TTL_ROLES,
    config_hash_key,
    config_key,
    execution_logs_stream_key,
    form_key,
    forms_hash_key,
    org_key,
    orgs_list_key,
    role_forms_key,
    role_key,
    role_users_key,
    roles_hash_key,
    user_forms_key,
)


class TestConfigKeys:
    """Tests for config cache key generation."""

    def test_config_hash_key_with_org(self):
        """Config hash key with organization ID."""
        key = config_hash_key("org-123")
        assert key == "bifrost:org:org-123:config"

    def test_config_hash_key_global(self):
        """Config hash key for global scope."""
        key = config_hash_key(None)
        assert key == "bifrost:global:config"

    def test_config_hash_key_empty_string_treated_as_global(self):
        """Empty string org_id should be treated as global."""
        key = config_hash_key("")
        assert key == "bifrost:global:config"

    def test_config_key_with_org(self):
        """Individual config key with organization ID."""
        key = config_key("org-123", "api_key")
        assert key == "bifrost:org:org-123:config:api_key"

    def test_config_key_global(self):
        """Individual config key for global scope."""
        key = config_key(None, "database_url")
        assert key == "bifrost:global:config:database_url"


class TestFormsKeys:
    """Tests for forms cache key generation."""

    def test_forms_hash_key_with_org(self):
        """Forms hash key with organization ID."""
        key = forms_hash_key("org-789")
        assert key == "bifrost:org:org-789:forms"

    def test_forms_hash_key_global(self):
        """Forms hash key for global scope."""
        key = forms_hash_key(None)
        assert key == "bifrost:global:forms"

    def test_form_key_with_org(self):
        """Individual form key with organization ID."""
        key = form_key("org-789", "form-abc")
        assert key == "bifrost:org:org-789:forms:form-abc"

    def test_form_key_global(self):
        """Individual form key for global scope."""
        key = form_key(None, "form-def")
        assert key == "bifrost:global:forms:form-def"

    def test_user_forms_key(self):
        """User forms key for tracking user-accessible forms."""
        key = user_forms_key("org-123", "user-456")
        assert key == "bifrost:org:org-123:user_forms:user-456"

    def test_user_forms_key_global(self):
        """User forms key in global scope."""
        key = user_forms_key(None, "user-789")
        assert key == "bifrost:global:user_forms:user-789"


class TestRolesKeys:
    """Tests for roles cache key generation."""

    def test_roles_hash_key_with_org(self):
        """Roles hash key with organization ID."""
        key = roles_hash_key("org-111")
        assert key == "bifrost:org:org-111:roles"

    def test_roles_hash_key_global(self):
        """Roles hash key for global scope."""
        key = roles_hash_key(None)
        assert key == "bifrost:global:roles"

    def test_role_key_with_org(self):
        """Individual role key with organization ID."""
        key = role_key("org-111", "role-222")
        assert key == "bifrost:org:org-111:roles:role-222"

    def test_role_key_global(self):
        """Individual role key for global scope."""
        key = role_key(None, "role-333")
        assert key == "bifrost:global:roles:role-333"

    def test_role_users_key(self):
        """Role users key for tracking users in a role."""
        key = role_users_key("org-222", "role-333")
        assert key == "bifrost:org:org-222:roles:role-333:users"

    def test_role_users_key_global(self):
        """Role users key for global scope."""
        key = role_users_key(None, "role-444")
        assert key == "bifrost:global:roles:role-444:users"

    def test_role_forms_key(self):
        """Role forms key for tracking forms accessible by a role."""
        key = role_forms_key("org-444", "role-555")
        assert key == "bifrost:org:org-444:roles:role-555:forms"

    def test_role_forms_key_global(self):
        """Role forms key for global scope."""
        key = role_forms_key(None, "role-666")
        assert key == "bifrost:global:roles:role-666:forms"


class TestOrgKeys:
    """Tests for organization cache key generation."""

    def test_org_key(self):
        """Organization key."""
        key = org_key("org-666")
        assert key == "bifrost:global:orgs:org-666"

    def test_orgs_list_key(self):
        """Organizations list key."""
        key = orgs_list_key()
        assert key == "bifrost:global:orgs:_list"


class TestExecutionKeys:
    """Tests for execution-scoped key generation."""

    def test_execution_logs_stream_key(self):
        """Execution logs stream key."""
        key = execution_logs_stream_key("exec-888")
        assert key == "bifrost:logs:exec-888"


class TestTTLConstants:
    """Tests for TTL constant values."""

    def test_ttl_config(self):
        """Config TTL is 5 minutes."""
        assert TTL_CONFIG == 300

    def test_ttl_forms(self):
        """Forms TTL is 10 minutes."""
        assert TTL_FORMS == 600

    def test_ttl_roles(self):
        """Roles TTL is 10 minutes."""
        assert TTL_ROLES == 600

    def test_ttl_orgs(self):
        """Organizations TTL is 1 hour."""
        assert TTL_ORGS == 3600

class TestKeyPatternConsistency:
    """Tests for consistent key patterns across all functions."""

    def test_all_keys_start_with_bifrost(self):
        """All keys should start with 'bifrost:' prefix."""
        keys = [
            config_hash_key("org-1"),
            config_hash_key(None),
            config_key("org-1", "my_key"),
            forms_hash_key("org-3"),
            form_key("org-3", "form-1"),
            roles_hash_key("org-4"),
            role_key("org-4", "role-1"),
            org_key("org-5"),
            orgs_list_key(),
            execution_logs_stream_key("exec-2"),
            user_forms_key("org-6", "user-1"),
            role_users_key("org-7", "role-1"),
            role_forms_key("org-8", "role-2"),
        ]
        for key in keys:
            assert key.startswith("bifrost:"), f"Key {key} doesn't start with bifrost:"

    def test_org_scoped_keys_contain_org_id(self):
        """Organization-scoped keys should contain the org ID."""
        org_id = "test-org-uuid-123"
        keys = [
            config_hash_key(org_id),
            config_key(org_id, "test_key"),
            forms_hash_key(org_id),
            form_key(org_id, "form-1"),
            roles_hash_key(org_id),
            role_key(org_id, "role-1"),
        ]
        for key in keys:
            assert org_id in key, f"Key {key} doesn't contain org_id"

    def test_global_keys_contain_global_scope(self):
        """Global-scoped keys should contain 'global' segment."""
        keys = [
            config_hash_key(None),
            config_key(None, "test_key"),
            forms_hash_key(None),
            form_key(None, "form-1"),
            roles_hash_key(None),
            role_key(None, "role-1"),
            org_key("org-1"),  # Org keys are always in global scope
            orgs_list_key(),
        ]
        for key in keys:
            assert ":global:" in key, f"Key {key} doesn't contain :global:"

    def test_execution_scoped_keys_contain_execution_id(self):
        """Execution-scoped keys should contain the execution ID."""
        exec_id = "exec-test-uuid-456"
        keys = [
            execution_logs_stream_key(exec_id),
        ]
        for key in keys:
            assert exec_id in key, f"Key {key} doesn't contain execution_id"
