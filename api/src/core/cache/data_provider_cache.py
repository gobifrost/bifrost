"""Trusted Redis cache for server-authorized data-provider results."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.cache.redis_client import CacheError, get_redis

logger = logging.getLogger(__name__)

TTL_DATA_PROVIDER = 300
_KEY_VERSION = "v2"


def _get_scope(org_id: str | None) -> str:
    return f"org:{org_id}" if org_id and org_id != "GLOBAL" else "global"


def data_provider_cache_key(
    org_id: str | None,
    workflow_id: str,
    authority_fingerprint: str,
    param_hash: str,
) -> str:
    """Build a versioned key scoped to one workflow and authority snapshot.

    Versioning intentionally leaves pre-cutover keys unreadable. The caller
    must obtain the fingerprint from the server-side authority factory.
    """
    return (
        f"bifrost:{_KEY_VERSION}:{_get_scope(org_id)}:dp:{workflow_id}:"
        f"{authority_fingerprint}:{param_hash}"
    )


def compute_param_hash(parameters: dict[str, Any] | None) -> str:
    if not parameters:
        return "empty"
    serialized = json.dumps(parameters, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


async def get_cached_result(
    org_id: str | None,
    workflow_id: str,
    authority_fingerprint: str,
    parameters: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return an unexpired entry for the exact authorized cache partition."""
    cache_key = data_provider_cache_key(
        org_id, workflow_id, authority_fingerprint, compute_param_hash(parameters)
    )
    try:
        async with get_redis() as redis:
            cached_json = await redis.get(cache_key)
            if cached_json is None:
                return None
            cached_entry = json.loads(cached_json)
            expires_at_raw = cached_entry.get("expires_at")
            if expires_at_raw:
                expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) >= expires_at:
                    await redis.delete(cache_key)
                    return None
            return cached_entry
    except CacheError as exc:
        logger.warning("Data-provider cache read failed for workflow %s: %s", workflow_id, exc)
        return None
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Data-provider cache entry was invalid for workflow %s: %s", workflow_id, exc)
        return None


async def cache_result(
    org_id: str | None,
    workflow_id: str,
    authority_fingerprint: str,
    parameters: dict[str, Any] | None,
    result: Any,
    ttl_seconds: int = TTL_DATA_PROVIDER,
) -> datetime:
    """Store one server-authorized partition and return its expiry."""
    cache_key = data_provider_cache_key(
        org_id, workflow_id, authority_fingerprint, compute_param_hash(parameters)
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    entry = {"data": result, "expires_at": expires_at.isoformat()}
    try:
        async with get_redis() as redis:
            await redis.setex(cache_key, ttl_seconds, json.dumps(entry, default=str))
    except CacheError as exc:
        logger.warning("Data-provider cache write failed for workflow %s: %s", workflow_id, exc)
    return expires_at


async def invalidate_data_provider(
    org_id: str | None,
    workflow_id: str,
    parameters: dict[str, Any] | None = None,
) -> None:
    """Invalidate every authority partition for a workflow's cached result.

    Explicit invalidation deliberately scans only v2 keys. Legacy keys are
    never read by this contract and expire under their existing TTL.
    """
    base = f"bifrost:{_KEY_VERSION}:{_get_scope(org_id)}:dp:{workflow_id}:"
    pattern = f"{base}*" if parameters is None else f"{base}*:{compute_param_hash(parameters)}"
    try:
        async with get_redis() as redis:
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await redis.delete(*keys)
                if cursor == 0:
                    break
    except CacheError as exc:
        logger.warning("Data-provider cache invalidation failed for workflow %s: %s", workflow_id, exc)
