"""Read and update the base role automatically given to new accounts."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.base_role_defaults import (
    BaseRoleDefaultsResponse,
    BaseRoleDefaultsUpdate,
)
from src.services.authorization.enforce import (
    GLOBAL,
    authorize_operation,
    org_target,
    require_permission,
)
from src.services.operation_catalog import operation_route


router = APIRouter(prefix="/api/base-role-defaults", tags=["Base Role Defaults"])


def _target(organization_id: UUID | None):
    return org_target(organization_id) if organization_id is not None else GLOBAL


@router.get("", response_model=BaseRoleDefaultsResponse, **operation_route("users.defaults.get"))
async def get_base_role_defaults(
    db: DbSession,
    user: CurrentActiveUser,
    organization_id: UUID | None = Query(None),
) -> BaseRoleDefaultsResponse:
    """Get the setting for Global or an organization and its effective role."""
    caller = await authorize_operation(
        db,
        user,
        "users.defaults.get",
        _target(organization_id),
    )
    require_permission(caller, "organizations.read" if organization_id is not None else "settings.read", organization_id)
    from shared.base_role_defaults import BaseRoleDefaultError, get_default_base_role

    try:
        configured, effective = await get_default_base_role(db, organization_id=organization_id)
    except BaseRoleDefaultError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return BaseRoleDefaultsResponse(
        default_base_role_id=configured,
        effective_base_role_id=effective.id,
        effective_base_role_name=effective.name,
        source=effective.source,
    )


@router.put("", response_model=BaseRoleDefaultsResponse, **operation_route("users.defaults.update"))
async def update_base_role_defaults(
    request: BaseRoleDefaultsUpdate,
    db: DbSession,
    user: CurrentActiveUser,
    organization_id: UUID | None = Query(None),
) -> BaseRoleDefaultsResponse:
    """Set the default at Global or override it for one organization."""
    target = _target(organization_id)
    caller = await authorize_operation(
        db,
        user,
        "users.defaults.update",
        target,
    )
    # This setting changes the access a future account starts with. Settings
    # permission alone must not let a delegated caller broaden provisioning.
    require_permission(caller, "organizations.readwrite" if organization_id is not None else "settings.readwrite", organization_id)
    from shared.base_role_defaults import BaseRoleDefaultError, set_default_base_role

    try:
        configured, effective = await set_default_base_role(
            db,
            organization_id=organization_id,
            default_base_role_id=request.default_base_role_id,
            updated_by=user.email,
        )
    except BaseRoleDefaultError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    await db.commit()
    return BaseRoleDefaultsResponse(
        default_base_role_id=configured,
        effective_base_role_id=effective.id,
        effective_base_role_name=effective.name,
        source=effective.source,
    )
