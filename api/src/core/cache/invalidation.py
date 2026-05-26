"""
Cache invalidation and upsert functions for Bifrost.

Used by API routes to maintain Redis cache after write operations.
All functions are async and use the shared Redis client.

Pattern (Dual-Write):
    1. API route writes to Postgres
    2. API route calls upsert_* to update Redis cache with the new value
    3. Reads check Redis first, fall back to Postgres on miss

Pattern (Invalidation):
    1. API route deletes from Postgres
    2. API route calls invalidate_* to clear Redis cache
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .keys import (
    TTL_CONFIG,
    TTL_ORGS,
    config_hash_key,
    config_key,
    form_key,
    forms_hash_key,
    org_key,
    orgs_list_key,
    role_forms_key,
    role_key,
    role_users_key,
    roles_hash_key,
)
from src.core.log_safety import log_safe

from .redis_client import get_shared_redis

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Config Cache (Dual-Write)
# =============================================================================


async def _delete_org_config_hashes(r: Any) -> None:
    """Delete merged org config hashes, which include global fallback values."""
    async for key in r.scan_iter(match="bifrost:org:*:config"):
        await r.delete(key)


async def upsert_config(
    org_id: str | None,
    key: str,
    value: Any,
    config_type: str,
) -> None:
    """
    Upsert a config value to Redis cache after a DB write.

    Secrets are stored ENCRYPTED - decryption happens at read time.

    Args:
        org_id: Organization ID or None for global config
        key: Config key
        value: Config value (encrypted for secrets)
        config_type: Config type ("string", "int", "bool", "json", "secret")
    """
    try:
        r = await get_shared_redis()
        hash_key = config_hash_key(org_id)

        if org_id:
            # Org config hashes are merged views (global fallback overlaid with
            # org values). Updating one field would create a partial hash that
            # hides the remaining global config values on the next read.
            await r.delete(hash_key)
            await r.delete(config_key(org_id, key))
            logger.debug(
                f"Invalidated merged org config cache after upsert: "
                f"org={log_safe(org_id)}, key={log_safe(key)}"
            )
            return

        # Store as JSON with type info for proper parsing at read time
        cache_value = json.dumps({"value": value, "type": config_type})

        # HSET the field in the hash
        await r.hset(hash_key, key, cache_value)  # type: ignore[misc]

        # Ensure TTL is set (HSET doesn't reset TTL, so check if key exists)
        ttl = await r.ttl(hash_key)
        if ttl < 0:  # -1 = no TTL, -2 = key doesn't exist
            await r.expire(hash_key, TTL_CONFIG)

        await _delete_org_config_hashes(r)

        logger.debug(f"Upserted config to cache: org={log_safe(org_id)}, key={log_safe(key)}")
    except Exception as e:
        # Log but don't fail - cache is best-effort
        logger.warning(f"Failed to upsert config cache: {e}")


async def invalidate_config(org_id: str | None, key: str | None = None) -> None:
    """
    Invalidate config cache after a config write operation.

    Args:
        org_id: Organization ID or None for global config
        key: Specific config key to invalidate, or None to invalidate all
    """
    try:
        r = await get_shared_redis()

        # Always invalidate the hash (contains all configs)
        await r.delete(config_hash_key(org_id))

        # Also invalidate specific key if provided
        if key:
            await r.delete(config_key(org_id, key))

        if org_id is None:
            await _delete_org_config_hashes(r)

        logger.debug(f"Invalidated config cache: org={log_safe(org_id)}, key={log_safe(key)}")
    except Exception as e:
        # Log but don't fail - cache invalidation is best-effort
        # TTL will eventually clear stale data
        logger.warning(f"Failed to invalidate config cache: {e}")


async def invalidate_all_config(org_id: str | None) -> None:
    """Invalidate all config cache for an organization."""
    await invalidate_config(org_id, key=None)


# =============================================================================
# Form Invalidation
# =============================================================================


async def invalidate_form(org_id: str | None, form_id: str | None = None) -> None:
    """
    Invalidate form cache after form CRUD operation.

    Args:
        org_id: Organization ID or None for global
        form_id: Specific form to invalidate, or None to invalidate all
    """
    try:
        r = await get_shared_redis()

        # Always invalidate the hash (contains all forms)
        await r.delete(forms_hash_key(org_id))

        # Also invalidate specific form if provided
        if form_id:
            await r.delete(form_key(org_id, form_id))

        # Invalidate user-specific form lists (use pattern delete)
        # This is needed because form-role assignments affect which forms users can see
        pattern = f"bifrost:{_get_scope(org_id)}:user_forms:*"
        async for key in r.scan_iter(pattern):
            await r.delete(key)

        logger.debug(f"Invalidated form cache: org={log_safe(org_id)}, form_id={log_safe(form_id)}")
    except Exception as e:
        logger.warning(f"Failed to invalidate form cache: {e}")


def _get_scope(org_id: str | None) -> str:
    """Get the scope prefix for a key."""
    if org_id and org_id != "GLOBAL":
        return f"org:{org_id}"
    return "global"


async def invalidate_form_assignment(org_id: str | None, form_id: str) -> None:
    """Invalidate form cache after role-form assignment change."""
    await invalidate_form(org_id, form_id)


# =============================================================================
# Role Invalidation
# =============================================================================


async def invalidate_role(org_id: str | None, role_id: str | None = None) -> None:
    """
    Invalidate role cache after role CRUD operation.

    Args:
        org_id: Organization ID or None for global
        role_id: Specific role to invalidate, or None to invalidate all
    """
    try:
        r = await get_shared_redis()

        # Always invalidate the hash (contains all roles)
        await r.delete(roles_hash_key(org_id))

        # Also invalidate specific role if provided
        if role_id:
            await r.delete(role_key(org_id, role_id))
            await r.delete(role_users_key(org_id, role_id))
            await r.delete(role_forms_key(org_id, role_id))

        logger.debug(f"Invalidated role cache: org={log_safe(org_id)}, role_id={log_safe(role_id)}")
    except Exception as e:
        logger.warning(f"Failed to invalidate role cache: {e}")


async def invalidate_role_users(org_id: str | None, role_id: str) -> None:
    """Invalidate role user assignments cache."""
    try:
        r = await get_shared_redis()
        await r.delete(role_users_key(org_id, role_id))
        # Also invalidate user_forms since role assignment affects form access
        pattern = f"bifrost:{_get_scope(org_id)}:user_forms:*"
        async for key in r.scan_iter(pattern):
            await r.delete(key)
        logger.debug(f"Invalidated role users cache: org={log_safe(org_id)}, role_id={log_safe(role_id)}")
    except Exception as e:
        logger.warning(f"Failed to invalidate role users cache: {e}")


async def invalidate_role_forms(org_id: str | None, role_id: str) -> None:
    """Invalidate role form assignments cache."""
    try:
        r = await get_shared_redis()
        await r.delete(role_forms_key(org_id, role_id))
        # Also invalidate user_forms since role-form assignment affects form access
        pattern = f"bifrost:{_get_scope(org_id)}:user_forms:*"
        async for key in r.scan_iter(pattern):
            await r.delete(key)
        logger.debug(f"Invalidated role forms cache: org={log_safe(org_id)}, role_id={log_safe(role_id)}")
    except Exception as e:
        logger.warning(f"Failed to invalidate role forms cache: {e}")


# =============================================================================
# Organization Cache (Dual-Write)
# =============================================================================


async def upsert_org(
    org_id: str,
    name: str,
    domain: str | None,
    is_active: bool,
    is_provider: bool = False,
) -> None:
    """
    Upsert an organization to Redis cache after a DB write.

    Args:
        org_id: Organization UUID as string
        name: Organization name
        domain: Organization domain (optional)
        is_active: Whether the organization is active
        is_provider: Whether the organization is the provider org
    """
    try:
        r = await get_shared_redis()
        redis_key = org_key(org_id)

        cache_value = json.dumps({
            "id": org_id,
            "name": name,
            "domain": domain,
            "is_active": is_active,
            "is_provider": is_provider,
        })

        await r.set(redis_key, cache_value, ex=TTL_ORGS)

        # Also invalidate the orgs list since it may contain this org
        await r.delete(orgs_list_key())

        logger.debug(f"Upserted org to cache: org_id={org_id}")
    except Exception as e:
        # Log but don't fail - cache is best-effort
        logger.warning(f"Failed to upsert org cache: {e}")


async def invalidate_org(org_id: str) -> None:
    """
    Invalidate organization cache after org CRUD operation.

    Args:
        org_id: Organization ID to invalidate
    """
    try:
        r = await get_shared_redis()
        await r.delete(org_key(org_id))
        await r.delete(orgs_list_key())
        logger.debug(f"Invalidated org cache: org_id={log_safe(org_id)}")
    except Exception as e:
        logger.warning(f"Failed to invalidate org cache: {e}")


async def invalidate_all_orgs() -> None:
    """Invalidate all organization cache."""
    try:
        r = await get_shared_redis()
        await r.delete(orgs_list_key())
        # Scan for individual org keys
        pattern = "bifrost:global:orgs:*"
        async for key in r.scan_iter(pattern):
            await r.delete(key)
        logger.debug("Invalidated all org cache")
    except Exception as e:
        logger.warning(f"Failed to invalidate all org cache: {e}")


# =============================================================================
# Execution Cleanup
# =============================================================================


async def cleanup_execution_cache(execution_id: str) -> None:
    """
    Clean up all execution-scoped cache entries.

    Called after execution completes to remove:
    - Pending changes (should already be flushed)
    - Log stream (if using Redis streams)
    - Any other execution-scoped data

    Args:
        execution_id: Execution ID to clean up
    """
    from .keys import execution_logs_stream_key, pending_changes_key

    try:
        r = await get_shared_redis()
        await r.delete(pending_changes_key(execution_id))
        await r.delete(execution_logs_stream_key(execution_id))
        logger.debug(f"Cleaned up execution cache: execution_id={execution_id}")
    except Exception as e:
        logger.warning(f"Failed to cleanup execution cache: {e}")
