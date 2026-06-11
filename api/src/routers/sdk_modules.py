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

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from src.core.auth import get_current_superuser
from src.core.module_cache import get_all_module_paths, get_module
from src.core.requirements_cache import get_requirements

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sdk", tags=["SDK Internals"])


@router.get("/modules/{path:path}")
async def fetch_module(
    path: str,
    _user: Annotated[object, Depends(get_current_superuser)],
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
    if ".." in path or path.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid module path",
        )

    module = await get_module(path)
    if module is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Module not found: {path}",
        )

    return JSONResponse(content=dict(module))


@router.get("/modules-index")
async def fetch_module_index(
    _user: Annotated[object, Depends(get_current_superuser)],
) -> JSONResponse:
    """
    Fetch the set of all known workspace module paths.

    Returns JSON: {"paths": ["features/api.py", ...]}.
    Used by the child's VirtualModuleFinder to rebuild the module index when
    the Redis SET is cold.
    """
    paths = await get_all_module_paths()
    return JSONResponse(content={"paths": sorted(paths)})


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
