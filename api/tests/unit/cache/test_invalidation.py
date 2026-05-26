"""
Unit tests for cache invalidation functions.

Tests the invalidation functions used by API routes after write operations.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.cache.invalidation import (
    cleanup_execution_cache,
    invalidate_all_config,
    invalidate_all_orgs,
    invalidate_config,
    invalidate_form,
    invalidate_form_assignment,
    invalidate_org,
    invalidate_role,
    invalidate_role_forms,
    invalidate_role_users,
    upsert_config,
)


class TestConfigInvalidation:
    """Tests for config cache invalidation."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()
        return mock_r

    @pytest.mark.asyncio
    async def test_invalidate_config_with_specific_key(self, mock_redis):
        """invalidate_config deletes hash and specific key."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_config("org-123", "api_key")

            # Should delete both the hash and the specific key
            assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_config_all_keys(self, mock_redis):
        """invalidate_config with no key deletes only the hash."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_config("org-456", key=None)

            # Should only delete the hash
            assert mock_redis.delete.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_config_global_scope(self, mock_redis):
        """invalidate_config works for global scope."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_config(None, "global_setting")

            assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_config_handles_error(self, mock_redis):
        """invalidate_config handles Redis errors gracefully."""
        mock_redis.delete.side_effect = Exception("Redis error")

        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            # Should not raise, just log warning
            await invalidate_config("org-789", "key")

    @pytest.mark.asyncio
    async def test_invalidate_all_config(self, mock_redis):
        """invalidate_all_config delegates to invalidate_config."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_all_config("org-999")

            mock_redis.delete.assert_called_once()


class TestConfigInvalidationCorrectness:
    """Regression tests for the partial-hash bug and global-version
    invalidation. These pin down the actual behavior changes from the
    2026-05 org-scoping overhaul (phase 5).

    The bug being prevented:
        Org-scoped config caches are merged views. If an org config write
        does HSET on one field, the cached merged hash now contains a
        partial overlay — the next read sees the partial hash and treats
        the missing global fallback fields as deleted.

    The companion bug:
        Global config writes used to leave per-org merged caches stale
        until TTL. The new versioned-key scheme invalidates them all in
        O(1) via INCR.
    """

    @pytest.fixture
    def mock_redis(self):
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()
        mock_r.hset = AsyncMock()
        mock_r.incr = AsyncMock()
        mock_r.ttl = AsyncMock(return_value=60)
        mock_r.expire = AsyncMock()
        mock_r.get = AsyncMock(return_value=b"0")
        return mock_r

    @pytest.mark.asyncio
    async def test_org_upsert_deletes_not_hsets(self, mock_redis):
        """Regression: org-scoped writes must DELETE the merged hash,
        never HSET into it. HSET would create a partial hash where
        global fallback values are silently hidden on the next read.
        """
        with patch(
            "src.core.cache.invalidation.get_shared_redis", return_value=mock_redis
        ):
            await upsert_config("org-123", "api_key", "encrypted-value", "secret")

        # The merged hash key must be DELETED — never HSET.
        mock_redis.delete.assert_called()
        mock_redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_global_upsert_bumps_version(self, mock_redis):
        """Regression: global writes must INCR the version key so every
        org's merged cache becomes stale by key. Without this, orgs
        keep returning the pre-write global fallback until TTL.
        """
        with patch(
            "src.core.cache.invalidation.get_shared_redis", return_value=mock_redis
        ):
            await upsert_config(None, "global_setting", "value", "string")

        # Global hash gets HSET (no merge concern at global scope).
        mock_redis.hset.assert_called()
        # AND the version is INCR'd to invalidate every org's merged cache.
        mock_redis.incr.assert_called_once()
        # The version key name must be the canonical one.
        assert (
            mock_redis.incr.call_args.args[0]
            == "bifrost:config:global_version"
        )

    @pytest.mark.asyncio
    async def test_org_invalidate_does_not_bump_version(self, mock_redis):
        """Org-scoped invalidation must NOT bump the global version —
        otherwise every other org's cache is invalidated by mistake.
        """
        with patch(
            "src.core.cache.invalidation.get_shared_redis", return_value=mock_redis
        ):
            await invalidate_config("org-abc", "some_key")

        mock_redis.incr.assert_not_called()

    @pytest.mark.asyncio
    async def test_global_invalidate_bumps_version(self, mock_redis):
        """Global invalidation must bump the version, same as global
        upsert.
        """
        with patch(
            "src.core.cache.invalidation.get_shared_redis", return_value=mock_redis
        ):
            await invalidate_config(None, "some_key")

        mock_redis.incr.assert_called_once()


class TestFormInvalidation:
    """Tests for form cache invalidation."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()

        async def empty_iter():
            return
            yield  # Makes this an async generator that yields nothing

        mock_r.scan_iter = lambda pattern: empty_iter()
        return mock_r

    @pytest.mark.asyncio
    async def test_invalidate_form_with_form_id(self, mock_redis):
        """invalidate_form deletes hash and specific form key."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_form("org-123", "form-abc")

            assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_form_all_forms(self, mock_redis):
        """invalidate_form with no form_id deletes only the hash."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_form("org-456", form_id=None)

            assert mock_redis.delete.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_form_clears_user_forms(self, mock_redis):
        """invalidate_form also clears user-specific form lists."""

        async def user_forms_iter():
            yield "bifrost:org:org-123:user_forms:user-1"
            yield "bifrost:org:org-123:user_forms:user-2"

        mock_redis.scan_iter = lambda pattern: user_forms_iter()

        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_form("org-123", "form-abc")

            # 2 for hash + form, plus 2 for user_forms keys
            assert mock_redis.delete.call_count == 4

    @pytest.mark.asyncio
    async def test_invalidate_form_assignment(self, mock_redis):
        """invalidate_form_assignment delegates to invalidate_form."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_form_assignment("org-789", "form-xyz")

            assert mock_redis.delete.call_count == 2


class TestRoleInvalidation:
    """Tests for role cache invalidation."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()

        async def empty_iter():
            return
            yield

        mock_r.scan_iter = lambda pattern: empty_iter()
        return mock_r

    @pytest.mark.asyncio
    async def test_invalidate_role_with_role_id(self, mock_redis):
        """invalidate_role deletes hash and role-related keys."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_role("org-123", "role-abc")

            # hash + role + role_users + role_forms
            assert mock_redis.delete.call_count == 4

    @pytest.mark.asyncio
    async def test_invalidate_role_all_roles(self, mock_redis):
        """invalidate_role with no role_id deletes only the hash."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_role("org-456", role_id=None)

            assert mock_redis.delete.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_role_users(self, mock_redis):
        """invalidate_role_users deletes role users key and user_forms."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_role_users("org-789", "role-xyz")

            # At least the role_users key
            assert mock_redis.delete.call_count >= 1

    @pytest.mark.asyncio
    async def test_invalidate_role_forms(self, mock_redis):
        """invalidate_role_forms deletes role forms key and user_forms."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_role_forms("org-111", "role-222")

            # At least the role_forms key
            assert mock_redis.delete.call_count >= 1


class TestOrgInvalidation:
    """Tests for organization cache invalidation."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()

        async def empty_iter():
            return
            yield

        mock_r.scan_iter = lambda pattern: empty_iter()
        return mock_r

    @pytest.mark.asyncio
    async def test_invalidate_org(self, mock_redis):
        """invalidate_org deletes org key and list."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_org("org-123")

            # org key + orgs list
            assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_all_orgs(self, mock_redis):
        """invalidate_all_orgs deletes list and scans for org keys."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_all_orgs()

            # At least the list key
            assert mock_redis.delete.call_count >= 1

    @pytest.mark.asyncio
    async def test_invalidate_all_orgs_with_keys(self, mock_redis):
        """invalidate_all_orgs deletes all found org keys."""

        async def org_keys_iter():
            yield "bifrost:global:orgs:org-1"
            yield "bifrost:global:orgs:org-2"
            yield "bifrost:global:orgs:org-3"

        mock_redis.scan_iter = lambda pattern: org_keys_iter()

        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await invalidate_all_orgs()

            # list key + 3 org keys
            assert mock_redis.delete.call_count == 4


class TestExecutionCleanup:
    """Tests for execution cache cleanup."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock async Redis client."""
        mock_r = AsyncMock()
        mock_r.delete = AsyncMock()
        return mock_r

    @pytest.mark.asyncio
    async def test_cleanup_execution_cache(self, mock_redis):
        """cleanup_execution_cache deletes pending changes and logs."""
        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            await cleanup_execution_cache("exec-123")

            # pending changes + logs stream
            assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_execution_cache_handles_error(self, mock_redis):
        """cleanup_execution_cache handles errors gracefully."""
        mock_redis.delete.side_effect = Exception("Redis error")

        with patch("src.core.cache.invalidation.get_shared_redis", return_value=mock_redis):
            # Should not raise
            await cleanup_execution_cache("exec-456")
