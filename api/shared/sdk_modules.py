"""Shared business service for SDK module-source operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/sdk_modules.py``), reached both by
  external/API callers and by worker child processes over HTTP (the
  worker-local engine socket).

Both paths share import-name resolution (metadata cache hydration, scoped
module/package/namespace probes, Solution-before-repo ordering, cache
writes, per-key singleflight) and direct source-fetch path validation plus
Solution-path access rules — so HTTP and worker-local results are identical by
construction.

Parent-side only: imports the Redis-backed module cache and the S3-backed
storages. The child never imports this module.

The service takes an already-authoritative :class:`ModuleSourceScope` and
must never accept ``Request``, a JWT, a raw child frame, or a
child-selected security scope. HTTP authentication, system-user
``engine_execution_id`` proof, signed-claim-over-query override, human
admin diagnostic query parsing, and HTTP exception mapping stay in the
router.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from uuid import UUID
from weakref import WeakValueDictionary

from src.core.log_safety import log_safe
from src.core.module_cache import (
    get_module,
    get_module_resolution_cache,
    module_resolution_cache_key,
    set_module_resolution_cache,
)
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SOLUTIONS_ROOT, SolutionStorage

logger = logging.getLogger(__name__)

_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z_][A-Za-z0-9_]*)*$")
_RESOLUTION_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


@dataclass(frozen=True)
class ModuleSourceScope:
    """Already-authoritative source scope for one engine execution."""

    solution_id: str | None
    global_repo_access: bool


class ModuleSourceError(Exception):
    """SDK module-source failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _resolution_lock(cache_key: str) -> asyncio.Lock:
    """Return a process-local singleflight lock without retaining idle keys."""
    lock = _RESOLUTION_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _RESOLUTION_LOCKS[cache_key] = lock
    return lock


def validate_solution_id(solution_id: str | None) -> None:
    if solution_id is not None:
        try:
            UUID(solution_id)
        except ValueError as exc:
            raise ModuleSourceError(400, "Invalid solution_id") from exc


def validate_module_path(path: str) -> None:
    if ".." in path or path.startswith("/"):
        raise ModuleSourceError(400, "Invalid module path")


def logical_name_to_base_path(name: str) -> str:
    path = name.strip().replace(".", "/").strip("/")
    if not path or not _MODULE_NAME_RE.fullmatch(path):
        raise ModuleSourceError(400, "Invalid module name")
    validate_module_path(path)
    return path


def validate_engine_storage_path(path: str, scope: ModuleSourceScope) -> None:
    """Prevent a cold-cache fetch outside the execution's signed scope."""
    if path.startswith(f"{SOLUTIONS_ROOT}/"):
        expected_prefix = (
            f"{SOLUTIONS_ROOT}/{scope.solution_id}/" if scope.solution_id else None
        )
        if expected_prefix is None or not path.startswith(expected_prefix):
            raise ModuleSourceError(403, "Module path is outside execution scope")
        return

    if scope.solution_id is not None and not scope.global_repo_access:
        raise ModuleSourceError(
            403, "Workspace module access is disabled for this Solution"
        )


async def _prefix_exists(storage_prefix: str) -> bool:
    parts = storage_prefix.split("/", 2)
    if len(parts) == 3 and parts[0] == SOLUTIONS_ROOT:
        return await SolutionStorage(parts[1]).prefix_exists(parts[2])
    return await RepoStorage().prefix_exists(storage_prefix)


async def hydrate_cached_resolution(cached: dict) -> dict | None:
    if cached.get("kind") not in {"module", "package"}:
        return cached

    storage_path = cached.get("storage_path")
    if not isinstance(storage_path, str):
        return None
    module = await get_module(storage_path)
    if module is None:
        return None
    return {
        **cached,
        "content": module["content"],
        "hash": module["hash"],
    }


async def resolve_module_name(
    name: str,
    *,
    scope: ModuleSourceScope,
) -> dict[str, object]:
    """Resolve one logical import name within an authoritative scope.

    The scope must already be authoritative: the router passes the signed
    system-execution scope for engine callers, or a validated
    human-admin query scope for diagnostics. Raw child-selected values
    must never reach this function — construct a ``ModuleSourceScope``
    from trusted input first.
    """
    solution_id = scope.solution_id
    global_repo_access = scope.global_repo_access
    validate_solution_id(solution_id)
    base_path = logical_name_to_base_path(name)
    scope_prefixes = [f"{SOLUTIONS_ROOT}/{solution_id}/"] if solution_id else [""]
    if not solution_id or global_repo_access:
        scope_prefixes.append("")
    scope_prefixes = list(dict.fromkeys(scope_prefixes))

    cache_key = module_resolution_cache_key(
        base_path,
        solution_id=solution_id,
        global_repo_access=global_repo_access,
    )
    # Concurrent cold requests for the same import share one storage probe in
    # this API process. This bounds S3 amplification during a workflow fan-out;
    # the Redis result remains the cross-process/pod cache.
    async with _resolution_lock(cache_key):
        cached = await get_module_resolution_cache(cache_key)
        if cached is not None:
            hydrated = await hydrate_cached_resolution(cached)
            if hydrated is not None:
                return hydrated

        # Resolve one scope completely before considering the next. A Solution
        # namespace is still a hit in that Solution and must not be shadowed by a
        # concrete module from the optional global repository fallback.
        for scope_prefix in scope_prefixes:
            for relative_path, kind in (
                (f"{base_path}.py", "module"),
                (f"{base_path}/__init__.py", "package"),
            ):
                storage_path = f"{scope_prefix}{relative_path}"
                module = await get_module(storage_path)
                if module is not None:
                    module_result: dict[str, object] = {
                        "kind": kind,
                        "path": relative_path,
                        "storage_path": storage_path,
                        "content": module["content"],
                        "hash": module["hash"],
                    }
                    await set_module_resolution_cache(
                        cache_key,
                        {
                            "kind": kind,
                            "path": relative_path,
                            "storage_path": storage_path,
                            "hash": module["hash"],
                        },
                    )
                    return module_result

            # The trailing slash is significant: without it, ``modules/foo``
            # would incorrectly treat ``modules/foobar.py`` as a child.
            storage_prefix = f"{scope_prefix}{base_path}/"
            if await _prefix_exists(storage_prefix):
                namespace_result: dict[str, object] = {
                    "kind": "namespace",
                    "path": base_path,
                }
                await set_module_resolution_cache(cache_key, namespace_result)
                return namespace_result

        logger.debug("SDK module resolver miss: %s", log_safe(name))
        miss_result: dict[str, object] = {"kind": "not_found", "path": base_path}
        await set_module_resolution_cache(cache_key, miss_result)
        return miss_result


async def fetch_module_source(
    path: str,
    *,
    scope: ModuleSourceScope | None = None,
) -> dict:
    """Validate a direct storage path and return its cached source.

    Applies path validation and, when an authoritative engine scope is
    given, the Solution-path access rules. Returns the module dict exactly
    as the module cache would.
    """
    validate_module_path(path)
    if scope is not None:
        validate_engine_storage_path(path, scope)

    module = await get_module(path)
    if module is None:
        raise ModuleSourceError(404, f"Module not found: {path}")
    return dict(module)
