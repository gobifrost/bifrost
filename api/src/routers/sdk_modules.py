"""
SDK Module-Fetch Router

Provides authenticated HTTP endpoints that worker child processes use to
fetch workspace module source code and requirements.txt content.

This eliminates the need for BIFROST_S3_* credentials in child processes
(Phase 2 of the execution sandbox hardening).  The child authenticates with
its pre-minted engine token; the server performs the Redis→S3 lookup and
returns the content.

Endpoints:
    GET /api/sdk/modules/{path:path}
        Fetch a single module's source (JSON: {content, path, hash}).

    GET /api/sdk/requirements
        Fetch requirements.txt content (JSON: {content}).
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID
from weakref import WeakValueDictionary

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.core.auth import get_current_superuser
from src.core.constants import SYSTEM_USER_UUID
from src.core.log_safety import log_safe
from src.core.module_cache import (
    get_module,
    get_module_resolution_cache,
    module_resolution_cache_key,
    set_module_resolution_cache,
)
from src.core.requirements_cache import get_requirements
from src.core.principal import UserPrincipal
from src.core.security import decode_token
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SOLUTIONS_ROOT, SolutionStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sdk", tags=["SDK Internals"])
_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z_][A-Za-z0-9_]*)*$")
_RESOLUTION_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


@dataclass(frozen=True)
class _EngineModuleScope:
    solution_id: str | None
    global_repo_access: bool


def _engine_module_scope(
    request: Request,
    user: UserPrincipal,
) -> _EngineModuleScope | None:
    """Read authoritative source scope from a system execution token.

    Human platform admins retain explicit query scope for diagnostics. The
    system execution identity must carry signed per-execution scope claims;
    query parameters are never authoritative for it.
    """
    if user.user_id != SYSTEM_USER_UUID:
        return None

    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Execution-scoped engine token required",
        )
    payload = decode_token(authorization[7:], expected_type="access")
    if payload is None or not payload.get("engine_execution_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Execution-scoped engine token required",
        )

    solution_id = payload.get("engine_solution_id")
    if solution_id is not None:
        _validate_solution_id(solution_id)
    return _EngineModuleScope(
        solution_id=solution_id,
        global_repo_access=bool(payload.get("engine_global_repo_access", False)),
    )


def _validate_engine_storage_path(path: str, scope: _EngineModuleScope) -> None:
    """Prevent a cold-cache fetch outside the execution's signed scope."""
    if path.startswith(f"{SOLUTIONS_ROOT}/"):
        expected_prefix = (
            f"{SOLUTIONS_ROOT}/{scope.solution_id}/" if scope.solution_id else None
        )
        if expected_prefix is None or not path.startswith(expected_prefix):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Module path is outside execution scope",
            )
        return

    if scope.solution_id is not None and not scope.global_repo_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace module access is disabled for this Solution",
        )


def _resolution_lock(cache_key: str) -> asyncio.Lock:
    """Return a process-local singleflight lock without retaining idle keys."""
    lock = _RESOLUTION_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _RESOLUTION_LOCKS[cache_key] = lock
    return lock


def _validate_module_path(path: str) -> None:
    if ".." in path or path.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid module path",
        )


def _logical_name_to_base_path(name: str) -> str:
    path = name.strip().replace(".", "/").strip("/")
    if not path or not _MODULE_NAME_RE.fullmatch(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid module name",
        )
    _validate_module_path(path)
    return path


async def _prefix_exists(storage_prefix: str) -> bool:
    parts = storage_prefix.split("/", 2)
    if len(parts) == 3 and parts[0] == SOLUTIONS_ROOT:
        return await SolutionStorage(parts[1]).prefix_exists(parts[2])
    return await RepoStorage().prefix_exists(storage_prefix)


def _validate_solution_id(solution_id: str | None) -> None:
    if solution_id is not None:
        try:
            UUID(solution_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid solution_id",
            ) from exc


async def _hydrate_cached_resolution(cached: dict) -> dict | None:
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


async def _resolve_module_name(
    name: str,
    *,
    solution_id: str | None = None,
    global_repo_access: bool = False,
) -> dict[str, object]:
    _validate_solution_id(solution_id)
    base_path = _logical_name_to_base_path(name)
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
            hydrated = await _hydrate_cached_resolution(cached)
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


@router.get("/modules/{path:path}")
async def fetch_module(
    path: str,
    request: Request,
    user: Annotated[UserPrincipal, Depends(get_current_superuser)],
) -> JSONResponse:
    """
    Fetch a workspace module by path.

    Returns the module source and hash exactly as the module cache would.
    On miss (module not found in Redis or S3), returns 404.

    This endpoint is called by the child process's virtual import hook when
    Redis is cold (i.e. after a Redis restart that evicted cached modules).
    The child authenticates with its pre-minted engine token.

    Args:
        path: Module path relative to workspace root (e.g. "features/api.py").
    """
    # Validate path — reject any attempt to escape the workspace prefix
    # (Redis key is always "bifrost:module:<path>"; the S3 key is "_repo/<path>")
    _validate_module_path(path)
    engine_scope = _engine_module_scope(request, user)
    if engine_scope is not None:
        _validate_engine_storage_path(path, engine_scope)

    module = await get_module(path)
    if module is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Module not found: {path}",
        )

    return JSONResponse(content=dict(module))


@router.get("/modules-resolve")
async def resolve_module(
    request: Request,
    user: Annotated[UserPrincipal, Depends(get_current_superuser)],
    name: str,
    solution_id: str | None = None,
    global_repo_access: bool = False,
) -> JSONResponse:
    """
    Resolve one logical Python import name or module path.

    Returns one of:
    - {"kind": "module", "content": ..., "hash": ...}
    - {"kind": "package", "content": ..., "hash": ...}
    - {"kind": "namespace"}
    - {"kind": "not_found"}

    The API performs concrete module/package lookup through get_module(), so it
    keeps the existing Redis→S3 self-healing cache behavior. Namespace detection
    uses a bounded prefix existence check instead of listing the whole module
    index.
    """
    engine_scope = _engine_module_scope(request, user)
    if engine_scope is not None:
        solution_id = engine_scope.solution_id
        global_repo_access = engine_scope.global_repo_access

    return JSONResponse(
        content=await _resolve_module_name(
            name,
            solution_id=solution_id,
            global_repo_access=global_repo_access,
        )
    )


@router.get("/requirements")
async def fetch_requirements(
    _user: Annotated[object, Depends(get_current_superuser)],
) -> JSONResponse:
    """
    Fetch requirements.txt content.

    Returns JSON: {"content": "...", "hash": "..."} or 404 if none exists.
    Used by the child's install_requirements() when Redis is cold.
    """
    cached = await get_requirements()
    if cached is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="requirements.txt not found",
        )

    return JSONResponse(content=dict(cached))
