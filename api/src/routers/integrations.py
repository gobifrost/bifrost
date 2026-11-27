"""
Integrations Router

Manages integrations and their mappings to organizations with external entities.
Integrations combine OAuth providers, data providers, and configuration schemas.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import Any

from sqlalchemy import func, select

from src.core.auth import Context, CurrentActiveUser
from src.core.log_safety import log_safe, log_safe_exception

from src.models import (
    ConfigSchemaItem,
    EntityIdSourceUpdateRequest,
    Integration,
    IntegrationCreate,
    IntegrationDetailResponse,
    IntegrationListResponse,
    IntegrationMapping,
    IntegrationMappingBatchRequest,
    IntegrationMappingBatchResponse,
    IntegrationMappingCreate,
    IntegrationMappingListResponse,
    IntegrationMappingResponse,
    IntegrationMappingUpdate,
    IntegrationResponse,
    IntegrationTestRequest,
    IntegrationTestResponse,
    IntegrationUpdate,
    MappingAuthorizeRequest,
    MappingAuthorizeResponse,
    OAuthConfigSummary,
)
from src.models.orm import Config as ConfigModel
from src.models.orm import OAuthToken
from src.repositories.integrations import IntegrationsRepository
from src.services.config_values import ConfigValueError
from src.services.oauth_provider import (
    append_query_params,
    get_url_resolution_defaults,
    resolve_url_template,
)
from src.services.audit import emit_audit
from src.services.oauth_state import encode_state, remember_nonce
from shared.logo_processing import (
    LogoProcessingError,
    is_logo_thumbnail_version,
    process_logo,
)
from src.services.operation_catalog import operation_route
from src.services.authorization.enforce import (
    Caller,
    load_caller,
    operation_reach,
    org_target,
    require_operation,
)
from src.services.authorization.evaluator import GLOBAL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations", tags=["Integrations"])


async def _catalog_reader(ctx: Context, user: CurrentActiveUser, operation: str) -> Caller:
    """Authorize shared integration metadata and return its permitted reach."""
    caller = await load_caller(ctx.db, user)
    operation_reach(caller, operation)
    return caller


async def _global_definition_writer(
    ctx: Context, user: CurrentActiveUser, operation: str
) -> Caller:
    """Authorize a mutation of the Global integration definition."""
    caller = await load_caller(ctx.db, user)
    require_operation(caller, operation, GLOBAL)
    return caller


async def _mapping_for_operation(
    repo: IntegrationsRepository,
    ctx: Context,
    user: CurrentActiveUser,
    integration_id: UUID,
    mapping_id: UUID,
    operation: str,
) -> IntegrationMapping:
    """Load then authorize a mapping at its persisted organization."""
    mapping = await repo.get_mapping_by_id(integration_id, mapping_id)
    if mapping is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mapping not found")
    caller = await load_caller(ctx.db, user)
    require_operation(caller, operation, org_target(mapping.organization_id))
    return mapping


def _logo_data_url(data: bytes | None, content_type: str | None) -> str | None:
    """Encode a binary logo as a data URL, or None if no logo is set."""
    if not data:
        return None
    import base64

    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type or 'application/octet-stream'};base64,{encoded}"


def _integration_logo_url(integration: Integration) -> str | None:
    """Return a logo URL without hiding legacy images during thumbnail backfill."""
    if is_logo_thumbnail_version(integration.logo_thumbnail_version):
        return f"/api/integrations/{integration.id}/logo?v={integration.logo_thumbnail_version}"
    if integration.logo_content_type:
        return f"/api/integrations/{integration.id}/logo"
    return None


def _integration_to_response(
    integration: Integration,
    summary: tuple[int, int, dict[str, int]] | None = None,
    include_inline_logo: bool = False,
) -> IntegrationResponse:
    mapping_count, connected_mapping_count, status_counts = summary or (0, 0, {})
    response = IntegrationResponse.model_validate(integration)
    response.logo_url = _integration_logo_url(integration)
    response.logo = (
        _logo_data_url(
            integration.logo_thumbnail_data or integration.logo_data,
            integration.logo_thumbnail_content_type or integration.logo_content_type,
        )
        if include_inline_logo
        else None
    )
    response.logo_version = (
        integration.logo_thumbnail_version
        if is_logo_thumbnail_version(integration.logo_thumbnail_version)
        else None
    )
    response.mapping_count = mapping_count
    response.connected_count = connected_mapping_count
    response.needs_reconnection_count = status_counts.get("failed", 0)
    response.connection_status_counts = status_counts
    response.oauth_flow_type = (
        integration.oauth_provider.oauth_flow_type
        if integration.oauth_provider else None
    )
    return response


# =============================================================================
# Response Models
# =============================================================================


class OAuthConfigResponse(BaseModel):
    """Response model for OAuth provider configuration."""

    id: UUID = Field(..., description="OAuth provider ID")
    provider_name: str = Field(..., description="Provider name")
    display_name: str | None = Field(default=None, description="Display name")
    oauth_flow_type: str = Field(..., description="OAuth flow type (authorization_code or client_credentials)")
    client_id: str = Field(..., description="OAuth client ID")
    authorization_url: str | None = Field(
        default=None,
        description="OAuth authorization endpoint URL (only for authorization_code flow)"
    )
    token_url: str | None = Field(..., description="OAuth token endpoint URL")
    scopes: list[str] = Field(default_factory=list, description="OAuth scopes")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class OAuthAuthorizeResponse(BaseModel):
    """Response model for OAuth authorization URL."""

    authorization_url: str = Field(..., description="URL to redirect user for authorization")
    state: str = Field(..., description="State parameter for CSRF protection")
    message: str = Field(default="Redirect user to authorization_url to complete OAuth flow")


# =============================================================================
# HTTP Endpoints - Integration CRUD
# =============================================================================


@router.post(
    "",
    response_model=IntegrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create integration",
    description="Create a new integration (Platform admin only)",
**operation_route("integrations.create"))
async def create_integration(
    request: IntegrationCreate,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationResponse:
    """Create a new integration."""
    await _global_definition_writer(ctx, user, operation="integrations.create")
    repo = IntegrationsRepository(ctx.db)

    # Check for duplicate name
    existing = await repo.get_integration_by_name(request.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Integration '{request.name}' already exists",
        )

    integration = await repo.create_integration(request)
    logger.info(f"Created integration: {log_safe(integration.name)}")

    await emit_audit(
        ctx.db,
        "integration.create",
        resource_type="integration",
        resource_id=integration.id,
        details={"name": integration.name},
    )

    response = _integration_to_response(integration)
    await ctx.db.commit()
    return response


@router.get(
    "",
    response_model=IntegrationListResponse,
    summary="List integrations",
    description="List all integrations (Platform admin only)",
**operation_route("integrations.list"))
async def list_integrations(
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationListResponse:
    """List all integrations."""
    caller = await _catalog_reader(ctx, user, operation="integrations.list")
    repo = IntegrationsRepository(ctx.db)
    integrations = await repo.list_integrations()
    summaries = await repo.get_list_connection_summaries(
        operation_reach(caller, "integrations.list")
    )

    items = [_integration_to_response(i, summaries.get(i.id)) for i in integrations]
    return IntegrationListResponse(items=items, total=len(items))


@router.get(
    "/{integration_id}",
    response_model=IntegrationDetailResponse,
    summary="Get integration by ID",
    description="Get a specific integration by ID with mappings and OAuth config (Platform admin only)",
**operation_route("integrations.get"))
async def get_integration(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationDetailResponse:
    """Get a specific integration by ID with nested mappings and OAuth config."""
    caller = await _catalog_reader(ctx, user, operation="integrations.get")
    mapping_reach = operation_reach(caller, "integrations.get")
    repo = IntegrationsRepository(ctx.db)
    integration = await repo.get_integration_detail_by_id(integration_id)

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    # Build OAuth config summary if OAuth provider exists
    oauth_config = None
    if integration.oauth_provider:
        provider = integration.oauth_provider
        # Token status is Global connection metadata, so it only follows the
        # response when the caller can read Global itself.
        token = provider.tokens[0] if provider.tokens and mapping_reach.covers(None) else None
        oauth_config = OAuthConfigSummary(
            provider_name=provider.provider_name,
            oauth_flow_type=provider.oauth_flow_type,
            client_id=provider.client_id,
            authorization_url=provider.authorization_url,
            token_url=provider.token_url or "",
            scopes=provider.scopes or [],
            tenant_id=(provider.provider_metadata or {}).get("tenant_id"),
            status=(provider.status or "not_connected") if mapping_reach.covers(None) else "not_connected",
            status_message=provider.status_message if mapping_reach.covers(None) else None,
            expires_at=token.expires_at if token else None,
            last_refresh_at=provider.last_token_refresh if mapping_reach.covers(None) else None,
            has_refresh_token=token.encrypted_refresh_token is not None if token else False,
            entity_id_source=provider.entity_id_source,
        )

    # Build mapping responses with org-specific overrides only (not merged with defaults)
    # This prevents users from accidentally saving defaults back to org config
    all_org_configs = await repo.get_all_org_config_overrides(
        integration.id, mapping_reach
    )
    mapping_responses = []
    for m in integration.mappings:
        if not mapping_reach.covers(m.organization_id):
            continue
        org_config = all_org_configs.get(m.organization_id, {}) if m.organization_id else {}
        mapping_responses.append(
            await _mapping_to_response(ctx.db, m, config=org_config if org_config else None)
        )

    # Convert ORM config_schema items to Pydantic models
    config_schema_items = None
    if integration.config_schema:
        config_schema_items = [
            ConfigSchemaItem.model_validate(item)
            for item in integration.config_schema
        ]

    # Get integration-level default config values
    config_defaults = await repo.get_integration_defaults(
        integration.id, external=False, reveal_secrets=False
    )

    return IntegrationDetailResponse(
        id=integration.id,
        name=integration.name,
        description=integration.description,
        notes=integration.notes,
        workflow_links=integration.workflow_links,
        list_entities_data_provider_id=integration.list_entities_data_provider_id,
        config_schema=config_schema_items,
        config_defaults=config_defaults if config_defaults else None,
        entity_id=integration.entity_id,
        entity_id_name=integration.entity_id_name,
        default_entity_id=integration.default_entity_id,
        has_oauth_config=integration.has_oauth_config,
        logo_url=_integration_logo_url(integration),
        logo=_logo_data_url(
            integration.logo_thumbnail_data or integration.logo_data,
            integration.logo_thumbnail_content_type or integration.logo_content_type,
        ),
        logo_version=(
            integration.logo_thumbnail_version
            if is_logo_thumbnail_version(integration.logo_thumbnail_version)
            else None
        ),
        mapping_count=len(mapping_responses),
        connected_count=sum(
            1 for mapping in mapping_responses if mapping.connection_status == "completed"
        ),
        needs_reconnection_count=sum(
            1 for mapping in mapping_responses if mapping.connection_status == "failed"
        ),
        connection_status_counts={
            status_value: sum(
                1
                for mapping in mapping_responses
                if mapping.connection_status == status_value
            )
            for status_value in {
                mapping.connection_status
                for mapping in mapping_responses
                if mapping.connection_status
            }
        },
        is_deleted=integration.is_deleted,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        mappings=mapping_responses,
        oauth_config=oauth_config,
    )


@router.get(
    "/by-name/{name}",
    response_model=IntegrationResponse,
    summary="Get integration by name",
    description="Get a specific integration by name (Platform admin only)",
)
async def get_integration_by_name(
    name: str,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationResponse:
    """Get a specific integration by name."""
    await _catalog_reader(ctx, user, operation="GET /api/integrations/by-name/{name}")
    repo = IntegrationsRepository(ctx.db)
    integration = await repo.get_integration_by_name(name)

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    return _integration_to_response(integration)


@router.put(
    "/{integration_id}",
    response_model=IntegrationResponse,
    summary="Update integration",
    description="Update an existing integration (Platform admin only)",
**operation_route("integrations.update"))
async def update_integration(
    integration_id: UUID,
    request: IntegrationUpdate,
    ctx: Context,
    user: CurrentActiveUser,
    force_remove_keys: bool = Query(
        False,
        description="Confirm deletion of config-schema keys and their stored values",
    ),
) -> IntegrationResponse:
    """Update an integration.

    If the new ``config_schema`` drops keys that have saved ``Config``
    values, this refuses with 409 ``integration_schema_removal_requires_confirmation``
    (reporting the affected count) unless ``force_remove_keys=true``.
    """
    await _global_definition_writer(ctx, user, operation="integrations.update")
    repo = IntegrationsRepository(ctx.db)

    current = await repo.get_integration_by_id(integration_id)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    if request.config_schema is not None:
        current_by_key = {item.key: item for item in current.config_schema}
        incoming_keys = {item.key for item in request.config_schema}
        removed_keys = sorted(set(current_by_key) - incoming_keys)
        if removed_keys and not force_remove_keys:
            removed_ids = [current_by_key[key].id for key in removed_keys]
            affected_values = await ctx.db.scalar(
                select(func.count())
                .select_from(ConfigModel)
                .where(ConfigModel.config_schema_id.in_(removed_ids))
            )
            affected_count = int(affected_values or 0)
            if affected_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "integration_schema_removal_requires_confirmation",
                        "message": (
                            "Removing integration config-schema keys deletes stored "
                            "default and organization values"
                        ),
                        "removed_keys": removed_keys,
                        "affected_config_values": affected_count,
                        "force_remove_keys": True,
                    },
                )

    try:
        integration = await repo.update_integration(integration_id, request)
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    logger.info(f"Updated integration: {log_safe(integration.name)}")
    changed_fields = sorted(request.model_dump(exclude_unset=True))
    await emit_audit(
        ctx.db,
        "integration.update",
        resource_type="integration",
        resource_id=integration.id,
        details={"name": integration.name, "changed_fields": changed_fields},
    )
    await ctx.db.commit()
    return _integration_to_response(integration)


@router.delete(
    "/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete integration",
    description="Soft delete an integration (Platform admin only)",
**operation_route("integrations.delete"))
async def delete_integration(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> None:
    """Soft delete an integration."""
    await _global_definition_writer(ctx, user, operation="integrations.delete")
    repo = IntegrationsRepository(ctx.db)
    deleted = await repo.delete_integration(integration_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    logger.info(f"Deleted integration: {log_safe(integration_id)}")


# =============================================================================
# Integration Logo Endpoints
# =============================================================================


@router.post(
    "/{integration_id}/logo",
    summary="Upload integration logo",
)
async def upload_integration_logo(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
    file: UploadFile = File(..., description="Logo image (PNG/JPEG/SVG, ≤5MB)"),
) -> dict:
    """Upload a square logo for an integration."""
    await _global_definition_writer(ctx, user, operation="POST /api/integrations/{integration_id}/logo")
    repo = IntegrationsRepository(ctx.db)
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    content = await file.read()
    try:
        processed = await asyncio.to_thread(process_logo, content, file.content_type or "")
    except LogoProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    integration.logo_data = processed.original_data
    integration.logo_content_type = processed.original_content_type
    integration.logo_thumbnail_data = processed.thumbnail_data
    integration.logo_thumbnail_content_type = processed.thumbnail_content_type
    integration.logo_thumbnail_version = processed.thumbnail_version
    await ctx.db.commit()
    return {"ok": True}


@router.get(
    "/{integration_id}/logo",
    summary="Get integration logo",
    responses={
        200: {
            "content": {
                "image/webp": {},
                "image/png": {},
                "image/jpeg": {},
                "image/svg+xml": {},
            }
        },
        404: {"description": "No logo set"},
    },
)
async def get_integration_logo(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> Response:
    await _catalog_reader(ctx, user, operation="GET /api/integrations/{integration_id}/logo")
    integration = (
        await ctx.db.execute(select(Integration).where(Integration.id == integration_id))
    ).scalar_one_or_none()
    if integration is None or integration.is_deleted or not integration.logo_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not set",
        )

    thumbnail_ready = bool(
        integration.logo_thumbnail_data and integration.logo_thumbnail_version
    )
    headers = (
        {
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{integration.logo_thumbnail_version}"',
        }
        if thumbnail_ready
        else {"Cache-Control": "no-store"}
    )
    return Response(
        content=integration.logo_thumbnail_data or integration.logo_data,
        media_type=(
            integration.logo_thumbnail_content_type
            or integration.logo_content_type
            or "application/octet-stream"
        ),
        headers=headers,
    )


@router.delete(
    "/{integration_id}/logo",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete integration logo",
)
async def delete_integration_logo(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> Response:
    await _global_definition_writer(ctx, user, operation="DELETE /api/integrations/{integration_id}/logo")
    repo = IntegrationsRepository(ctx.db)
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    integration.logo_data = None
    integration.logo_content_type = None
    integration.logo_thumbnail_data = None
    integration.logo_thumbnail_content_type = None
    integration.logo_thumbnail_version = None
    await ctx.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# HTTP Endpoints - Integration Config Defaults
# =============================================================================


class IntegrationConfigUpdate(BaseModel):
    """Request model for updating integration default config values."""

    config: dict[str, Any] = Field(
        ..., description="Default config values for this integration"
    )


class IntegrationConfigResponse(BaseModel):
    """Response model for integration config."""

    integration_id: UUID
    config: dict[str, Any]


@router.put(
    "/{integration_id}/config",
    response_model=IntegrationConfigResponse,
    summary="Update integration default config",
    description="Set default config values for an integration (Platform admin only)",
**operation_route("integrations.config.update"))
async def update_integration_config(
    integration_id: UUID,
    request: IntegrationConfigUpdate,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationConfigResponse:
    """Update integration-level default config values.

    These defaults are stored in the configs table with integration_id set
    but no organization_id. Per-org configs override these values.
    """
    await _global_definition_writer(ctx, user, operation="integrations.config.update")
    repo = IntegrationsRepository(ctx.db)

    # Verify integration exists
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    # Save config with integration_id only (no org_id = defaults)
    try:
        await repo.save_config(
            integration_id=integration_id,
            organization_id=None,
            config=request.config,
            updated_by=user.email,
        )
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(f"Updated default config for integration {log_safe(integration_id)}")

    # Return the saved config
    saved_config = await repo.get_integration_defaults(
        integration_id, external=False, reveal_secrets=False
    )
    return IntegrationConfigResponse(
        integration_id=integration_id,
        config=saved_config,
    )


@router.get(
    "/{integration_id}/config",
    response_model=IntegrationConfigResponse,
    summary="Get integration default config",
    description="Get default config values for an integration (Platform admin only)",
**operation_route("integrations.config.get"))
async def get_integration_config(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationConfigResponse:
    """Get integration-level default config values."""
    await _catalog_reader(ctx, user, operation="integrations.config.get")
    repo = IntegrationsRepository(ctx.db)

    # Verify integration exists
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    config = await repo.get_integration_defaults(
        integration_id, external=False, reveal_secrets=False
    )
    return IntegrationConfigResponse(
        integration_id=integration_id,
        config=config,
    )


# =============================================================================
# HTTP Endpoints - Integration Mappings
# =============================================================================


async def _mapping_to_response(
    db,
    m,  # IntegrationMapping ORM instance
    config: dict | None = None,
) -> IntegrationMappingResponse:
    """Build IntegrationMappingResponse, hydrating per-token status."""
    status_val = None
    message = None
    last_refresh = None
    expires_at = None
    if m.oauth_token_id:
        token = await db.get(OAuthToken, m.oauth_token_id)
        if token:
            status_val = token.status
            message = token.status_message
            last_refresh = token.last_refresh_at
            expires_at = token.expires_at
    return IntegrationMappingResponse(
        id=m.id,
        integration_id=m.integration_id,
        organization_id=m.organization_id,
        entity_id=m.entity_id,
        entity_name=m.entity_name,
        oauth_token_id=m.oauth_token_id,
        config=config,
        connection_status=status_val,
        connection_message=message,
        last_refresh_at=last_refresh,
        connection_expires_at=expires_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.post(
    "/{integration_id}/mappings",
    response_model=IntegrationMappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create integration mapping",
    description="Create a new mapping between an integration and organization (Platform admin only)",
**operation_route("integrations.mappings.create"))
async def create_mapping(
    integration_id: UUID,
    request: IntegrationMappingCreate,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationMappingResponse:
    """Create a new integration mapping."""
    caller = await load_caller(ctx.db, user)
    require_operation(caller, "integrations.mappings.create", org_target(request.organization_id))
    repo = IntegrationsRepository(ctx.db)

    # Verify integration exists
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    # Check for duplicate mapping (one per org per integration)
    existing = await repo.get_mapping_by_org(integration_id, request.organization_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mapping already exists for this organization and integration",
        )

    try:
        mapping = await repo.create_mapping(integration_id, request, updated_by=user.email)
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        f"Created mapping for integration {log_safe(integration_id)} in org {log_safe(request.organization_id)}"
    )
    await emit_audit(
        ctx.db,
        "integration_mapping.create",
        resource_type="integration_mapping",
        resource_id=mapping.id,
        details={
            "integration_id": str(integration_id),
            "organization_id": str(request.organization_id),
        },
    )

    # Get org-specific overrides only (not merged with defaults)
    org_config = await repo.get_org_config_overrides(integration_id, request.organization_id)

    return await _mapping_to_response(ctx.db, mapping, config=org_config if org_config else None)


@router.get(
    "/{integration_id}/mappings",
    response_model=IntegrationMappingListResponse,
    summary="List mappings for integration",
    description="List all mappings for a specific integration (Platform admin only)",
**operation_route("integrations.mappings.list"))
async def list_mappings(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationMappingListResponse:
    """List all mappings for an integration."""
    caller = await load_caller(ctx.db, user)
    reach = operation_reach(caller, "integrations.mappings.list")
    repo = IntegrationsRepository(ctx.db)

    # Verify integration exists
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    mappings = await repo.list_mappings(integration_id, reach=reach)

    items = [await _mapping_to_response(ctx.db, m) for m in mappings]
    return IntegrationMappingListResponse(items=items, total=len(items))


@router.get(
    "/{integration_id}/mappings/{mapping_id}",
    response_model=IntegrationMappingResponse,
    summary="Get integration mapping",
    description="Get a specific mapping by ID (Platform admin only)",
**operation_route("integrations.mappings.get"))
async def get_mapping(
    integration_id: UUID,
    mapping_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationMappingResponse:
    """Get a specific mapping."""
    repo = IntegrationsRepository(ctx.db)

    mapping = await _mapping_for_operation(
        repo, ctx, user, integration_id, mapping_id, operation="integrations.mappings.get"
    )

    # Get org-specific overrides only (not merged with defaults)
    org_config = await repo.get_org_config_overrides(integration_id, mapping.organization_id)

    return await _mapping_to_response(ctx.db, mapping, config=org_config if org_config else None)


@router.get(
    "/{integration_id}/mappings/by-org/{org_id}",
    response_model=IntegrationMappingResponse,
    summary="Get mapping by organization",
    description="Get the mapping for an integration in a specific organization (Platform admin only)",
**operation_route("integrations.mappings.get_by_org"))
async def get_mapping_by_org(
    integration_id: UUID,
    org_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationMappingResponse:
    """Get the mapping for an integration in a specific organization."""
    caller = await load_caller(ctx.db, user)
    require_operation(caller, "integrations.mappings.get_by_org", org_target(org_id))
    repo = IntegrationsRepository(ctx.db)

    mapping = await repo.get_mapping_by_org(integration_id, org_id)
    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mapping not found for this organization",
        )

    # Get org-specific overrides only (not merged with defaults)
    org_config = await repo.get_org_config_overrides(integration_id, org_id)

    return await _mapping_to_response(ctx.db, mapping, config=org_config if org_config else None)


@router.put(
    "/{integration_id}/mappings/{mapping_id}",
    response_model=IntegrationMappingResponse,
    summary="Update integration mapping",
    description="Update an existing mapping (Platform admin only)",
**operation_route("integrations.mappings.update"))
async def update_mapping(
    integration_id: UUID,
    mapping_id: UUID,
    request: IntegrationMappingUpdate,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationMappingResponse:
    """Update an integration mapping."""
    repo = IntegrationsRepository(ctx.db)
    await _mapping_for_operation(
        repo, ctx, user, integration_id, mapping_id, operation="integrations.mappings.update"
    )

    try:
        mapping = await repo.update_mapping(
            integration_id, mapping_id, request, updated_by=user.email
        )
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mapping not found",
        )

    logger.info(f"Updated mapping {log_safe(mapping_id)} for integration {log_safe(integration_id)}")
    await emit_audit(
        ctx.db,
        "integration_mapping.update",
        resource_type="integration_mapping",
        resource_id=mapping.id,
        details={"integration_id": str(integration_id)},
    )

    # Get org-specific overrides only (not merged with defaults)
    org_config = await repo.get_org_config_overrides(integration_id, mapping.organization_id)

    return await _mapping_to_response(ctx.db, mapping, config=org_config if org_config else None)


@router.post(
    "/{integration_id}/mappings/batch",
    response_model=IntegrationMappingBatchResponse,
    summary="Batch upsert integration mappings",
    description="Create or update multiple mappings in a single request (Platform admin only)",
**operation_route("integrations.mappings.batch"))
async def batch_upsert_mappings(
    integration_id: UUID,
    request: IntegrationMappingBatchRequest,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationMappingBatchResponse:
    """Batch create/update integration mappings."""
    caller = await load_caller(ctx.db, user)
    for item in request.mappings:
        require_operation(caller, "integrations.mappings.batch", org_target(item.organization_id))
    repo = IntegrationsRepository(ctx.db)

    # Verify integration exists
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    created = 0
    updated = 0
    errors: list[str] = []

    for item in request.mappings:
        try:
            existing = await repo.get_mapping_by_org(integration_id, item.organization_id)
            if existing:
                update_data = IntegrationMappingUpdate(
                    entity_id=item.entity_id,
                    entity_name=item.entity_name,
                )
                await repo.update_mapping(
                    integration_id, existing.id, update_data, updated_by=user.email
                )
                updated += 1
            else:
                create_data = IntegrationMappingCreate(
                    organization_id=item.organization_id,
                    entity_id=item.entity_id,
                    entity_name=item.entity_name,
                )
                await repo.create_mapping(integration_id, create_data, updated_by=user.email)
                created += 1
        except Exception as e:
            errors.append(f"org {item.organization_id}: {str(e)}")
            log_safe_exception(logger, f"Batch mapping error for org {item.organization_id}: {log_safe(e)}")

    await ctx.db.commit()

    logger.info(
        f"Batch upsert for integration {log_safe(integration_id)}: "
        f"created={created}, updated={updated}, errors={len(errors)}"
    )

    return IntegrationMappingBatchResponse(
        created=created,
        updated=updated,
        errors=errors,
    )


@router.delete(
    "/{integration_id}/mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete integration mapping",
    description="Delete an integration mapping (Platform admin only)",
**operation_route("integrations.mappings.delete"))
async def delete_mapping(
    integration_id: UUID,
    mapping_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> None:
    """Delete an integration mapping."""
    repo = IntegrationsRepository(ctx.db)
    await _mapping_for_operation(
        repo, ctx, user, integration_id, mapping_id, operation="integrations.mappings.delete"
    )

    deleted = await repo.delete_mapping(integration_id, mapping_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mapping not found",
        )

    logger.info(f"Deleted mapping {log_safe(mapping_id)} for integration {log_safe(integration_id)}")


@router.post(
    "/{integration_id}/mappings/{mapping_id}/oauth/authorize",
    response_model=MappingAuthorizeResponse,
    summary="Begin OAuth authorize flow for a mapping",
    description="Returns the authorization URL with a signed state token carrying mapping_id (Platform admin only)",
**operation_route("integrations.mappings.authorize"))
async def authorize_mapping(
    integration_id: UUID,
    mapping_id: UUID,
    request: MappingAuthorizeRequest,
    ctx: Context,
    user: CurrentActiveUser,
) -> MappingAuthorizeResponse:
    """Begin the OAuth authorization flow for a specific integration mapping.

    Returns an authorization URL containing a signed state token that carries
    the mapping_id so the callback can attribute the resulting token to this mapping.
    """
    repo = IntegrationsRepository(ctx.db)
    await _mapping_for_operation(
        repo, ctx, user, integration_id, mapping_id, operation="integrations.mappings.authorize"
    )
    integration = await repo.get_integration_by_id(integration_id)
    if not integration or not integration.oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration or its OAuth provider not found",
        )
    provider = integration.oauth_provider
    if provider.oauth_flow_type == "workload_identity":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workload Identity is SDK managed and does not obtain or refresh OAuth tokens",
        )
    if not provider.authorization_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This integration uses client_credentials flow and does not require user authorization",
        )

    mapping = await repo.get_mapping_by_id(integration_id, mapping_id)
    assert mapping is not None

    defaults = await get_url_resolution_defaults(ctx.db, provider)
    resolved_url = resolve_url_template(url=provider.authorization_url, defaults=defaults)
    state, nonce = encode_state(provider_id=provider.id, mapping_id=mapping_id)
    await remember_nonce(nonce)
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "state": state,
        "scope": " ".join(provider.scopes) if provider.scopes else "",
        "redirect_uri": request.redirect_uri,
    }

    logger.info(
        f"Generated per-mapping OAuth authorization URL for mapping {log_safe(mapping_id)}, "
        f"integration {log_safe(integration_id)}"
    )

    return MappingAuthorizeResponse(
        authorization_url=append_query_params(resolved_url, params),
    )


@router.post(
    "/{integration_id}/mappings/{mapping_id}/oauth/disconnect",
    status_code=204,
    summary="Disconnect a mapping's per-row OAuth connection",
    description="Deletes the mapping's OAuth token and clears oauth_token_id. Fallback to integration-level token resumes (Platform admin only).",
**operation_route("integrations.mappings.disconnect"))
async def disconnect_mapping(
    integration_id: UUID,
    mapping_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> None:
    repo = IntegrationsRepository(ctx.db)
    mapping = await _mapping_for_operation(
        repo, ctx, user, integration_id, mapping_id, operation="integrations.mappings.disconnect"
    )

    integration = mapping.integration
    organization = mapping.organization
    external_account_id = mapping.entity_id
    external_account_name = mapping.entity_name
    token_id = mapping.oauth_token_id
    mapping.oauth_token_id = None
    await ctx.db.flush()

    if token_id is not None:
        token = await ctx.db.get(OAuthToken, token_id)
        if token:
            await ctx.db.delete(token)
            await ctx.db.flush()

    if token_id is not None:
        try:
            from src.services.events.builtins import emit_integration_disconnected

            await emit_integration_disconnected(
                integration_id=integration.id if integration else integration_id,
                integration_name=integration.name if integration else None,
                organization_id=mapping.organization_id,
                organization_name=organization.name if organization else None,
                connection_id=mapping.id,
                external_account_id=external_account_id,
                external_account_name=external_account_name,
                actor_user_id=ctx.user.user_id,
                actor_email=ctx.user.email,
                actor_name=ctx.user.name,
            )
        except Exception as e:
            logger.warning(f"Failed to emit integration.disconnected: {e}", exc_info=True)


@router.post(
    "/{integration_id}/mappings/{mapping_id}/oauth/refresh",
    response_model=IntegrationMappingResponse,
    summary="Refresh a mapping's per-row OAuth token",
    description=(
        "Proactively refresh the OAuth access token linked to this mapping. "
        "Uses the stored refresh token (authorization_code) or re-mints with "
        "client credentials (client_credentials). Updates token.status and "
        "token.last_refresh_at; provider.status is NOT touched (per-mapping "
        "tokens don't poison the integration-level fallback's health). "
        "Platform admin only."
    ),
**operation_route("integrations.mappings.refresh"))
async def refresh_mapping_oauth(
    integration_id: UUID,
    mapping_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationMappingResponse:
    from src.services.oauth_provider import (
        build_token_refresh_context,
        refresh_oauth_token_http,
    )

    repo = IntegrationsRepository(ctx.db)
    mapping = await _mapping_for_operation(
        repo, ctx, user, integration_id, mapping_id, operation="integrations.mappings.refresh"
    )
    integration = await repo.get_integration_by_id(integration_id)
    if not integration or not integration.oauth_provider:
        raise HTTPException(status_code=400, detail="Integration has no OAuth provider")
    provider = integration.oauth_provider

    if provider.oauth_flow_type == "workload_identity":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workload Identity is SDK managed and does not obtain or refresh OAuth tokens",
        )

    if not mapping.oauth_token_id:
        raise HTTPException(
            status_code=400,
            detail="Mapping has no per-row OAuth connection to refresh",
        )

    token = await ctx.db.get(OAuthToken, mapping.oauth_token_id)
    if not token:
        # The FK is set but the row is gone — clean up the dangling pointer.
        mapping.oauth_token_id = None
        await ctx.db.flush()
        raise HTTPException(
            status_code=404,
            detail="OAuth token row not found (mapping link cleared)",
        )

    if provider.oauth_flow_type != "client_credentials" and not token.encrypted_refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token available — reconnect this mapping to acquire one",
        )

    td = await build_token_refresh_context(
        db=ctx.db,
        provider=provider,
        token=token,
        org_id=mapping.organization_id,
    )
    was_failed = token.status == "failed"
    previous_success_at = token.last_refresh_at if token.status == "completed" else None
    outcome = await refresh_oauth_token_http(td)

    now = datetime.now(timezone.utc)
    if outcome["success"]:
        token.encrypted_access_token = outcome["encrypted_access_token"]
        token.expires_at = outcome["expires_at"]
        if outcome.get("encrypted_refresh_token"):
            token.encrypted_refresh_token = outcome["encrypted_refresh_token"]
        if outcome.get("scopes"):
            token.scopes = outcome["scopes"]
        token.status = "completed"
        token.status_message = None
        token.last_refresh_at = now
        if was_failed:
            try:
                from src.services.events.builtins import emit_integration_refresh_recovered

                await emit_integration_refresh_recovered(
                    integration_id=integration.id,
                    integration_name=integration.name,
                    organization_id=mapping.organization_id,
                    organization_name=mapping.organization.name if mapping.organization else None,
                    connection_id=mapping.id,
                    external_account_id=mapping.entity_id,
                    external_account_name=mapping.entity_name,
                    attempt=1,
                    last_success_at=now,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to emit integration.refresh_recovered: {e}",
                    exc_info=True,
                )
    else:
        token.status = "failed"
        token.status_message = (outcome.get("error", "Refresh failed"))[:200]
        token.last_refresh_at = now
        await ctx.db.flush()
        try:
            from src.services.events.builtins import emit_integration_refresh_failed

            await emit_integration_refresh_failed(
                integration_id=integration.id,
                integration_name=integration.name,
                organization_id=mapping.organization_id,
                organization_name=mapping.organization.name if mapping.organization else None,
                connection_id=mapping.id,
                external_account_id=mapping.entity_id,
                external_account_name=mapping.entity_name,
                attempt=1,
                last_success_at=previous_success_at,
                error_code=outcome.get("error_code"),
                error_message=token.status_message or "Refresh failed",
                retryable=False,
                reauth_required=True,
            )
        except Exception as e:
            logger.warning(
                f"Failed to emit integration.refresh_failed: {e}",
                exc_info=True,
            )
        raise HTTPException(
            status_code=502,
            detail=token.status_message or "Refresh failed",
        )

    await ctx.db.flush()
    logger.info(
        f"Refreshed per-mapping OAuth token for mapping {log_safe(mapping_id)}"
    )

    return await _mapping_to_response(ctx.db, mapping)


# =============================================================================
# HTTP Endpoints - OAuth Configuration
# =============================================================================


@router.get(
    "/{integration_id}/oauth",
    response_model=OAuthConfigResponse,
    summary="Get OAuth provider config",
    description="Get the OAuth provider configuration for this integration (Platform admin only)",
**operation_route("integrations.oauth.get"))
async def get_oauth_config(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
) -> OAuthConfigResponse:
    """Get the OAuth provider configuration associated with an integration."""
    await _catalog_reader(ctx, user, operation="integrations.oauth.get")
    repo = IntegrationsRepository(ctx.db)

    oauth_provider = await repo.get_oauth_provider(integration_id)
    if not oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No OAuth configuration found for this integration",
        )

    return OAuthConfigResponse(
        id=oauth_provider.id,
        provider_name=oauth_provider.provider_name,
        display_name=oauth_provider.display_name,
        oauth_flow_type=oauth_provider.oauth_flow_type,
        client_id=oauth_provider.client_id,
        authorization_url=oauth_provider.authorization_url,
        token_url=oauth_provider.token_url,
        scopes=oauth_provider.scopes or [],
        created_at=oauth_provider.created_at,
        updated_at=oauth_provider.updated_at,
    )


@router.get(
    "/{integration_id}/oauth/authorize",
    response_model=OAuthAuthorizeResponse,
    summary="Get OAuth authorization URL",
    description="Get the authorization URL for this integration's OAuth flow (Platform admin only)",
**operation_route("integrations.oauth.authorize"))
async def get_oauth_authorization_url(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
    redirect_uri: str = Query(..., description="Frontend callback URL for OAuth redirect"),
) -> OAuthAuthorizeResponse:
    """
    Get the authorization URL for this integration's OAuth flow.

    This initiates the OAuth authorization flow and returns the URL
    where the user should be redirected for authorization.
    """
    await _catalog_reader(ctx, user, operation="integrations.oauth.authorize")
    repo = IntegrationsRepository(ctx.db)

    oauth_provider = await repo.get_oauth_provider(integration_id)
    if not oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No OAuth configuration found for this integration",
        )

    if oauth_provider.oauth_flow_type == "workload_identity":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workload Identity is SDK managed and does not obtain or refresh OAuth tokens",
        )

    # Check if authorization_url is available (not all flows require it)
    if not oauth_provider.authorization_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This integration uses client_credentials flow and does not require user authorization",
        )

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    # Convert scopes list to space-separated string (OAuth 2.0 standard format)
    scopes_str = " ".join(oauth_provider.scopes) if oauth_provider.scopes else ""
    params = {
        "client_id": oauth_provider.client_id,
        "response_type": "code",
        "state": state,
        "scope": scopes_str,
        "redirect_uri": redirect_uri,
    }

    authorization_url = append_query_params(oauth_provider.authorization_url, params)

    logger.info(
        f"Generated OAuth authorization URL for integration {log_safe(integration_id)}, "
        f"provider {log_safe(oauth_provider.provider_name)}"
    )

    return OAuthAuthorizeResponse(
        authorization_url=authorization_url,
        state=state,
        message="Redirect user to authorization_url to complete OAuth flow",
    )


@router.patch(
    "/{integration_id}/oauth/entity_id_source",
    summary="Set entity_id_source on the integration's OAuth provider",
    description=(
        "Persists the admin's picker selection. Optionally backfills a "
        "specific mapping's entity_id (used when the picker fires inside "
        "the OAuth popup of a per-mapping connect). Platform admin only."
    ),
**operation_route("integrations.oauth.entity_id_source.update"))
async def set_entity_id_source(
    integration_id: UUID,
    request: EntityIdSourceUpdateRequest,
    ctx: Context,
    user: CurrentActiveUser,
) -> dict:
    from src.models.orm import IntegrationMapping as _IM
    from src.models.orm import OAuthProvider as _OP

    caller = await _global_definition_writer(
        ctx, user, operation="integrations.oauth.entity_id_source.update"
    )
    if request.type not in {"url_param", "token_response_field", "id_token_claim"}:
        raise HTTPException(status_code=400, detail=f"Invalid type: {request.type}")
    if not request.key:
        raise HTTPException(status_code=400, detail="key is required")

    result = await ctx.db.execute(
        select(_OP).where(_OP.integration_id == integration_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration has no OAuth provider",
        )

    provider.entity_id_source = {"type": request.type, "key": request.key}

    if request.apply_to_mapping_id and request.apply_value:
        mapping = await ctx.db.get(_IM, request.apply_to_mapping_id)
        if mapping and mapping.integration_id == integration_id and not mapping.entity_id:
            require_operation(
                caller, "integrations.mappings.update", org_target(mapping.organization_id)
            )
            mapping.entity_id = request.apply_value

    await ctx.db.flush()
    return {"entity_id_source": provider.entity_id_source}


@router.delete(
    "/{integration_id}/oauth/entity_id_source",
    summary="Clear entity_id_source on the integration's OAuth provider",
    description=(
        "Resets the provider's entity_id_source to NULL. Picker will reappear "
        "on next OAuth connect so the admin can pick a different source. "
        "When clear_mappings=true, also clears entity_id on every mapping "
        "under this integration so they re-capture on reconnect. "
        "Platform admin only."
    ),
**operation_route("integrations.oauth.entity_id_source.delete"))
async def clear_entity_id_source(
    integration_id: UUID,
    ctx: Context,
    user: CurrentActiveUser,
    clear_mappings: bool = Query(
        False,
        description="When true, also clear entity_id on every mapping for this integration",
    ),
) -> dict:
    from src.models.orm import IntegrationMapping as _IM
    from src.models.orm import OAuthProvider as _OP

    caller = await _global_definition_writer(
        ctx, user, operation="integrations.oauth.entity_id_source.delete"
    )
    result = await ctx.db.execute(
        select(_OP).where(_OP.integration_id == integration_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration has no OAuth provider",
        )

    provider.entity_id_source = None
    cleared_mapping_count = 0

    if clear_mappings:
        mappings_result = await ctx.db.execute(
            select(_IM).where(_IM.integration_id == integration_id)
        )
        mappings = mappings_result.scalars().all()
        for mapping in mappings:
            require_operation(
                caller, "integrations.mappings.update", org_target(mapping.organization_id)
            )
        for mapping in mappings:
            if mapping.entity_id:
                mapping.entity_id = ""
                cleared_mapping_count += 1

    await ctx.db.flush()
    return {
        "entity_id_source": None,
        "cleared_mapping_count": cleared_mapping_count,
    }


# =============================================================================
# HTTP Endpoints - Integration Testing
# =============================================================================
#
# NOTE (EXT-1 NEW-H): the former GET /api/integrations/sdk/{name} endpoint was
# DELETED. It took ``org_id`` as a free Query param with NO own-org check and
# returned decrypted SECRET config + OAuth metadata for ANY org_id the caller
# named — any authenticated user (incl. an external hospital portal user) could
# read ANY tenant's integration secrets. It was orphaned (superseded by
# POST /api/sdk/integrations/get, which goes through _resolve_sdk_org_id and the
# external gate); no live Python/TS/app/workflow caller referenced it. Removed
# along with its IntegrationSDKResponse contract.


# Test code template that runs in workflow execution context
_TEST_INTEGRATION_CODE = '''
from bifrost import integrations
import httpx
import time

async def test():
    """Test integration connectivity by making a GET request to the specified endpoint."""
    integration_name = "{integration_name}"
    endpoint = "{endpoint}"
    org_id = {org_id}

    # Get integration config (includes OAuth tokens if available)
    integration = await integrations.get(integration_name, org_id)
    if not integration:
        return {{"success": False, "error": f"Integration '{{integration_name}}' not found"}}

    base_url = integration.config.get("base_url", "").rstrip("/")
    if not base_url:
        return {{"success": False, "error": "base_url not configured"}}

    # Build headers based on available auth
    headers = {{}}
    auth_method = "none"

    if integration.oauth and integration.oauth.access_token:
        headers["Authorization"] = f"Bearer {{integration.oauth.access_token}}"
        auth_method = "oauth"
    elif integration.config.get("token"):
        headers["Authorization"] = f"Bearer {{integration.config['token']}}"
        auth_method = "bearer"
    elif integration.config.get("api_key"):
        header_name = integration.config.get("header_name", "Authorization")
        headers[header_name] = integration.config["api_key"]
        auth_method = "api_key"

    # Make request
    url = f"{{base_url}}{{endpoint}}"
    start_time = time.time()

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, timeout=30.0)

    duration_ms = int((time.time() - start_time) * 1000)

    return {{
        "success": response.status_code < 400,
        "status_code": response.status_code,
        "url": url,
        "auth_method": auth_method,
        "duration_ms": duration_ms,
    }}

result = await test()
'''


@router.post(
    "/{integration_id}/test",
    response_model=IntegrationTestResponse,
    summary="Test integration connection",
    description="Test connectivity to an integration by making a GET request to the specified endpoint (Platform admin only)",
**operation_route("integrations.test"))
async def test_integration_connection(
    integration_id: UUID,
    request: IntegrationTestRequest,
    ctx: Context,
    user: CurrentActiveUser,
) -> IntegrationTestResponse:
    """
    Test an integration's connectivity.

    Executes inline code synchronously that:
    1. Uses bifrost.integrations.get() to fetch config and OAuth tokens
    2. Makes a GET request to the specified endpoint
    3. Returns success/failure with status code

    Uses sync execution pattern (queue + Redis BLPOP) to wait for result.
    """
    import base64
    import time
    from uuid import uuid4
    from src.sdk.context import ExecutionContext as SharedContext, Organization
    from shared.run_lineage import principal_lineage
    from src.services.execution.async_executor import enqueue_code_execution
    from src.core.redis_client import get_redis_client

    caller = await load_caller(ctx.db, user)
    require_operation(caller, "integrations.test", org_target(request.organization_id))
    repo = IntegrationsRepository(ctx.db)

    # Get integration
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    # Build execution context
    # If org_id provided, use that org's context; otherwise use global scope
    org = None
    scope = "GLOBAL"
    if request.organization_id:
        org = Organization(id=str(request.organization_id), name="", is_active=True)
        scope = str(request.organization_id)

    execution_id = str(uuid4())
    shared_ctx = SharedContext(
        user_id=str(ctx.user.user_id),
        name=ctx.user.name,
        email=ctx.user.email,
        scope=scope,
        organization=org,
        is_platform_admin=ctx.user.is_superuser,
        is_function_key=False,
        execution_id=execution_id,
    )

    # Format the test code with parameters
    org_id_str = f'"{request.organization_id}"' if request.organization_id else "None"
    test_code = _TEST_INTEGRATION_CODE.format(
        integration_name=integration.name,
        endpoint=request.endpoint,
        org_id=org_id_str,
    )

    script_name = f"test_integration_{integration.name}"
    lineage = await principal_lineage(ctx.db, user)
    scope_label = f"org {request.organization_id}" if request.organization_id else "global"
    start_time = time.time()

    try:
        redis_client = get_redis_client()
        # Queue with sync=True to wait for result
        await enqueue_code_execution(
            context=shared_ctx,
            script_name=script_name,
            code_base64=base64.b64encode(test_code.encode()).decode(),
            parameters={},
            execution_id=execution_id,
            sync=True,
            lineage=lineage,
        )

        # Wait for result from worker via Redis BLPOP
        worker_result = await redis_client.wait_for_result(execution_id, timeout_seconds=60)

        duration_ms = int((time.time() - start_time) * 1000)

        if worker_result is None:
            logger.error(f"Integration test for {log_safe(integration.name)} ({log_safe(scope_label)}): timeout")
            return IntegrationTestResponse(
                success=False,
                message=f"Test timed out for {integration.name}",
                error_details="Execution timed out after 60 seconds",
                duration_ms=duration_ms,
            )

        # Check execution status from worker
        status_str = worker_result.get("status", "Failed")
        if status_str == "Failed":
            error = worker_result.get("error", "Unknown error")
            logger.info(
                f"Integration test for {log_safe(integration.name)} ({log_safe(scope_label)}): failed - {log_safe(error)}"
            )
            return IntegrationTestResponse(
                success=False,
                message=f"Test execution failed for {integration.name}",
                error_details=error[:500] if error else "Unknown error",
                duration_ms=duration_ms,
            )

        # Parse the result from the test code
        result = worker_result.get("result")
        if isinstance(result, dict):
            success = result.get("success", False)
            if success:
                logger.info(
                    f"Integration test for {log_safe(integration.name)} ({log_safe(scope_label)}): success - "
                    f"HTTP {result.get('status_code')} at {log_safe(result.get('url'))}"
                )
                return IntegrationTestResponse(
                    success=True,
                    message=f"Successfully connected to {integration.name}",
                    method_called=f"GET {request.endpoint}",
                    duration_ms=result.get("duration_ms", duration_ms),
                )
            else:
                error = result.get("error", f"HTTP {result.get('status_code', 'unknown')}")
                logger.info(
                    f"Integration test for {log_safe(integration.name)} ({log_safe(scope_label)}): failed - {log_safe(error)}"
                )
                return IntegrationTestResponse(
                    success=False,
                    message=f"Connection test failed for {integration.name}",
                    method_called=f"GET {request.endpoint}",
                    error_details=error,
                    duration_ms=result.get("duration_ms", duration_ms),
                )

        # Unexpected result format
        logger.warning(f"Unexpected test result format: {log_safe(result)}")
        return IntegrationTestResponse(
            success=False,
            message=f"Unexpected test result for {integration.name}",
            error_details=str(result)[:500] if result else "No result returned",
            duration_ms=duration_ms,
        )

    except Exception as e:
        import traceback
        logger.exception(
            f"Integration test error for {integration.name}: {e}\n{traceback.format_exc()}"
        )
        return IntegrationTestResponse(
            success=False,
            message=f"Test execution error for {integration.name}",
            error_details=str(e)[:500],
        )


# =============================================================================
# HTTP Endpoints - SDK Generation
# =============================================================================


def _get_config_schema_for_auth_type(auth_type: str) -> list[ConfigSchemaItem]:
    """Get config schema items required for a given auth type.

    Auto-creates the necessary config fields (base_url, token, etc.)
    so users can configure them in the UI.
    """
    items = [
        ConfigSchemaItem(
            key="base_url",
            type="string",
            required=True,
            description="Base URL for the API (e.g., https://api.example.com)",
        ),
    ]

    if auth_type == "bearer":
        items.append(
            ConfigSchemaItem(
                key="token",
                type="secret",
                required=True,
                description="Bearer token for authentication",
            )
        )
    elif auth_type == "api_key":
        items.extend(
            [
                ConfigSchemaItem(
                    key="header_name",
                    type="string",
                    required=True,
                    description="HTTP header name (e.g., x-api-key, Authorization)",
                ),
                ConfigSchemaItem(
                    key="api_key",
                    type="secret",
                    required=True,
                    description="API key value",
                ),
            ]
        )
    elif auth_type == "basic":
        items.extend(
            [
                ConfigSchemaItem(
                    key="username",
                    type="string",
                    required=True,
                    description="Username for basic auth",
                ),
                ConfigSchemaItem(
                    key="password",
                    type="secret",
                    required=True,
                    description="Password for basic auth",
                ),
            ]
        )
    # oauth: only needs base_url (token comes from OAuth provider)

    return items


class GenerateSDKRequest(BaseModel):
    """Request model for generating an SDK from an OpenAPI spec."""

    spec_url: str = Field(
        ...,
        description="URL to OpenAPI specification (JSON or YAML)",
    )
    auth_type: str = Field(
        ...,
        pattern="^(bearer|api_key|basic|oauth)$",
        description="Authentication type: bearer, api_key, basic, or oauth",
    )
    module_name: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Module name (lowercase, underscores). Defaults to spec title.",
    )


class GenerateSDKResponse(BaseModel):
    """Response model for SDK generation."""

    success: bool = Field(..., description="Whether generation succeeded")
    module_name: str = Field(..., description="Generated module name")
    module_path: str = Field(..., description="Path to generated module file")
    class_name: str = Field(..., description="Generated client class name")
    endpoint_count: int = Field(..., description="Number of API endpoints")
    schema_count: int = Field(..., description="Number of data schemas")
    usage_example: str = Field(..., description="Example code for using the SDK")


@router.post(
    "/{integration_id}/generate-sdk",
    response_model=GenerateSDKResponse,
    summary="Generate SDK from OpenAPI spec",
    description="Generate a Python SDK module from an OpenAPI specification (Platform admin only)",
**operation_route("integrations.generate_sdk"))
async def generate_sdk(
    integration_id: UUID,
    request: GenerateSDKRequest,
    ctx: Context,
    user: CurrentActiveUser,
) -> GenerateSDKResponse:
    """
    Generate a Python SDK from an OpenAPI specification.

    The generated SDK will automatically authenticate using this integration's
    configuration (base_url, tokens, API keys, etc.).

    The SDK is saved to the workspace modules/ folder and can be imported
    directly in workflows:

        from modules import example_api
        result = await example_api.list_users()
    """
    from src.services.sdk_generator import generate_sdk_from_url

    await _global_definition_writer(ctx, user, operation="integrations.generate_sdk")
    repo = IntegrationsRepository(ctx.db)

    # Verify integration exists
    integration = await repo.get_integration_by_id(integration_id)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    # Auto-create config schema for auth type (base_url, token, etc.)
    schema_items = _get_config_schema_for_auth_type(request.auth_type)
    await repo.update_integration(
        integration_id,
        IntegrationUpdate(config_schema=schema_items),
    )

    try:
        # Generate the SDK
        result = generate_sdk_from_url(
            spec_url=request.spec_url,
            integration_name=integration.name,
            auth_type=request.auth_type,  # type: ignore
            module_name=request.module_name,
        )

        # Write to S3 workspace
        from src.services.file_storage import FileStorageService
        storage = FileStorageService(ctx.db)
        module_path = f"modules/{result.module_name}.py"
        await storage.write_file(
            path=module_path,
            content=result.code.encode("utf-8"),
            updated_by=str(user.user_id),
        )

        logger.info(
            f"Generated SDK for integration {log_safe(integration.name)}: "
            f"{log_safe(module_path)} ({result.endpoint_count} endpoints)"
        )

        # Build usage example
        usage_example = f'''from modules import {result.module_name}

# All methods are async and auto-authenticate via Bifrost integration
result = await {result.module_name}.list_resources()
print(result)'''

        return GenerateSDKResponse(
            success=True,
            module_name=result.module_name,
            module_path=f"modules/{result.module_name}.py",
            class_name=result.class_name,
            endpoint_count=result.endpoint_count,
            schema_count=result.schema_count,
            usage_example=usage_example,
        )

    except Exception as e:
        logger.exception(f"SDK generation failed for integration {log_safe(integration_id)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SDK generation failed: {str(e)}",
        )
