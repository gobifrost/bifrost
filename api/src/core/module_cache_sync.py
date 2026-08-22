"""
Synchronous Redis client for import hook.

Python's import system runs synchronously - we need sync Redis access
for the MetaPathFinder to fetch modules during import.

This module provides synchronous versions of the cache functions
specifically for use in virtual_import.py's MetaPathFinder.

When a cache miss occurs, we fall back via two paths (tried in order):
1. API module-fetch endpoint (GET /api/sdk/modules/<path>) — preferred when
   BIFROST_API_URL is set and a credentials file is present.  This path does
   not require BIFROST_S3_* in the child env (Phase 2 hardening).
2. Direct S3 access via botocore — legacy fallback, only active when
   BIFROST_S3_ACCESS_KEY/SECRET_KEY are set in the environment.

Self-healing: on any successful fetch, the result is re-cached to Redis so
subsequent calls on the same worker hit the fast path.
"""

import hashlib
import json
import logging
import os
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast
from uuid import UUID

import redis

from src.core.module_cache import (
    MODULE_INDEX_KEY,
    MODULE_KEY_PREFIX,
    MODULE_RESOLUTION_KEY_PREFIX,
    CachedModule,
    module_resolution_cache_key,
)

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


@dataclass(frozen=True)
class ModuleResolution:
    """Targeted resolver result for a logical import name."""

    kind: str
    path: str
    content: str | None = None
    hash: str = ""
    storage_path: str | None = None


class ModuleResolutionError(RuntimeError):
    """Raised when targeted module resolution cannot reach the API."""


def set_solution_context(solution_id: UUID | str, global_repo_access: bool) -> None:
    """Activate the solution import root for the current thread/execution."""
    _solution_ctx.value = SolutionContext(
        solution_id=str(solution_id), global_repo_access=bool(global_repo_access)
    )
    _solution_ctx.resolution_cache = {}


def clear_solution_context() -> None:
    """Deactivate the solution import root (restore plain _repo/ behavior)."""
    _solution_ctx.value = None
    _solution_ctx.resolution_cache = {}


def get_solution_context() -> SolutionContext | None:
    """Return the active solution context for this thread, or None."""
    return getattr(_solution_ctx, "value", None)


def _get_resolution_cache() -> dict[tuple[str, str | None, bool], ModuleResolution]:
    cache = getattr(_solution_ctx, "resolution_cache", None)
    if cache is None:
        cache = {}
        _solution_ctx.resolution_cache = cache
    return cache


def _get_http_client() -> Any:
    client = getattr(_solution_ctx, "http_client", None)
    if client is None:
        import httpx

        client = httpx.Client(timeout=10.0)
        _solution_ctx.http_client = client
    return client


def _close_http_client() -> None:
    client = getattr(_solution_ctx, "http_client", None)
    if client is not None:
        # Teardown must not replace the workflow result with a transport-close
        # failure; the process is exiting and owns no reusable client state.
        with suppress(Exception):
            client.close()
        _solution_ctx.http_client = None


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


def _get_engine_credentials() -> tuple[str, str] | None:
    """
    Read the engine bearer token and API URL from the credentials file.

    The file is written by save_credentials() (either from the handed-down
    context_data["engine_token"] path or the legacy authenticate_engine()
    path) and carries both the access token and the API URL.  Returning the
    URL from here means the API module-fetch fallback works even when
    BIFROST_API_URL is not set in the child env (it is not, in either the
    test stack or the k8s worker manifests).

    Returns (api_url, access_token) or None if unavailable.
    """
    try:
        from bifrost.credentials import get_credentials
        creds = get_credentials()
        if creds and creds.get("access_token") and creds.get("api_url"):
            return creds["api_url"].rstrip("/"), creds["access_token"]
    except Exception:
        # Credentials file absent/unreadable in this child — caller falls back
        # to BIFROST_API_URL or treats the cold-cache fetch as unavailable.
        pass
    return None


def _fetch_module_from_api(path: str) -> CachedModule | None:
    """
    Fetch a module via GET /api/sdk/modules/<path> (synchronous, httpx).

    Uses the engine bearer token and API URL from the credentials file,
    falling back to the BIFROST_API_URL env var only if the creds file has
    no URL.  Returns a CachedModule dict on success, None on any error
    (404, auth failure, etc.).

    This is the primary cold-cache fallback when BIFROST_S3_* are absent
    from the child environment (Phase 2 hardening).
    """
    creds = _get_engine_credentials()
    if not creds:
        return None
    creds_url, token = creds

    # Creds-file URL is the source of truth; env var is a secondary source.
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return None

    try:
        url = f"{api_url}/api/sdk/modules/{path}"
        resp = _get_http_client().get(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning(
                f"API module-fetch returned {resp.status_code} for {path}"
            )
            return None

        data: CachedModule = resp.json()
        return data
    except Exception as e:
        logger.warning(f"API module-fetch error for {path}: {e}")
        return None


def _fetch_module_resolution_from_api(name: str) -> ModuleResolution | None:
    """
    Resolve one import name via GET /api/sdk/modules-resolve.

    This is the targeted replacement for fetching the whole module index during
    child import resolution. The API owns Redis→S3 module lookup and bounded
    namespace prefix probing; the child memoizes each result for the active
    execution.
    """
    creds = _get_engine_credentials()
    if not creds:
        return None
    creds_url, token = creds
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return None

    ctx = get_solution_context()
    params: dict[str, object] = {"name": name}
    if ctx is not None:
        params["solution_id"] = ctx.solution_id
        params["global_repo_access"] = ctx.global_repo_access

    try:
        resp = _get_http_client().get(
            f"{api_url}/api/sdk/modules-resolve",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code != 200:
            logger.warning(
                f"API module-resolve returned {resp.status_code} for {name}"
            )
            return None

        data = resp.json()
        kind = data.get("kind")
        if kind not in {"module", "package", "namespace", "not_found"}:
            logger.warning(f"API module-resolve returned invalid kind for {name}")
            return None
        return ModuleResolution(
            kind=kind,
            path=data.get("path") or name.replace(".", "/"),
            content=data.get("content"),
            hash=data.get("hash") or "",
            storage_path=data.get("storage_path"),
        )
    except Exception as e:
        logger.warning(f"API module-resolve error for {name}: {e}")
        return None


def _resolution_redis_key(name: str) -> str:
    ctx = get_solution_context()
    cache_key = module_resolution_cache_key(
        name,
        solution_id=ctx.solution_id if ctx is not None else None,
        global_repo_access=ctx.global_repo_access if ctx is not None else False,
    )
    return f"{MODULE_RESOLUTION_KEY_PREFIX}{cache_key}"


def _module_resolution_from_cached_source(
    *,
    kind: str,
    path: str,
    storage_path: str,
    raw_module: str,
) -> ModuleResolution | None:
    try:
        module = json.loads(raw_module)
        content = module["content"]
        content_hash = module.get("hash") or ""
        if not isinstance(content, str) or not isinstance(content_hash, str):
            return None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return ModuleResolution(
        kind=kind,
        path=path,
        content=content,
        hash=content_hash,
        storage_path=storage_path,
    )


def _get_cached_module_resolution(name: str) -> ModuleResolution | None:
    """Hydrate resolver metadata and source directly from worker-visible Redis."""
    try:
        client = _get_sync_redis()
        raw_resolution = client.get(_resolution_redis_key(name))
        if not raw_resolution:
            return None
        data = json.loads(raw_resolution)
        kind = data.get("kind")
        path = data.get("path") or name.replace(".", "/")
        if kind in {"namespace", "not_found"}:
            return ModuleResolution(kind=kind, path=path)
        if kind not in {"module", "package"}:
            return None
        storage_path = data.get("storage_path")
        if not isinstance(storage_path, str):
            return None
        raw_module = client.get(f"{MODULE_KEY_PREFIX}{storage_path}")
        if not raw_module:
            return None
        return _module_resolution_from_cached_source(
            kind=kind,
            path=path,
            storage_path=storage_path,
            raw_module=raw_module,
        )
    except (redis.RedisError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Cached module resolution unavailable for %s: %s", name, exc)
        return None


def _get_exact_scoped_module(name: str) -> ModuleResolution | None:
    """Resolve a concrete module in the primary scope without an HTTP hop.

    This intentionally does not probe namespaces or a Solution's optional
    workspace fallback. Those cases require the API resolver to preserve the
    rule that a Solution namespace shadows a concrete workspace module.
    """
    base_path = name.replace(".", "/")
    ctx = get_solution_context()
    scope_prefix = f"{SOLUTIONS_ROOT}/{ctx.solution_id}/" if ctx else ""
    try:
        client = _get_sync_redis()
        for relative_path, kind in (
            (f"{base_path}.py", "module"),
            (f"{base_path}/__init__.py", "package"),
        ):
            storage_path = f"{scope_prefix}{relative_path}"
            raw_module = client.get(f"{MODULE_KEY_PREFIX}{storage_path}")
            if not raw_module:
                continue
            return _module_resolution_from_cached_source(
                kind=kind,
                path=relative_path,
                storage_path=storage_path,
                raw_module=raw_module,
            )
    except redis.RedisError as exc:
        logger.debug("Direct module cache unavailable for %s: %s", name, exc)
    return None


def resolve_module_sync(name: str) -> ModuleResolution:
    """Resolve one logical import name, cached for the active execution."""
    top_level = name.split(".", 1)[0]
    if (
        top_level in sys.builtin_module_names
        or top_level in getattr(sys, "stdlib_module_names", set())
    ):
        return ModuleResolution(kind="not_found", path=name.replace(".", "/"))

    ctx = get_solution_context()
    cache_key = (
        name,
        ctx.solution_id if ctx is not None else None,
        ctx.global_repo_access if ctx is not None else False,
    )
    cache = _get_resolution_cache()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    resolution = _get_cached_module_resolution(name)
    if resolution is None:
        resolution = _get_exact_scoped_module(name)
    if resolution is None:
        resolution = _fetch_module_resolution_from_api(name)
    if resolution is None:
        raise ModuleResolutionError(f"Module resolver unavailable for {name}")

    cache[cache_key] = resolution
    return resolution


def _fetch_requirements_from_api() -> tuple[bool, str | None]:
    """
    Fetch requirements.txt via GET /api/sdk/requirements (synchronous).

    Returns ``(authoritative, content)``. A 404 is authoritative absence, while
    connection/auth/server failures return ``(False, None)`` so the caller can
    distinguish them from a workspace that intentionally has no requirements.
    Used as the primary cold-cache fallback in get_requirements_sync() when
    BIFROST_S3_* are absent from the child environment (Phase 2 hardening).
    """
    creds = _get_engine_credentials()
    if not creds:
        return False, None
    creds_url, token = creds
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return False, None

    try:
        resp = _get_http_client().get(
            f"{api_url}/api/sdk/requirements",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 404:
            return True, None
        if resp.status_code != 200:
            logger.warning(f"API requirements-fetch returned {resp.status_code}")
            return False, None

        data = resp.json()
        return True, data.get("content")
    except Exception as e:
        logger.warning(f"API requirements-fetch error: {e}")
        return False, None


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
    2. API endpoint GET /api/sdk/modules/<storage_path>  — preferred cold-cache
       fallback (no S3 env vars required; uses engine token from credentials
       file; the server performs the Redis→S3 lookup)
    3. Direct S3 via botocore — legacy fallback when BIFROST_S3_* are present
    Then the next candidate; None if no candidate resolves.

    On any successful fallback hit the module is re-cached to Redis (under the
    storage-path key) so the next call on the same worker takes the fast path.
    The returned CachedModule keeps the logical (bare) ``path`` so __file__ and
    spec origin stay stable regardless of where the bytes were stored.
    """
    try:
        client = _get_sync_redis()

        for storage_path in _candidate_storage_paths(path):
            key = f"{MODULE_KEY_PREFIX}{storage_path}"
            data = client.get(key)
            if data:
                cached = cast(CachedModule, json.loads(data))
                cached["storage_path"] = storage_path
                return cached

            # --- Cold-cache fallback 1: API endpoint ---
            api_module = _fetch_module_from_api(storage_path)
            if api_module is not None:
                try:
                    client.setex(key, MODULE_CACHE_TTL, json.dumps(api_module))
                    client.sadd(MODULE_INDEX_KEY, storage_path)
                except redis.RedisError as e:
                    logger.warning(f"Failed to re-cache API module to Redis: {e}")
                api_module["storage_path"] = storage_path
                return api_module

            # --- Cold-cache fallback 2: direct S3 (legacy; not needed post-scrub) ---
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
                "storage_path": storage_path,
            }

            # Cache back to Redis under the storage-path key + index.
            try:
                client.setex(key, MODULE_CACHE_TTL, json.dumps(module))
                client.sadd(MODULE_INDEX_KEY, storage_path)
            except redis.RedisError as e:
                logger.warning(f"Failed to cache S3 module to Redis: {e}")

            return module

        logger.debug(f"Module not in cache, API, or S3: {path}")
        return None

    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module {path}: {e}")
        return None


def reset_sync_redis() -> None:
    """Reset the sync Redis client."""
    _get_sync_redis.cache_clear()


def reset_s3_client() -> None:
    """Reset the cached S3 client. Used for testing."""
    global _s3_client, _s3_available
    _s3_client = None
    _s3_available = None
