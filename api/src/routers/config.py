"""
Config Router

Manage global and organization-specific configuration key-value pairs.

Uses OrgScopedRepository for standardized org scoping.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

# Import existing Pydantic models for API compatibility
from src.models import (
    ConfigResponse,
    SetConfigRequest,
    UpdateConfigRequest,
)

from src.core.auth import Context
from src.core.org_filter import OrgFilterType
from src.models.orm.config import Config as ConfigORM
from src.models.orm.organizations import Organization
from src.repositories.config import ConfigRepository
from src.services.audit import emit_audit
from src.services.authorization import (
    AuthorizationBoundaryKind,
    CurrentAuthorizationContext,
)
from src.services.operation_catalog import operation_route

from src.core.cache import invalidate_config, upsert_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Configuration"])


def _parse_scope(scope: str | None) -> UUID | None:
    if scope is None or scope == "global":
        return None
    try:
        return UUID(scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="scope must be 'global' or an organization UUID",
        ) from exc


def _selected_config_target(
    authorization: CurrentAuthorizationContext,
) -> UUID | None:
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization before changing Configuration",
        )
    return (
        boundary.organization_id
        if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
        else None
    )


def _require_scope_matches_selected_boundary(
    authorization: CurrentAuthorizationContext,
    requested_organization_id: UUID | None,
) -> None:
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        return
    selected = (
        boundary.organization_id
        if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
        else None
    )
    if requested_organization_id != selected:
        expected = "global" if requested_organization_id is None else str(
            requested_organization_id
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "scope does not match the selected authorization boundary; "
                f"select {expected} first"
            ),
        )


# =============================================================================
# Config Endpoints
# =============================================================================


@router.get(
    "/api/config",
    response_model=list[ConfigResponse],
    summary="Get configuration values",
    description="Get configuration values for current scope (includes global configs)",
    **operation_route("configs.list"),
)
async def get_config(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    scope: str | None = Query(
        None,
        description="Optional scope within the selected authorization boundary."
    ),
) -> list[ConfigResponse]:
    """List Config definitions admitted by the selected boundary."""
    authorization.require_operation("configs.list")
    requested = _parse_scope(scope)
    boundary = authorization.selected_boundary
    if (
        scope is None
        and boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
    ):
        requested = boundary.organization_id
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        customer_ids = set(
            (
                await ctx.db.execute(
                    select(Organization.id).where(
                        Organization.is_provider.is_(False)
                    )
                )
            )
            .scalars()
            .all()
        )
        if scope is not None:
            if requested is None or requested not in customer_ids:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Managed organizations only contains customer organizations",
                )
            repo = ConfigRepository(ctx.db, org_id=requested, bypass_resource_admission=True)
            return await repo.list_configs(OrgFilterType.ORG_ONLY)
        repo = ConfigRepository(ctx.db, org_id=None, bypass_resource_admission=True)
        rows = await repo.list_configs(OrgFilterType.ALL)
        return [
            row
            for row in rows
            if row.org_id is not None and UUID(row.org_id) in customer_ids
        ]

    _require_scope_matches_selected_boundary(authorization, requested)
    target = _selected_config_target(authorization)
    filter_type = (
        OrgFilterType.GLOBAL_ONLY if target is None else OrgFilterType.ORG_ONLY
    )
    return await ConfigRepository(
        ctx.db, org_id=target, bypass_resource_admission=True
    ).list_configs(filter_type)


@router.get(
    "/api/config/{config_id}",
    response_model=ConfigResponse,
    summary="Get a configuration value by ID",
    description="Get a single configuration value by its UUID",
    **operation_route("configs.get"),
)
async def get_config_by_id(
    config_id: UUID,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> ConfigResponse:
    """Get one configuration by UUID.

    Scoped to the caller's org plus global rows. Secret values are masked as
    ``[SECRET]``, matching the list endpoint.
    """
    authorization.require_operation("configs.get")
    row = await ctx.db.get(ConfigORM, config_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )
    authorization.require_resource_boundary(row.organization_id)
    repo = ConfigRepository(
        ctx.db, org_id=row.organization_id, bypass_resource_admission=True
    )

    config = await repo.get_config_by_id(config_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )
    return config


@router.post(
    "/api/config",
    response_model=ConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Set configuration value",
    description="Set a configuration value in the current scope",
    **operation_route("configs.create"),
)
async def set_config(
    request: SetConfigRequest,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> ConfigResponse:
    """Set a configuration key-value pair.

    The selected exact Organization or Platform boundary is the write target.
    An explicit request organization must agree with that boundary.
    """
    authorization.require_operation("configs.create")
    target_org_id = _selected_config_target(authorization)
    if "organization_id" in (request.model_fields_set or set()):
        _require_scope_matches_selected_boundary(
            authorization, request.organization_id
        )
    authorization.require_resource_boundary(target_org_id)

    repo = ConfigRepository(ctx.db, org_id=target_org_id, bypass_resource_admission=True)

    try:
        result = await repo.set_config(
            request, updated_by=authorization.requester.email
        )

        # Upsert to cache after successful write (dual-write pattern)
        org_id_str = str(target_org_id) if target_org_id else None
        config_type_str = request.type.value if request.type else "string"
        # Note: For secrets, stored_value is already encrypted by the repository
        stored_value = result.value
        await upsert_config(org_id_str, request.key, stored_value, config_type_str)

        await emit_audit(
            ctx.db,
            "config.create",
            resource_type="config",
            resource_id=result.id,
            details={
                "key": result.key,
                "organization_id": org_id_str,
            },
        )

        return result
    except Exception as e:
        logger.error(f"Error setting config: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set configuration",
        )


@router.put(
    "/api/config/{config_id}",
    response_model=ConfigResponse,
    summary="Update configuration value by ID",
    description="Update an existing configuration value, including its organization scope",
    **operation_route("configs.update"),
)
async def update_config(
    config_id: UUID,
    request: UpdateConfigRequest,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> ConfigResponse:
    """Update a configuration by ID.

    Unlike POST (which upserts by key within an org scope), this updates the
    specific config row by ID — allowing changes to organization_id (scope).

    For SECRET type configs, omit value or send empty string to keep the
    existing encrypted value.
    """
    authorization.require_operation("configs.update")
    existing = await ctx.db.get(ConfigORM, config_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )
    authorization.require_resource_boundary(existing.organization_id)
    destination = (
        request.organization_id
        if "organization_id" in (request.model_fields_set or set())
        else existing.organization_id
    )
    authorization.require_resource_boundary(destination)
    repo = ConfigRepository(
        ctx.db, org_id=existing.organization_id, bypass_resource_admission=True
    )

    update = await repo.update_config_by_id(
        config_id, request, updated_by=authorization.requester.email
    )
    if update is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    result, old_org_id, old_key = update

    new_org_id_str = str(result.org_id) if result.org_id else None
    old_org_id_str = str(old_org_id) if old_org_id else None

    # If the row's identity changed (rename or org-move), the old
    # cache entry would otherwise survive until TTL with stale —
    # possibly secret — data. Drop the old (old_org, old_key)
    # entry before writing the new one. ``invalidate_config`` also
    # bumps CONFIG_GLOBAL_VERSION_KEY when ``old_org`` was global,
    # so org-merged caches re-fetch.
    if old_org_id_str != new_org_id_str or old_key != result.key:
        await invalidate_config(old_org_id_str, old_key)

    # If this update crosses the global↔org boundary, bump the
    # global version so org caches that merged the old global
    # value re-fetch even though the new write is org-scoped.
    if (old_org_id is None) != (result.org_id is None):
        from src.core.cache import get_shared_redis
        from src.core.cache.keys import CONFIG_GLOBAL_VERSION_KEY
        try:
            r = await get_shared_redis()
            await r.incr(CONFIG_GLOBAL_VERSION_KEY)
        except Exception as e:
            logger.warning(f"Failed to bump global config version on transition: {e}")

    config_type_str = result.type.value if result.type else "string"
    stored_value = result.value
    await upsert_config(new_org_id_str, result.key, stored_value, config_type_str)

    await emit_audit(
        ctx.db,
        "config.update",
        resource_type="config",
        resource_id=result.id,
        details={
            "key": result.key,
            "organization_id": new_org_id_str,
        },
    )

    return result


@router.delete(
    "/api/config/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete configuration value",
    description="Delete a configuration value by ID",
    **operation_route("configs.delete"),
)
async def delete_config(
    config_id: UUID,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> None:
    """Delete a configuration by ID."""
    authorization.require_operation("configs.delete")
    existing = await ctx.db.get(ConfigORM, config_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )
    authorization.require_resource_boundary(existing.organization_id)
    repo = ConfigRepository(
        ctx.db, org_id=existing.organization_id, bypass_resource_admission=True
    )

    deleted = await repo.delete_config(config_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    # Invalidate cache after successful delete
    org_id_str = str(deleted.organization_id) if deleted.organization_id else None
    await invalidate_config(org_id_str, deleted.key)
    await emit_audit(
        ctx.db,
        "config.delete",
        resource_type="config",
        resource_id=deleted.id,
        details={"key": deleted.key, "organization_id": org_id_str},
    )
