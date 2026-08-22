"""
Async Redis client for module caching.

Used by API services and background jobs that have async context.
Workers read modules from this cache during virtual imports.

Key patterns:
- bifrost:module:{path} - JSON: {content, path, hash}
- bifrost:module:index - SET of all module paths
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Awaitable, NotRequired, TypedDict, cast

from src.core.log_safety import log_safe
from src.core.redis_client import get_redis_client
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SOLUTIONS_ROOT, SolutionStorage

logger = logging.getLogger(__name__)

MODULE_KEY_PREFIX = "bifrost:module:"
MODULE_INDEX_KEY = "bifrost:module:index"
MODULE_RESOLUTION_KEY_PREFIX = "bifrost:module:resolution:"
MODULE_RESOLUTION_TTL = 86400
MODULE_RESOLUTION_NEGATIVE_TTL = 30


def module_resolution_cache_key(
    name: str,
    *,
    solution_id: str | None,
    global_repo_access: bool,
) -> str:
    """Build the shared Redis key suffix for one scoped import name."""
    dotted_name = name.strip().replace("/", ".").strip(".")
    return f"{solution_id or '-'}:{int(global_repo_access)}:{dotted_name}"


class CachedModule(TypedDict):
    """Schema for cached module data."""

    content: str
    path: str
    hash: str
    storage_path: NotRequired[str]


async def _read_module_from_storage(path: str) -> bytes:
    parts = path.split("/", 2)
    if len(parts) == 3 and parts[0] == SOLUTIONS_ROOT:
        solution_id, relative_path = parts[1], parts[2]
        return await SolutionStorage(solution_id).read(relative_path)

    return await RepoStorage().read(path)


async def get_module(path: str) -> CachedModule | None:
    """
    Fetch a module from cache, falling back to S3.

    Lookup order:
    1. Redis cache (fast path)
    2. S3 _repo/ (fallback, re-caches to Redis)
    3. None (module not found)

    Args:
        path: Module path relative to workspace (e.g., "shared/halopsa.py")

    Returns:
        CachedModule dict if found, None otherwise
    """
    redis = get_redis_client()
    key = f"{MODULE_KEY_PREFIX}{path}"
    data = await redis.get(key)
    if data:
        return json.loads(data)

    # Redis miss — try S3 fallback
    try:
        content_bytes = await _read_module_from_storage(path)
    except Exception:
        logger.debug(f"Module not in cache or S3: {log_safe(path)}")
        return None

    try:
        content_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(f"Could not decode {log_safe(path)} as UTF-8, skipping")
        return None

    content_hash = hashlib.sha256(content_bytes).hexdigest()
    module: CachedModule = {
        "content": content_str,
        "path": path,
        "hash": content_hash,
    }

    # Re-cache to Redis (self-healing)
    try:
        await redis.setex(key, 86400, json.dumps(module))
        redis_conn = await redis._get_redis()
        await cast(Awaitable[int], redis_conn.sadd(MODULE_INDEX_KEY, path))
    except Exception as e:
        logger.warning(f"Failed to re-cache S3 module to Redis: {e}")

    return module


async def get_module_resolution_cache(cache_key: str) -> dict | None:
    """Read a targeted module resolver result from Redis."""
    redis = get_redis_client()
    data = await redis.get(f"{MODULE_RESOLUTION_KEY_PREFIX}{cache_key}")
    return json.loads(data) if data else None


async def set_module_resolution_cache(cache_key: str, result: dict) -> None:
    """Cache a targeted module resolver result."""
    ttl = (
        MODULE_RESOLUTION_NEGATIVE_TTL
        if result.get("kind") == "not_found"
        else MODULE_RESOLUTION_TTL
    )
    redis = get_redis_client()
    await redis.setex(
        f"{MODULE_RESOLUTION_KEY_PREFIX}{cache_key}",
        ttl,
        json.dumps(result),
    )


async def _scan_keys(redis_conn, pattern: str) -> list[str]:
    """Collect Redis keys from both real and lightweight test clients."""
    iterator = redis_conn.scan_iter(pattern)
    if hasattr(iterator, "__await__"):
        iterator = await iterator
    if not hasattr(iterator, "__aiter__"):
        return []
    return [key async for key in iterator]


def _resolver_names_for_path(path: str) -> set[str]:
    """Logical names and ancestor namespace names affected by a module path."""
    parts = path.split("/", 2)
    if len(parts) == 3 and parts[0] == SOLUTIONS_ROOT:
        path = parts[2]

    if path.endswith("/__init__.py"):
        logical = path.removesuffix("/__init__.py")
    elif path.endswith(".py"):
        logical = path.removesuffix(".py")
    else:
        logical = path

    dotted = logical.replace("/", ".").strip(".")
    if not dotted:
        return set()
    name_parts = dotted.split(".")
    return {".".join(name_parts[:i]) for i in range(1, len(name_parts) + 1)}


async def invalidate_module_resolution_cache(path: str) -> None:
    """Invalidate resolver metadata for a changed module and its namespaces."""
    names = _resolver_names_for_path(path)
    if not names:
        return

    redis = get_redis_client()
    redis_conn = await redis._get_redis()
    keys: list[str] = []
    for name in sorted(names):
        keys.extend(
            await _scan_keys(redis_conn, f"{MODULE_RESOLUTION_KEY_PREFIX}*:{name}")
        )
    if keys:
        await cast(Awaitable[int], redis_conn.delete(*dict.fromkeys(keys)))


async def set_module(path: str, content: str, content_hash: str) -> None:
    """
    Cache a module and add to index.

    Called by file_ops when a module is written.

    Args:
        path: Module path relative to workspace
        content: Python source code
        content_hash: SHA-256 hash of content (for change detection)
    """
    redis = get_redis_client()
    key = f"{MODULE_KEY_PREFIX}{path}"

    cached = CachedModule(content=content, path=path, hash=content_hash)
    await redis.setex(key, 86400, json.dumps(cached))  # 24hr TTL

    # Add to index set
    redis_conn = await redis._get_redis()
    await cast(Awaitable[int], redis_conn.sadd(MODULE_INDEX_KEY, path))
    await invalidate_module_resolution_cache(path)

    logger.debug(f"Cached module: {log_safe(path)}")


async def invalidate_module(path: str) -> None:
    """
    Remove module from cache and index.

    Called by file_ops when a module is deleted.

    Args:
        path: Module path to invalidate
    """
    redis = get_redis_client()
    key = f"{MODULE_KEY_PREFIX}{path}"

    await redis.delete(key)
    await invalidate_module_resolution_cache(path)

    # Remove from index set
    redis_conn = await redis._get_redis()
    await cast(Awaitable[int], redis_conn.srem(MODULE_INDEX_KEY, path))

    logger.debug(f"Invalidated module cache: {log_safe(path)}")


async def refresh_modules_from_directory(work_dir: Path) -> int:
    """Re-populate Redis module cache from local .py files after sync.

    Called after S3 sync to ensure Redis cache matches the just-synced
    content, preventing stale reads by the editor and workflow engine.

    Args:
        work_dir: Local directory containing the synced files.

    Returns:
        Number of modules refreshed.
    """
    count = 0
    for py_file in work_dir.rglob("*.py"):
        rel_path = str(py_file.relative_to(work_dir))
        content_bytes = py_file.read_bytes()
        try:
            content_str = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        await set_module(rel_path, content_str, content_hash)
        count += 1
    logger.info(f"Refreshed {count} module(s) in Redis cache from {work_dir}")
    return count


async def clear_module_cache() -> int:
    """
    Clear all cached modules.

    Used for testing and cache invalidation.

    Returns:
        Number of modules cleared
    """
    redis = get_redis_client()
    redis_conn = await redis._get_redis()

    # Get all module paths from index
    paths = await cast(Awaitable[set[str]], redis_conn.smembers(MODULE_INDEX_KEY))
    count = len(paths)

    if paths:
        # Delete all module keys
        keys = [f"{MODULE_KEY_PREFIX}{p if isinstance(p, str) else p.decode()}" for p in paths]
        await cast(Awaitable[int], redis_conn.delete(*keys))

    # Clear the index
    await cast(Awaitable[int], redis_conn.delete(MODULE_INDEX_KEY))

    # Resolver metadata can otherwise outlive the source cache and turn a
    # deliberate cold-cache reset into a partially warm lookup.
    resolution_keys = await _scan_keys(redis_conn, f"{MODULE_RESOLUTION_KEY_PREFIX}*")
    if resolution_keys:
        await cast(Awaitable[int], redis_conn.delete(*resolution_keys))

    logger.info(f"Cleared {count} modules from cache")
    return count
