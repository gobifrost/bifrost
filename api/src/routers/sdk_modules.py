"""
SDK Module-Fetch Router

Provides authenticated HTTP endpoints that worker child processes use to
fetch workspace module source code.

Also retains the administrative requirements read endpoint for external
callers. Worker requirements setup reads Redis/S3 directly.

This eliminates the need for BIFROST_S3_* credentials in child processes
(Phase 2 of the execution sandbox hardening).  The child authenticates with
its pre-minted engine token; the server performs the Redis→S3 lookup and
returns the content.

Endpoints:
    GET /api/sdk/modules/{path:path}
        Fetch a single module's source (JSON: {content, path, hash}).

"""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from shared.sdk_modules import (
    ModuleSourceError,
    fetch_module_source,
    resolve_module_name,
    module_scope_for_principal,
)
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.core.requirements_cache import get_requirements

router = APIRouter(prefix="/api/sdk", tags=["SDK Internals"])


@router.get("/modules/{path:path}")
async def fetch_module(
    path: str,
    user: CurrentActiveUser,
    db: DbSession,
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
    try:
        from src.services.solutions.storage import SOLUTIONS_ROOT

        solution_id = path.split("/", 2)[1] if path.startswith(f"{SOLUTIONS_ROOT}/") else None
        scope = await module_scope_for_principal(db, user, solution_id=solution_id)
        module = await fetch_module_source(path, scope=scope)
    except ModuleSourceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc

    return JSONResponse(content=module)


@router.get("/modules-resolve")
async def resolve_module(
    user: CurrentActiveUser,
    db: DbSession,
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
    try:
        scope = await module_scope_for_principal(
            db, user, solution_id=solution_id, global_repo_access=global_repo_access,
        )
        result = await resolve_module_name(name, scope=scope)
    except ModuleSourceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from exc

    return JSONResponse(content=result)


@router.get("/requirements")
async def fetch_requirements(
    user: CurrentActiveUser,
    db: DbSession,
) -> JSONResponse:
    """Return the existing requirements.txt HTTP contract for external callers."""
    from src.services.authorization.enforce import authorize_operation
    from src.services.authorization.evaluator import GLOBAL

    await authorize_operation(db, user, "GET /api/sdk/requirements", GLOBAL)
    cached = await get_requirements()
    if cached is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="requirements.txt not found",
        )
    return JSONResponse(content=dict(cached))
