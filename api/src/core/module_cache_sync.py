"""Synchronous source loading for the virtual import hook.

Python imports run synchronously. Parent processes may use the supported
Redis/S3 cache and network API paths. A forked execution child is explicitly
marked after fork; every one of its module resolution and source requests goes
through the scoped SDK transport before any cache or storage access. The SDK
uses the worker-local Unix socket when installed and ordinary network HTTP
otherwise. Children never read or write Redis or S3.
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

from src.core.module_cache_contract import (
    CachedModule,
    MODULE_INDEX_KEY,
    MODULE_KEY_PREFIX,
    MODULE_RESOLUTION_KEY_PREFIX,
    MODULE_RESOLUTION_READ_SCRIPT,
    module_resolution_generation_key,
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
_sync_redis_client: Any = None
# Set only by TemplateProcess after fork. It is process-local by virtue of
# fork's copy-on-write memory and never comes from workflow-controlled input.
_execution_child = False


def mark_execution_child() -> None:
    """Mark this post-fork process as an execution child."""
    global _execution_child
    _execution_child = True


@lru_cache(maxsize=1)
def _get_sync_redis() -> Any:
    """
    Get synchronous Redis client.

    Uses lru_cache to reuse connection across imports.
    """
    global _sync_redis_client
    _sync_redis_client = redis.Redis.from_url(
        os.environ.get("BIFROST_REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )
    return _sync_redis_client


def _get_engine_credentials() -> tuple[str, str] | None:
    """
    Read the engine bearer token and API URL from the credentials file.

    The file is written by the handed-down ``context_data["engine_token"]``
    path and carries both the access token and the API URL. Parent synchronous
    callers use it only when no trusted execution-child socket is installed.

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


def _get_engine_client() -> Any | None:
    """Return the scoped SDK client for a trusted execution child.

    A worker-installed private Unix socket remains an execution-child marker
    for existing callers. TemplateProcess also marks every post-fork execution
    child, including a thin runner that uses the SDK's network transport.
    Everywhere else (API processes, plain workers, tests without injection)
    this is None and parent cache behavior applies.

    The import is lazy so this module stays importable before the SDK package
    loads. A SDK package that is present but broken raises instead of
    silently switching an execution child to Redis or S3.
    """
    try:
        from bifrost.client import get_client, get_engine_socket_path
    except ModuleNotFoundError as e:
        if not _execution_child and e.name in {"bifrost", "bifrost.client"}:
            return None
        raise
    if not _execution_child and get_engine_socket_path() is None:
        return None
    return get_client()


def _fetch_module_from_api(path: str) -> CachedModule | None:
    """
    Fetch a module via GET /api/sdk/modules/<path> (synchronous, httpx).

    Uses the engine bearer token and API URL from the credentials file,
    falling back to the BIFROST_API_URL env var only if the creds file has
    no URL.  Returns a CachedModule dict on success, None on any error
    (404, auth failure, etc.).

    This is the primary parent cold-cache fallback when direct S3 settings are
    unavailable.
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


def _resolve_via_engine_transport(client: Any, name: str) -> ModuleResolution:
    """Resolve one import name through the scoped SDK transport.

    Calls the original ``GET /api/sdk/modules-resolve`` route through the
    synchronous ``engine_request_sync`` entry point. That client selects the
    worker socket when one is installed, otherwise its ordinary network HTTP
    transport. The active Solution scope rides the query and the route's
    signed per-execution claims remain authoritative for engine callers.

    Raises :class:`ModuleResolutionError` on transport failure, a non-200
    status, or an invalid payload. An execution-child attempt never falls back
    to Redis or S3.
    """
    ctx = get_solution_context()
    params: dict[str, object] = {"name": name}
    if ctx is not None:
        params["solution_id"] = ctx.solution_id
        params["global_repo_access"] = ctx.global_repo_access

    try:
        response = client.engine_request_sync(
            "GET", "/api/sdk/modules-resolve", params=params
        )
    except Exception as e:
        raise ModuleResolutionError(
            f"Engine-local module resolver failed for {name}: {e}"
        ) from e
    if response.status_code != 200:
        raise ModuleResolutionError(
            f"Engine-local module resolver returned {response.status_code} "
            f"for {name}"
        )

    data = response.json()
    kind = data.get("kind")
    if kind not in {"module", "package", "namespace", "not_found"}:
        raise ModuleResolutionError(
            f"Engine-local module resolver returned invalid kind for {name}"
        )
    content = data.get("content")
    if content is not None and not isinstance(content, str):
        raise ModuleResolutionError(
            f"Engine-local module resolver returned invalid content for {name}"
        )
    module_hash = data.get("hash") or ""
    if not isinstance(module_hash, str):
        raise ModuleResolutionError(
            f"Engine-local module resolver returned invalid hash for {name}"
        )
    storage_path = data.get("storage_path")
    if storage_path is not None and not isinstance(storage_path, str):
        raise ModuleResolutionError(
            f"Engine-local module resolver returned invalid storage path for {name}"
        )
    return ModuleResolution(
        kind=kind,
        path=data.get("path") or name.replace(".", "/"),
        content=content,
        hash=module_hash,
        storage_path=storage_path,
    )


def _fetch_via_engine_transport(client: Any, storage_path: str) -> CachedModule | None:
    """Fetch one candidate's source through the scoped SDK transport.

    Calls the original ``GET /api/sdk/modules/{path}`` route through the
    synchronous ``engine_request_sync`` entry point. Returns None on a route
    404 so the caller advances to the next candidate. Any other failure raises
    :class:`ModuleResolutionError`; an execution child never falls back to
    Redis or S3.
    """
    try:
        response = client.engine_request_sync(
            "GET", f"/api/sdk/modules/{storage_path}"
        )
    except Exception as e:
        raise ModuleResolutionError(
            f"Engine-local module fetch failed for {storage_path}: {e}"
        ) from e
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ModuleResolutionError(
            f"Engine-local module fetch returned {response.status_code} "
            f"for {storage_path}"
        )

    module = response.json()
    content = module.get("content")
    if not isinstance(content, str):
        raise ModuleResolutionError(
            f"Engine-local module fetch returned invalid content for {storage_path}"
        )
    return cast(CachedModule, dict(module))


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
        resolution_key = _resolution_redis_key(name)
        raw_resolution = client.eval(
            MODULE_RESOLUTION_READ_SCRIPT, 2,
            module_resolution_generation_key(resolution_key), resolution_key,
        )
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

    # A worker socket or post-fork marker selects the scoped SDK transport
    # before any Redis helper so an inherited or ambient cache cannot become
    # an authority or credential path for customer code.
    engine_client = _get_engine_client()
    if engine_client is not None:
        resolution = _resolve_via_engine_transport(engine_client, name)
    else:
        resolution = _get_cached_module_resolution(name)
        if resolution is None:
            resolution = _get_exact_scoped_module(name)
        if resolution is None:
            resolution = _fetch_module_resolution_from_api(name)
    if resolution is None:
        raise ModuleResolutionError(f"Module resolver unavailable for {name}")

    cache[cache_key] = resolution
    return resolution


def _get_s3_client() -> Any:
    """Get or create the parent-only synchronous CRT S3 client."""
    global _s3_client, _s3_available

    if _s3_available is False:
        return None

    if _s3_client is not None:
        return _s3_client

    endpoint_url = os.environ.get("BIFROST_S3_ENDPOINT_URL")
    access_key = os.environ.get("BIFROST_S3_ACCESS_KEY")
    secret_key = os.environ.get("BIFROST_S3_SECRET_KEY")
    region = os.environ.get("BIFROST_S3_REGION", "us-east-1")

    try:
        from src.core.s3_transport import SyncS3Transport

        client = SyncS3Transport(
            endpoint_url=endpoint_url,
            access_key_id=access_key,
            secret_access_key=secret_key,
            region=region,
        )
        _s3_client = client
        _s3_available = True
        return client
    except Exception:
        _s3_available = False
        logger.debug("S3 transport unavailable; S3 fallback disabled")
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
    verbatim. This is available only to parent synchronous callers. Returns
    raw bytes or None if not found.
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

    A forked execution child is identified by its injected worker socket or
    post-fork marker. It uses ``GET /api/sdk/modules/<storage_path>`` through
    the scoped SDK transport for every candidate, including entries that would
    be warm in Redis. It never uses Redis or S3, and a 404 advances to the
    next candidate.

    Parent callers retain the existing Redis → network API → S3 lookup and
    re-cache successful fallback reads. The returned CachedModule keeps the
    logical (bare) ``path`` so __file__ and spec origin stay stable regardless
    of where the bytes were stored.
    """
    # The worker socket or post-fork marker selects the scoped SDK transport
    # before touching any Redis cache or parent-only fallback.
    engine_client = _get_engine_client()
    if engine_client is not None:
        for storage_path in _candidate_storage_paths(path):
            engine_module = _fetch_via_engine_transport(engine_client, storage_path)
            if engine_module is None:
                continue
            engine_module["storage_path"] = storage_path
            return engine_module
        return None

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
    """Close and reset the synchronous parent Redis cache client."""
    global _sync_redis_client
    if _sync_redis_client is not None:
        with suppress(Exception):
            _sync_redis_client.close()
        _sync_redis_client = None
    _get_sync_redis.cache_clear()


def reset_s3_client() -> None:
    """Reset the cached S3 client. Used for testing."""
    global _s3_client, _s3_available
    if _s3_client is not None:
        with suppress(Exception):
            _s3_client.close()
    _s3_client = None
    _s3_available = None
