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

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from shared.sdk_modules import (
    ModuleSourceError,
    ModuleSourceScope,
    fetch_module_source,
    resolve_module_name,
    validate_solution_id,
)
from src.core.auth import get_current_superuser
from src.core.constants import SYSTEM_USER_UUID
from src.core.requirements_cache import get_requirements
from src.core.principal import UserPrincipal
from src.core.security import decode_token

router = APIRouter(prefix="/api/sdk", tags=["SDK Internals"])


def _engine_module_scope(
    request: Request,
    user: UserPrincipal,
) -> ModuleSourceScope | None:
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
        try:
            validate_solution_id(solution_id)
        except ModuleSourceError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc
    return ModuleSourceScope(
        solution_id=solution_id,
        global_repo_access=bool(payload.get("engine_global_repo_access", False)),
    )


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
    engine_scope = _engine_module_scope(request, user)
    try:
        module = await fetch_module_source(path, scope=engine_scope)
    except ModuleSourceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc

    return JSONResponse(content=module)


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
        scope = engine_scope
    else:
        try:
            validate_solution_id(solution_id)
        except ModuleSourceError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc
        scope = ModuleSourceScope(
            solution_id=solution_id,
            global_repo_access=global_repo_access,
        )

    try:
        result = await resolve_module_name(name, scope=scope)
    except ModuleSourceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc

    return JSONResponse(content=result)


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
