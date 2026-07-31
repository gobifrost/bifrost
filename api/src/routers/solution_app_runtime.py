"""Explicit, least-privilege API surface for generated Solution apps.

This router is mounted only by ``src.app_host``.  It deliberately re-exports
an exact allowlist of the SDK's data-plane routes instead of mounting any
control-plane router wholesale.  The original endpoint functions are reused
so table policies, file policies, workflow validation, and response contracts
stay on the canonical implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authorization_scopes import (
    EXECUTIONS_READ_SCOPE,
    FILE_CONTENT_READ_SCOPE,
    FILE_CONTENT_WRITE_SCOPE,
    TABLE_DOCUMENTS_READ_SCOPE,
    TABLE_DOCUMENTS_WRITE_SCOPE,
    WORKFLOWS_EXECUTE_SCOPE,
)
from shared.role_cache import get_user_roles
from src.core.app_actor import (
    CurrentSolutionApp,
    get_solution_app_principal,
)
from src.core.auth import ExecutionContext
from src.core.database import get_db
from src.core.principal import UserPrincipal
from src.models.orm.applications import Application
from src.models.orm.executions import Execution
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.routers import executions, files, tables, workflows

Db = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(
    prefix="/_bifrost",
    tags=["solution-app-runtime"],
    dependencies=[Depends(get_solution_app_principal)],
)

# This is the complete HTTP surface used by client/src/lib/app-sdk.  Adding an
# SDK endpoint requires an explicit review and an entry here.
_DYNAMIC_FILE_SCOPE = "__dynamic_file_scope__"

# Every admitted SDK route declares its action scope alongside the allowlist.
# This keeps "is the route exposed?" and "what action authority does it need?"
# as one reviewable decision.
_SDK_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/workflows/execute"): WORKFLOWS_EXECUTE_SCOPE,
    ("GET", "/api/executions/{execution_id}"): EXECUTIONS_READ_SCOPE,
    ("GET", "/api/executions/{execution_id}/result"): EXECUTIONS_READ_SCOPE,
    ("GET", "/api/executions/{execution_id}/logs"): EXECUTIONS_READ_SCOPE,
    ("GET", "/api/tables/{table_id}/documents/{doc_id}"): TABLE_DOCUMENTS_READ_SCOPE,
    ("POST", "/api/tables/{table_id}/documents"): TABLE_DOCUMENTS_WRITE_SCOPE,
    ("POST", "/api/tables/{table_id}/documents/upsert"): TABLE_DOCUMENTS_WRITE_SCOPE,
    ("POST", "/api/tables/{table_id}/documents/batch"): TABLE_DOCUMENTS_WRITE_SCOPE,
    ("PATCH", "/api/tables/{table_id}/documents/{doc_id}"): TABLE_DOCUMENTS_WRITE_SCOPE,
    ("DELETE", "/api/tables/{table_id}/documents/{doc_id}"): TABLE_DOCUMENTS_WRITE_SCOPE,
    ("POST", "/api/tables/{table_id}/documents/batch-delete"): TABLE_DOCUMENTS_WRITE_SCOPE,
    ("POST", "/api/tables/{table_id}/documents/query"): TABLE_DOCUMENTS_READ_SCOPE,
    ("GET", "/api/tables/{table_id}/documents/count"): TABLE_DOCUMENTS_READ_SCOPE,
    ("POST", "/api/files/read"): FILE_CONTENT_READ_SCOPE,
    ("POST", "/api/files/write"): FILE_CONTENT_WRITE_SCOPE,
    ("POST", "/api/files/delete"): FILE_CONTENT_WRITE_SCOPE,
    ("POST", "/api/files/list"): FILE_CONTENT_READ_SCOPE,
    ("POST", "/api/files/exists"): FILE_CONTENT_READ_SCOPE,
    ("POST", "/api/files/signed-url"): _DYNAMIC_FILE_SCOPE,
    ("POST", "/api/files/signed-urls"): _DYNAMIC_FILE_SCOPE,
    ("POST", "/api/files/complete-upload"): FILE_CONTENT_WRITE_SCOPE,
}


def _source_routes() -> Iterable[APIRoute]:
    for source in (workflows.router, executions.router, tables.router, files.router):
        for route in source.routes:
            if isinstance(route, APIRoute):
                yield route


def require_solution_app_scope(required_scope: str):
    """Build a route dependency for one exact app-actor action scope."""

    async def dependency(principal: CurrentSolutionApp) -> None:
        if not principal.has_scope(required_scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The {required_scope} scope is required",
            )

    return dependency


async def enforce_signed_url_scopes(
    request: Request,
    principal: CurrentSolutionApp,
) -> None:
    """Authorize each signed-URL operation by the method in its request body."""

    try:
        body = await request.json()
        items = body.get("requests") if request.url.path.endswith("/signed-urls") else [body]
        methods = {str(item.get("method", "PUT")).upper() for item in items}
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid signed URL request",
        ) from None

    required = set()
    if "GET" in methods:
        required.add(FILE_CONTENT_READ_SCOPE)
    if "PUT" in methods:
        required.add(FILE_CONTENT_WRITE_SCOPE)
    if not methods or methods - {"GET", "PUT"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Signed URL method must be GET or PUT",
        )
    missing = sorted(scope for scope in required if not principal.has_scope(scope))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"The {', '.join(missing)} scope is required",
        )


def _mount_sdk_routes() -> None:
    mounted: set[tuple[str, str]] = set()
    for route in _source_routes():
        selected_methods = {
            method
            for method in route.methods or set()
            if (method, route.path) in _SDK_ROUTES
        }
        if not selected_methods:
            continue
        dependencies = []
        route_scopes = {
            _SDK_ROUTES[(method, route.path)] for method in selected_methods
        }
        if _DYNAMIC_FILE_SCOPE in route_scopes:
            dependencies.append(Depends(enforce_signed_url_scopes))
            route_scopes.remove(_DYNAMIC_FILE_SCOPE)
        dependencies.extend(
            Depends(require_solution_app_scope(scope))
            for scope in sorted(route_scopes)
        )
        if route.path.startswith("/api/executions/"):
            dependencies.append(Depends(enforce_actor_execution_binding))
        router.add_api_route(
            route.path,
            route.endpoint,
            methods=selected_methods,
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            operation_id=route.operation_id,
            response_class=route.response_class,
            name=route.name,
            callbacks=route.callbacks,
            openapi_extra=route.openapi_extra,
            dependencies=dependencies or None,
        )
        mounted.update((method, route.path) for method in selected_methods)

    missing = set(_SDK_ROUTES) - mounted
    if missing:
        formatted = ", ".join(f"{method} {path}" for method, path in sorted(missing))
        raise RuntimeError(f"Solution app SDK routes are missing: {formatted}")


async def get_solution_app_user(
    principal: CurrentSolutionApp,
    db: Db,
) -> UserPrincipal:
    """Rehydrate a normal-looking principal from the actor's live owner row.

    This dependency is used only as an override inside the isolated app-host
    process.  It never accepts caller-supplied scope and never grants the
    owner's platform-admin authority to generated code.
    """
    user = await db.get(User, principal.actor_user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The Solution app owner is no longer active",
        )

    role_ids, role_names = await get_user_roles(user.id, db)
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        name=user.name or user.email,
        organization_id=principal.organization_id,
        is_active=True,
        is_superuser=False,
        is_verified=user.is_verified,
        is_external=False,
        is_provider_org=False,
        roles=role_names,
        scopes=sorted(principal.scopes),
        role_ids=role_ids,
        role_names=role_names,
        jti=principal.jti,
        app_id=str(principal.app_id),
    )


async def enforce_actor_execution_binding(
    request: Request,
    principal: CurrentSolutionApp,
    db: Db,
) -> None:
    """Allow execution reads only for this exact app-host session."""
    raw_execution_id = request.path_params.get("execution_id")
    try:
        from uuid import UUID

        execution_id = UUID(str(raw_execution_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None

    execution = await db.get(Execution, execution_id)
    if (
        execution is None
        or execution.executed_by != principal.actor_user_id
        or not execution.execution_context
        or execution.execution_context.get("actor_jti") != principal.jti
        or execution.workflow_id is None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    workflow_solution_id = (
        await db.execute(
            select(Workflow.solution_id).where(
                Workflow.id == execution.workflow_id,
            )
        )
    ).scalar_one_or_none()
    if workflow_solution_id != principal.solution_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


_mount_sdk_routes()


async def get_solution_app_execution_context(
    principal: CurrentSolutionApp,
    user: Annotated[UserPrincipal, Depends(get_solution_app_user)],
    db: Db,
) -> ExecutionContext:
    return ExecutionContext(
        user=user,
        org_id=principal.organization_id,
        db=db,
        app_id=str(principal.app_id),
        solution_id=str(principal.solution_id),
    )


@router.get("/api/auth/me")
async def get_actor_user_info(
    user: Annotated[UserPrincipal, Depends(get_solution_app_user)],
) -> dict[str, Any]:
    """Return only the header fields generated apps need."""
    return {
        "id": str(user.user_id),
        "email": user.email,
        "name": user.name,
        "is_active": True,
        "is_superuser": False,
        "is_verified": user.is_verified,
        "organization_id": (
            str(user.organization_id) if user.organization_id is not None else None
        ),
        "roles": user.roles,
    }


@router.get("/api/applications/{app_id}/logo")
async def get_actor_application_logo(
    app_id: str,
    principal: CurrentSolutionApp,
    db: Db,
) -> Response:
    """Serve chrome for this exact app; sibling apps remain invisible."""
    if app_id != str(principal.app_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    application = (
        await db.execute(
            select(Application).where(
                Application.id == principal.app_id,
                Application.solution_id == principal.solution_id,
                Application.organization_id == principal.organization_id,
            )
        )
    ).scalar_one_or_none()
    if application is None or not application.logo_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not set",
        )
    return Response(
        content=application.logo_data,
        media_type=application.logo_content_type or "application/octet-stream",
    )
