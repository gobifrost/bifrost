"""
Synchronous Redis client for import hook.

Python's import system runs synchronously - we need sync Redis access
for the MetaPathFinder to fetch modules during import.

This module provides synchronous versions of the cache functions
specifically for use in virtual_import.py's MetaPathFinder.

When a cache miss occurs, we fall back to S3 to fetch the module
and re-cache it in Redis. This provides self-healing behavior when:
- Redis cache entries expire (24hr TTL)
- Redis restarts or evicts keys
- Cache warming at startup was incomplete
"""

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

import redis

from src.core.module_cache import MODULE_INDEX_KEY, MODULE_KEY_PREFIX, CachedModule

logger = logging.getLogger(__name__)

# TTL for cached modules (24 hours)
MODULE_CACHE_TTL = 86400

REPO_PREFIX = "_repo/"
SOLUTIONS_ROOT = "_solutions"


# ── Per-execution Solution import root ───────────────────────────────────────
# When a solution-managed workflow runs, module resolution must be rooted at
# _solutions/{solution_id}/ — for the entry workflow's own code AND its
# `from modules.x import y` imports — falling back to the bare _repo/ root ONLY
# when the install's global_repo_access flag is on (success-criteria §3.5).
#
# The context is thread-local: each forked worker runs a single execution on one
# thread, and the import system runs synchronously on that thread, so a
# thread-local correctly scopes the root to exactly one execution with no
# cross-execution bleed. No active context == unchanged _repo/ behavior.
_solution_ctx = threading.local()


@dataclass(frozen=True)
class SolutionContext:
    """Active per-execution solution import root."""

    solution_id: str
    global_repo_access: bool


def set_solution_context(solution_id: UUID | str, global_repo_access: bool) -> None:
    """Activate the solution import root for the current thread/execution."""
    _solution_ctx.value = SolutionContext(
        solution_id=str(solution_id), global_repo_access=bool(global_repo_access)
    )


def clear_solution_context() -> None:
    """Deactivate the solution import root (restore plain _repo/ behavior)."""
    _solution_ctx.value = None


def get_solution_context() -> SolutionContext | None:
    """Return the active solution context for this thread, or None."""
    return getattr(_solution_ctx, "value", None)


def _candidate_storage_paths(path: str) -> list[str]:
    """Ordered storage paths to try for a relative module path.

    - No active solution → just the bare path (resolved under _repo/ downstream).
    - Solution active → the solution-rooted path FIRST. When global_repo_access
      is on, the bare path follows as a fallback; when off, there is no fallback
      (a _repo/ import must NOT silently resolve — criterion 4).

    The returned paths are storage paths: a bare path is later read from the
    _repo/ prefix; a path already under ``_solutions/`` is read verbatim.
    """
    ctx = get_solution_context()
    if ctx is None:
        return [path]
    rooted = f"{SOLUTIONS_ROOT}/{ctx.solution_id}/{path.lstrip('/')}"
    if ctx.global_repo_access:
        return [rooted, path]
    return [rooted]


def candidate_index_prefixes(base_path: str) -> list[str]:
    """Storage-path prefixes to scan the module index with, for namespace-package
    (PEP 420) detection of ``base_path`` (e.g. "modules").

    Mirrors :func:`_candidate_storage_paths`: solution-rooted prefix first, with
    the bare prefix only when global_repo_access is on; bare prefix only when no
    solution is active. The finder tests ``index_entry.startswith(prefix)``.
    """
    base = base_path.rstrip("/")
    ctx = get_solution_context()
    if ctx is None:
        return [f"{base}/"]
    rooted = f"{SOLUTIONS_ROOT}/{ctx.solution_id}/{base}/"
    if ctx.global_repo_access:
        return [rooted, f"{base}/"]
    return [rooted]

# Cached S3 client — reused across calls to avoid repeated setup
_s3_client: Any = None
_s3_available: bool | None = None


@lru_cache(maxsize=1)
def _get_sync_redis() -> Any:
    """
    Get synchronous Redis client.

    Uses lru_cache to reuse connection across imports.
    """
    return redis.Redis.from_url(
        os.environ.get("BIFROST_REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _get_s3_client() -> Any:
    """
    Get or create a sync S3 client using botocore (always available via aiobotocore).
    """
    global _s3_client, _s3_available

    if _s3_available is False:
        return None

    if _s3_client is not None:
        return _s3_client

    endpoint_url = os.environ.get("BIFROST_S3_ENDPOINT_URL")
    access_key = os.environ.get("BIFROST_S3_ACCESS_KEY")
    secret_key = os.environ.get("BIFROST_S3_SECRET_KEY")
    region = os.environ.get("BIFROST_S3_REGION", "us-east-1")

    if not all([access_key, secret_key]):
        logger.debug("S3 not configured, skipping S3 fallback")
        _s3_available = False
        return None

    try:
        import botocore.session  # type: ignore[import-untyped]

        session = botocore.session.get_session()
        client = session.create_client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        _s3_client = client
        _s3_available = True
        return client
    except Exception:
        _s3_available = False
        logger.debug("botocore not available, S3 fallback disabled")
        return None


def _storage_path_to_s3_key(storage_path: str) -> str:
    """Map a storage path to its S3 key.

    A bare relative path lives under the _repo/ prefix; a path already rooted at
    ``_solutions/`` is used verbatim (it already carries its full prefix).
    """
    if storage_path.startswith(f"{SOLUTIONS_ROOT}/"):
        return storage_path
    return f"{REPO_PREFIX}{storage_path}"


def _get_s3_module(storage_path: str) -> bytes | None:
    """
    Fetch a module from S3 by storage path (synchronous).

    Bare paths resolve under _repo/; ``_solutions/{id}/...`` paths are used
    verbatim. Uses botocore sync client since this runs in worker subprocesses.
    Returns raw bytes or None if not found.
    """
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return None

    client = _get_s3_client()
    if client is None:
        return None

    try:
        key = _storage_path_to_s3_key(storage_path)
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    except Exception as e:
        # Check for S3 NoSuchKey specifically
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = resp.get("Error", {}).get("Code", "")
            if code == "NoSuchKey":
                logger.debug(f"Module not found in S3: {storage_path}")
                return None
        logger.warning(f"S3 fallback error for {storage_path}: {e}")
        return None


def get_module_sync(path: str) -> CachedModule | None:
    """
    Fetch a single module from cache (synchronous).

    Called by VirtualModuleFinder.find_spec() during import resolution AND by
    the worker to load the entry workflow's own code.

    When a Solution context is active (set_solution_context), candidate storage
    paths are tried in order — solution-rooted first, then bare _repo/ only when
    global_repo_access is on. With no context, behavior is unchanged: the bare
    path resolves under _repo/.

    Per candidate, the lookup order is:
    1. Redis cache (fast path)
    2. S3 (fallback, re-caches to Redis)
    Then the next candidate; None if no candidate resolves.

    The returned CachedModule keeps the logical (bare) ``path`` so __file__ and
    spec origin stay stable regardless of where the bytes were stored.
    """
    try:
        client = _get_sync_redis()

        for storage_path in _candidate_storage_paths(path):
            key = f"{MODULE_KEY_PREFIX}{storage_path}"
            data = client.get(key)
            if data:
                return json.loads(data)

            # Redis miss — try S3 fallback for this candidate
            s3_content = _get_s3_module(storage_path)
            if s3_content is None:
                continue
            try:
                content_str = s3_content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(f"Could not decode S3 module as UTF-8: {storage_path}")
                continue

            content_hash = hashlib.sha256(s3_content).hexdigest()
            module: CachedModule = {
                "content": content_str,
                "path": path,
                "hash": content_hash,
            }

            # Cache back to Redis under the storage-path key + index.
            try:
                client.setex(key, MODULE_CACHE_TTL, json.dumps(module))
                client.sadd(MODULE_INDEX_KEY, storage_path)
            except redis.RedisError as e:
                logger.warning(f"Failed to cache S3 module to Redis: {e}")

            return module

        logger.debug(f"Module not in cache or S3: {path}")
        return None

    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module {path}: {e}")
        return None


def _list_s3_modules() -> set[str]:
    """
    List all Python module paths in S3 _repo/ (synchronous).

    Used as a fallback when the Redis module index is empty, which can happen
    after Redis restarts or cache eviction. Returns paths relative to _repo/
    (e.g. "features/spotify_journal/services/spotify_api.py").
    """
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return set()

    client = _get_s3_client()
    if client is None:
        return set()

    paths: set[str] = set()
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=REPO_PREFIX):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                if key.endswith(".py"):
                    # Strip the _repo/ prefix to get the relative path
                    paths.add(key[len(REPO_PREFIX):])
    except Exception as e:
        logger.warning(f"S3 list error when rebuilding module index: {e}")

    return paths


def get_module_index_sync() -> set[str]:
    """
    Get all cached module paths (synchronous).

    When the Redis index is empty (cold cache after restart or eviction),
    falls back to listing S3 and repopulates Redis so subsequent calls are fast.
    """
    try:
        client = _get_sync_redis()
        paths = client.smembers(MODULE_INDEX_KEY)
        if paths:
            return {p if isinstance(p, str) else p.decode() for p in paths}

        # Redis index is empty — could be cold cache. Try S3.
        logger.debug("Module index empty in Redis, falling back to S3 listing")
        s3_paths = _list_s3_modules()
        if s3_paths:
            # Repopulate Redis index so subsequent calls are fast
            try:
                client.sadd(MODULE_INDEX_KEY, *s3_paths)
                client.expire(MODULE_INDEX_KEY, MODULE_CACHE_TTL)
            except redis.RedisError as e:
                logger.warning(f"Failed to repopulate module index in Redis: {e}")
            return s3_paths

        return set()
    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module index: {e}")
        return set()


def solution_has_submodules(base_path: str) -> bool:
    """True if the active solution has any object under ``{base_path}/`` in S3.

    Namespace-package (PEP 420) detection for solution code can't rely on the
    Redis module index alone: a freshly-deployed module is only indexed once
    it's first loaded, but it can't load until its parent package resolves as a
    namespace — a chicken-and-egg. So when a solution is active we check S3
    directly under ``_solutions/{id}/{base_path}/`` (one key is enough).

    Returns False when no solution is active (the _repo/ index path already
    handles that case) or S3 is unavailable.
    """
    ctx = get_solution_context()
    if ctx is None:
        return False
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return False
    client = _get_s3_client()
    if client is None:
        return False
    prefix = f"{SOLUTIONS_ROOT}/{ctx.solution_id}/{base_path.rstrip('/')}/"
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        return resp.get("KeyCount", 0) > 0 or bool(resp.get("Contents"))
    except Exception as e:
        logger.debug(f"S3 submodule check failed for {prefix}: {e}")
        return False


def reset_sync_redis() -> None:
    """Reset the sync Redis client."""
    _get_sync_redis.cache_clear()


def reset_s3_client() -> None:
    """Reset the cached S3 client. Used for testing."""
    global _s3_client, _s3_available
    _s3_client = None
    _s3_available = None
