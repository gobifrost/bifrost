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

from src.core.auth import Context, CurrentSuperuser
from src.core.org_filter import resolve_org_filter
from src.repositories.config import ConfigRepository

# Import cache functions
try:
    from src.core.cache import invalidate_config, upsert_config
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False
    invalidate_config = None  # type: ignore
    upsert_config = None  # type: ignore

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
)
async def get_config(
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org."
    ),
) -> list[ConfigResponse]:
    """Get configuration for current scope.

    Superusers can filter by scope or see all configs.
    """
    # Resolve organization filter based on user permissions
    try:
        filter_type, filter_org = resolve_org_filter(ctx.user, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    # Use repository for all filtering
    # Config endpoints are superuser-only, so is_superuser=True (no role checks)
    repo = ConfigRepository(ctx.db, org_id=filter_org, is_superuser=True)
    return await repo.list_configs(filter_type)


@router.post(
    "/api/config",
    response_model=ConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Set configuration value",
    description="Set a configuration value in the current scope",
)
async def set_config(
    request: SetConfigRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> ConfigResponse:
    """Set a configuration key-value pair.

    Superusers can specify organization_id in the request body to target a
    specific organization, or set it to null for global configs.
    """
    # Use organization_id from request body if explicitly provided, else default to current org
    if "organization_id" in (request.model_fields_set or set()):
        target_org_id = request.organization_id
    else:
        target_org_id = ctx.org_id

    # Config endpoints are superuser-only, so is_superuser=True (no role checks)
    repo = ConfigRepository(ctx.db, org_id=target_org_id, is_superuser=True)

    try:
        result = await repo.set_config(request, updated_by=user.email)

        # Upsert to cache after successful write (dual-write pattern)
        if CACHE_AVAILABLE and upsert_config:
            org_id_str = str(target_org_id) if target_org_id else None
            config_type_str = request.type.value if request.type else "string"
            # Note: For secrets, stored_value is already encrypted by the repository
            stored_value = result.value
            await upsert_config(org_id_str, request.key, stored_value, config_type_str)

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
)
async def update_config(
    config_id: UUID,
    request: UpdateConfigRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> ConfigResponse:
    """Update a configuration by ID.

    Unlike POST (which upserts by key within an org scope), this updates the
    specific config row by ID — allowing changes to organization_id (scope).

    For SECRET type configs, omit value or send empty string to keep the
    existing encrypted value.
    """
    # Use is_superuser=True; org scoping not needed since we look up by ID
    repo = ConfigRepository(ctx.db, org_id=ctx.org_id, is_superuser=True)

    update = await repo.update_config_by_id(config_id, request, updated_by=user.email)
    if update is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    result, old_org_id, old_key = update

    if CACHE_AVAILABLE and upsert_config and invalidate_config:
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

    return result


@router.delete(
    "/api/config/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete configuration value",
    description="Delete a configuration value by ID",
)
async def delete_config(
    config_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> None:
    """Delete a configuration by ID."""
    # Config endpoints are superuser-only, so is_superuser=True (no role checks)
    repo = ConfigRepository(ctx.db, org_id=ctx.org_id, is_superuser=True)

    deleted = await repo.delete_config(config_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    # Invalidate cache after successful delete
    if CACHE_AVAILABLE and invalidate_config:
        org_id_str = str(deleted.organization_id) if deleted.organization_id else None
        await invalidate_config(org_id_str, deleted.key)
