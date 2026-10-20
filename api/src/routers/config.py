"""
Config Router

Manage global and organization-specific configuration key-value pairs.

Uses OrgScopedRepository for standardized org scoping.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

# Import existing Pydantic models for API compatibility
from src.models import (
    ConfigResponse,
    SetConfigRequest,
    UpdateConfigRequest,
)

from src.core.auth import Context, CurrentActiveUser
from src.core.org_filter import OrgFilterType
from src.repositories.config import ConfigRepository

from src.core.cache import invalidate_config, upsert_config
from src.services.operation_catalog import operation_route
from src.services.authorization.enforce import (
    load_caller,
    operation_reach,
    org_target,
    require_operation,
)
from src.services.authorization.evaluator import GLOBAL

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Configuration"])


# =============================================================================
# Config Endpoints
# =============================================================================


@router.get(
    "/api/config",
    response_model=list[ConfigResponse],
    summary="Get configuration values",
    description="Get configuration values for current scope (includes global configs)",
**operation_route("configs.list"))
async def get_config(
    ctx: Context,
    user: CurrentActiveUser,
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org."
    ),
) -> list[ConfigResponse]:
    """List only configurations within the original user's permitted reach."""
    caller = await load_caller(ctx.db, user)
    repo = ConfigRepository(ctx.db, org_id=ctx.org_id, is_superuser=False)
    if scope == "global":
        require_operation(caller, "configs.list", GLOBAL)
        return await repo.list_configs(OrgFilterType.GLOBAL_ONLY)
    if scope:
        try:
            target_org = UUID(scope)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "scope must be 'global' or an organization UUID") from None
        require_operation(caller, "configs.list", org_target(target_org))
        repo = ConfigRepository(ctx.db, org_id=target_org, is_superuser=False)
        return await repo.list_configs(OrgFilterType.ORG_PLUS_GLOBAL)

    # An omitted scope is a list across the caller's permission reach.  The
    # shared Global tier is included for every selected organization; it is
    # not an implicit request to target Global.
    reach = operation_reach(caller, "configs.list")
    return await repo.list_configs_in_reach(reach, include_global_fallback=True)


@router.get(
    "/api/config/{config_id}",
    response_model=ConfigResponse,
    summary="Get a configuration value by ID",
    description="Get a single configuration value by its UUID",
**operation_route("configs.get"))
async def get_config_by_id(
    config_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> ConfigResponse:
    """Get one configuration by UUID.

    No org cascade — an ID lookup resolves directly, matching
    ``update_config`` and ``delete_config`` (also id-only). The caller is
    authorized against the stored organization before the response is sent.
    Secret values are masked as
    ``[SECRET]``, matching the list endpoint.
    """
    repo = ConfigRepository(ctx.db, org_id=ctx.org_id)

    config = await repo.get_config_by_id(config_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )
    caller = await load_caller(ctx.db, user)
    require_operation(caller, "configs.get", org_target(UUID(config.org_id) if config.org_id else None))
    return config


@router.post(
    "/api/config",
    response_model=ConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Set configuration value",
    description="Set a configuration value in the current scope",
**operation_route("configs.create"))
async def set_config(
    request: SetConfigRequest,
    ctx: Context,
    user: CurrentActiveUser,
) -> ConfigResponse:
    """Set a configuration key-value pair.

    The request may specify organization_id to target a permitted organization,
    or null for Global configs.
    """
    # Use organization_id from request body if explicitly provided, else default to current org
    if "organization_id" in (request.model_fields_set or set()):
        target_org_id = request.organization_id
    else:
        target_org_id = ctx.org_id

    caller = await load_caller(ctx.db, user)
    require_operation(caller, "configs.create", org_target(target_org_id))
    repo = ConfigRepository(ctx.db, org_id=target_org_id)

    try:
        write = await repo.set_config(request, updated_by=user.email)

        # Upsert to cache after successful write (dual-write pattern).
        # The cache holds the stored value (ciphertext for secrets); the
        # response carries the masked one.
        org_id_str = str(target_org_id) if target_org_id else None
        config_type_str = request.type.value if request.type else "string"
        await upsert_config(org_id_str, request.key, write.stored_value, config_type_str)

        return write.response
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
**operation_route("configs.update"))
async def update_config(
    config_id: UUID,
    request: UpdateConfigRequest,
    ctx: Context,
    user: CurrentActiveUser,
) -> ConfigResponse:
    """Update a configuration by ID.

    Unlike POST (which upserts by key within an org scope), this updates the
    specific config row by ID — allowing changes to organization_id (scope).

    For SECRET type configs, omit value or send empty string to keep the
    existing encrypted value.
    """
    repo = ConfigRepository(ctx.db, org_id=ctx.org_id)
    existing = await repo.get_config_by_id(config_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Configuration not found")
    caller = await load_caller(ctx.db, user)
    old_org_id = UUID(existing.org_id) if existing.org_id else None
    require_operation(caller, "configs.update", org_target(old_org_id))
    if "organization_id" in request.model_fields_set:
        require_operation(caller, "configs.update", org_target(request.organization_id))

    update = await repo.update_config_by_id(config_id, request, updated_by=user.email)
    if update is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    write, old_org_id, old_key = update
    result = write.response

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
    await upsert_config(new_org_id_str, result.key, write.stored_value, config_type_str)

    return result


@router.delete(
    "/api/config/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete configuration value",
    description="Delete a configuration value by ID",
**operation_route("configs.delete"))
async def delete_config(
    config_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> None:
    """Delete a configuration by ID."""
    repo = ConfigRepository(ctx.db, org_id=ctx.org_id)
    existing = await repo.get_config_by_id(config_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configuration not found")
    caller = await load_caller(ctx.db, user)
    require_operation(
        caller,
        "configs.delete",
        org_target(UUID(existing.org_id) if existing.org_id else None),
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
