"""
SDK Execution Router (historically named "CLI Router").

This is the **SDK execution surface**. The Bifrost CLI is one consumer;
the workflow runtime SDK and agent execution paths are equally first-class
callers. The "CLI" in the path and the file name is historical and will
be renamed in phase 7 of the org-scoping consolidation (see
`docs/plans/2026-05-26-org-scoping-consolidation.md`).

## Org scoping contract for this file

Every endpoint here that reads or writes an execution-resolution entity
(anything with `organization_id`: Config, Table, OAuth, Knowledge, etc.)
MUST:

- Use an `OrgScopedRepository` subclass for data access. Do NOT write
  inline cascade queries (`WHERE organization_id == x OR
  organization_id IS NULL`). The lint test
  `test_no_inline_org_scoping_in_routers` catches this.
- Pass `is_superuser=True` to the repository — the engine sentinel is
  the authenticated principal here, and the SDK has already resolved
  scope before the call reaches us.
- Receive the scope as a request body field; trust it as-is. The engine
  did the platform-admin-or-own-org check via
  `api/shared/scope_resolver.py::resolve_effective_scope` before
  calling us.

Endpoints that do NOT touch execution-resolution entities (auth,
context, health, download, CLI session management) are exempt. Document
the exemption in the endpoint docstring.

See `api/src/repositories/README.md` for the full canonical doc.

## Endpoints

- Developer context (default organization, parameters)
- CLI package download
- Config operations (get, set, list, delete)
- CLI Sessions (register, state, continue, pending, log, result)

Note: File operations have been moved to /api/files router.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context, CurrentEngineOrBypassUser, CurrentUser
from src.core.principal import UserPrincipal
from src.core.database import get_db
from src.core.log_safety import log_safe
from src.models.contracts.cli import (
    CLIAICompleteRequest,
    CLIAICompleteResponse,
    CLIAIInfoResponse,
    CLIConfigDeleteRequest,
    CLIConfigGetRequest,
    CLIConfigListRequest,
    CLIConfigSetRequest,
    CLIConfigValue,
    CLIKnowledgeDeleteRequest,
    CLIKnowledgeDocumentResponse,
    CLIKnowledgeNamespaceInfo,
    CLIKnowledgeSearchRequest,
    CLIKnowledgeStoreManyRequest,
    CLIKnowledgeStoreRequest,
    SDKIntegrationsGetRequest,
    SDKIntegrationsGetResponse,
    SDKIntegrationsListMappingsRequest,
    SDKIntegrationsListMappingsResponse,
    SDKIntegrationsGetMappingRequest,
    SDKIntegrationsUpsertMappingRequest,
    SDKIntegrationsDeleteMappingRequest,
    SDKIntegrationsMappingItem,
    SDKIntegrationsRefreshTokenRequest,
    SDKIntegrationsRefreshTokenResponse,
    SDKTableCreateRequest,
    SDKTableListRequest,
    SDKTableInfo,
)
from src.models.contracts.artifacts import (
    ArtifactDownloadResponse,
    ArtifactRef,
    DocumentArtifactSpec,
    ImageArtifactSpec,
    SpreadsheetArtifactSpec,
    TextArtifactSpec,
    VideoArtifactSpec,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.core.pubsub import (
    publish_execution_log,
    publish_execution_update,
    publish_history_update,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sdk", tags=["SDK"])

# /api/cli/download/bifrost-cli.tar.gz is the permanent, installer-compatible
# home for the CLI install endpoint. The extensionless /api/cli/download path
# remains a compatibility redirect for clients that actually make the request.
# These live at /api/cli/ (not /api/sdk/) because users type the URL.
# The rest of /api/cli/* moved to /api/sdk/* in the 2026-05 overhaul
# because those endpoints are the SDK execution surface, not the CLI.
install_router = APIRouter(prefix="/api/cli", tags=["CLI Install"])

CLI_DOWNLOAD_ALIAS = "bifrost-cli.tar.gz"
CLI_ARTIFACT_DIR = Path(os.environ.get("BIFROST_CLI_ARTIFACT_DIR", "/app/artifacts"))


# =============================================================================
# Pydantic Models (Developer Context)
# =============================================================================


class DeveloperContextResponse(BaseModel):
    """Developer context for CLI initialization.

    Sourced entirely from the auth-verified ``current_user`` and their
    ``organization_id``. There is no mutable per-user default-org override.
    Platform admins / provider-org members targeting another org pass
    ``?org_id=<uuid>`` on this endpoint or ``scope`` on each SDK call.
    """

    user: dict = Field(description="User information")
    organization: dict | None = Field(description="Default organization")
    default_parameters: dict = Field(
        default={}, description="Default workflow parameters"
    )
    track_executions: bool = Field(
        default=True, description="Whether to track executions in history"
    )


# =============================================================================
# Context Endpoints
# =============================================================================


@router.get(
    "/context",
    response_model=DeveloperContextResponse,
    summary="Get developer context",
)
async def get_dev_context(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    org_id: UUID | None = None,
) -> DeveloperContextResponse:
    """Get development context for CLI initialization.

    Returns the authenticated user and their ``organization_id``-resolved
    org. The optional ``org_id`` query parameter lets platform admins and
    provider-org members target another org for the session — gated by
    the same C2 rule the scope resolver applies elsewhere.

    Context behavior lives in the shared service (``shared.sdk_context``),
    which a worker-local engine child can call with the same inputs.
    """
    from shared.sdk_context import SdkContextError, get_sdk_context

    try:
        data = await get_sdk_context(db, current_user, org_id=org_id)
    except SdkContextError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None

    return DeveloperContextResponse(**data)


# =============================================================================
# CLI Config Operations
# =============================================================================


async def _resolve_sdk_org_id(
    current_user: "UserPrincipal",
    scope: str | None,
    db: AsyncSession,
) -> str | None:
    """Resolve the effective organization scope for an SDK call.

    Thin HTTP adapter over the shared scope rule
    (``shared.sdk_config.resolve_sdk_scope``), which the engine-local
    dispatcher calls for the same inputs. Grammar, UNSET/global/UUID
    parsing, provider-org bypass, and 403/422 precedence live in the
    shared service; this helper only maps its transport-neutral error
    to ``HTTPException``.

    Args:
        current_user: The auth-verified user principal.
        scope: Requested scope:
            - ``None`` or ``""``: UNSET — use caller's own org.
            - ``"global"``: Explicit global scope (bypass required).
            - org UUID string: Target specific organization (own org
              always; other orgs bypass required).
        db: Database session.

    Returns:
        Organization UUID string, or None for global scope.

    Raises:
        HTTPException 422: If ``scope`` is a non-empty string that is
            neither ``"global"`` nor a valid UUID.
        HTTPException 403: If the caller is not authorized to use the
            requested scope.
    """
    from fastapi import HTTPException

    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope

    try:
        resolved = await resolve_sdk_scope(
            scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    return str(resolved) if resolved is not None else None


@router.post(
    "/config/get",
    response_model=CLIConfigValue | None,
    summary="Get config value",
)
async def cli_get_config(
    request: CLIConfigGetRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> CLIConfigValue | None:
    """Get a config value via CLI API."""
    from shared.sdk_config import (
        ScopeResolutionError,
        get_sdk_config_value,
        resolve_sdk_scope,
    )

    try:
        org_uuid = await resolve_sdk_scope(
            request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    return await get_sdk_config_value(
        db,
        key=request.key,
        org_id=org_uuid,
        external=current_user.is_external,
    )


@router.post(
    "/config/set",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set config value",
)
async def cli_set_config(
    request: CLIConfigSetRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Set a config value via CLI API."""
    from shared.sdk_config import (
        ScopeResolutionError,
        resolve_sdk_scope,
        set_sdk_config_value,
    )

    try:
        org_uuid = await resolve_sdk_scope(
            request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    await set_sdk_config_value(
        db,
        key=request.key,
        value=request.value,
        is_secret=request.is_secret,
        org_id=org_uuid,
        actor_email=current_user.email,
    )


@router.post(
    "/config/list",
    summary="List config values",
)
async def cli_list_config(
    request: CLIConfigListRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all config values via CLI API."""
    from shared.sdk_config import (
        ScopeResolutionError,
        list_sdk_config_values,
        resolve_sdk_scope,
    )

    try:
        org_uuid = await resolve_sdk_scope(
            request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    # External callers get org-only (no global tier) — EXT-1 NEW-1.
    return await list_sdk_config_values(
        db,
        org_id=org_uuid,
        external=current_user.is_external,
    )


@router.post(
    "/config/delete",
    summary="Delete config value",
)
async def cli_delete_config(
    request: CLIConfigDeleteRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> bool:
    """Delete a config value via CLI API."""
    from shared.sdk_config import (
        ScopeResolutionError,
        delete_sdk_config_value,
        resolve_sdk_scope,
    )

    try:
        org_uuid = await resolve_sdk_scope(
            request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    return await delete_sdk_config_value(
        db,
        key=request.key,
        org_id=org_uuid,
        actor_email=current_user.email,
    )


# =============================================================================
# SDK Integrations Endpoints
# =============================================================================


@router.post(
    "/integrations/get",
    response_model=SDKIntegrationsGetResponse | None,
    summary="Get integration data for an organization",
)
async def sdk_integrations_get(
    request: SDKIntegrationsGetRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsGetResponse | None:
    """Get integration mapping data for an organization via SDK.

    Supports three modes:
    1. Global scope (scope="global"): Returns integration defaults only (no org mapping)
    2. Org-specific mapping: Returns mapping entity_id, config, and OAuth data
    3. Fallback to integration defaults: When no org mapping exists, returns
       integration.default_entity_id, integration-level config, and OAuth data

    Scope resolution and response construction live in the shared
    integrations service (``shared.sdk_integrations``), which the
    worker-local engine child calls for the same inputs.
    """
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from shared.sdk_integrations import (
        IntegrationServiceError,
        get_sdk_integration_dict,
    )

    try:
        org_uuid = await resolve_sdk_scope(
            request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    try:
        result = await get_sdk_integration_dict(
            db,
            name=request.name,
            org_id=org_uuid,
            oauth_scope=request.oauth_scope,
            solution_id=request.solution,
            external=current_user.is_external,
        )
    except IntegrationServiceError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None
    except HTTPException:
        # Auth/scope failures must surface.
        raise
    except Exception as e:
        logger.error(f"SDK integrations.get failed: {log_safe(e)}")
        return None

    if result is None:
        return None
    return SDKIntegrationsGetResponse(**result)


@router.post(
    "/integrations/list_mappings",
    response_model=SDKIntegrationsListMappingsResponse | None,
    summary="List all mappings for an integration",
)
async def sdk_integrations_list_mappings(
    request: SDKIntegrationsListMappingsRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsListMappingsResponse | None:
    """List all mappings for an integration via SDK.

    Scope resolution and response construction live in the shared
    integrations service (``shared.sdk_integrations``), which the
    worker-local engine child calls for the same inputs.
    """
    from shared.sdk_config import ScopeResolutionError
    from shared.sdk_integrations import list_sdk_integration_mappings

    try:
        items = await list_sdk_integration_mappings(
            db,
            name=request.name,
            scope=request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            external=current_user.is_external,
        )
    except ScopeResolutionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except HTTPException:
        # Authorization failures must surface to the client.
        raise
    except Exception as e:
        logger.error(f"SDK integrations.list_mappings failed: {log_safe(e)}")
        return None

    if items is None:
        return None
    return SDKIntegrationsListMappingsResponse(items=items)


@router.post(
    "/integrations/get_mapping",
    response_model=SDKIntegrationsMappingItem | None,
    summary="Get a specific mapping by org_id or entity_id",
)
async def sdk_integrations_get_mapping(
    request: SDKIntegrationsGetMappingRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsMappingItem | None:
    """Get a specific integration mapping by org_id or entity_id via SDK.

    Scope resolution and response construction live in the shared
    integrations service (``shared.sdk_integrations``), which the
    worker-local engine child calls for the same inputs.
    """
    from shared.sdk_config import ScopeResolutionError
    from shared.sdk_integrations import get_sdk_integration_mapping_dict

    try:
        result = await get_sdk_integration_mapping_dict(
            db,
            name=request.name,
            scope=request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            entity_id=request.entity_id,
            external=current_user.is_external,
        )
    except ScopeResolutionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except HTTPException:
        # Auth/scope failures must surface.
        raise
    except Exception as e:
        logger.error(f"SDK integrations.get_mapping failed: {log_safe(e)}")
        return None

    if result is None:
        return None
    return SDKIntegrationsMappingItem(**result)


@router.post(
    "/integrations/upsert_mapping",
    response_model=SDKIntegrationsMappingItem,
    summary="Create or update a mapping for an organization",
)
async def sdk_integrations_upsert_mapping(
    request: SDKIntegrationsUpsertMappingRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsMappingItem:
    """Create or update an integration mapping for an organization via SDK.

    Mutation rules live in the shared integrations service
    (``shared.sdk_integrations``), which the worker-local engine child calls
    for the same inputs.
    """
    from shared.sdk_config import ScopeResolutionError
    from shared.sdk_integrations import (
        IntegrationServiceError,
        upsert_sdk_integration_mapping,
    )

    try:
        result = await upsert_sdk_integration_mapping(
            db,
            name=request.name,
            scope=request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            external=current_user.is_external,
            entity_id=request.entity_id,
            entity_name=request.entity_name,
            config=request.config,
            actor_email=current_user.email,
        )
    except ScopeResolutionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except IntegrationServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SDK integrations.upsert_mapping failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upsert mapping: {str(e)}",
        )

    return SDKIntegrationsMappingItem(**result)


@router.post(
    "/integrations/delete_mapping",
    summary="Delete a mapping for an organization",
)
async def sdk_integrations_delete_mapping(
    request: SDKIntegrationsDeleteMappingRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete an integration mapping for an organization via SDK.

    Mutation rules live in the shared integrations service
    (``shared.sdk_integrations``), which the worker-local engine child calls
    for the same inputs.
    """
    from shared.sdk_config import ScopeResolutionError
    from shared.sdk_integrations import delete_sdk_integration_mapping

    try:
        return await delete_sdk_integration_mapping(
            db,
            name=request.name,
            scope=request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
        )
    except ScopeResolutionError as e:
        # Auth/scope failures must surface.
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except HTTPException:
        # Auth/scope failures (e.g. 403 from the scope gate) must surface.
        raise
    except Exception as e:
        logger.error(f"SDK integrations.delete_mapping failed: {log_safe(e)}")
        return {"deleted": False}


@router.post(
    "/integrations/refresh_token",
    response_model=SDKIntegrationsRefreshTokenResponse,
    summary="Refresh OAuth token for an integration",
)
async def sdk_integrations_refresh_token(
    request: SDKIntegrationsRefreshTokenRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsRefreshTokenResponse:
    """Programmatically refresh an OAuth token for an integration.

    For client_credentials flows: fetches a fresh token from the provider.
    For authorization_code flows: uses the stored refresh_token to get a new access token.

    The new token is persisted to the database so subsequent integrations.get() calls
    also benefit from the refreshed token.

    The refresh rules live in the shared integrations service
    (``shared.sdk_integrations``), which a worker-local engine child calls
    for the same inputs. The HTTP refresh itself is delegated to the shared
    primitive :func:`src.services.oauth_provider.refresh_oauth_token_http`
    via that service; this handler only maps transport errors.
    """
    from shared.sdk_config import ScopeResolutionError
    from shared.sdk_integrations import (
        IntegrationServiceError,
        refresh_sdk_oauth_token,
    )

    try:
        result = await refresh_sdk_oauth_token(
            db,
            connection_name=request.connection_name,
            scope=request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            external=current_user.is_external,
        )
    except ScopeResolutionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except IntegrationServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SDK integrations.refresh_token failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh failed: {str(e)}",
        )

    logger.info(
        f"SDK refreshed OAuth token for '{log_safe(request.connection_name)}' "
        f"by {current_user.email}"
    )

    return SDKIntegrationsRefreshTokenResponse(**result)


# =============================================================================
# SDK AI Endpoints
# =============================================================================


@router.post("/artifacts")
async def sdk_store_artifact(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    workspace_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Validate and store workflow-produced bytes behind an opaque identity."""
    from shared.sdk_artifacts import ArtifactCaller, sdk_store_artifact as _store

    filename = file.filename or "Artifact"
    content_type = file.content_type or "application/octet-stream"
    content = await file.read()
    return await _store(
        ArtifactCaller(user=current_user, db=db),
        filename=filename,
        content_type=content_type,
        content=content,
        workspace_id=workspace_id,
    )


@router.get("/artifacts", response_model=list[ArtifactRef])
async def sdk_list_artifacts(
    current_user: CurrentUser,
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ArtifactRef]:
    """List the latest logical files in one authorized execution workspace."""
    from shared.sdk_artifacts import ArtifactCaller, sdk_list_artifacts as _list

    return await _list(
        ArtifactCaller(user=current_user, db=db),
        workspace_id=workspace_id,
    )


@router.post("/artifacts/document")
async def sdk_render_document_artifact(
    request: DocumentArtifactSpec,
    current_user: CurrentUser,
    workspace_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Render and store a trusted PDF or DOCX artifact."""
    from shared.sdk_artifact_generation import (
        sdk_render_document_artifact as _render_document,
    )
    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError

    try:
        return await _render_document(
            ArtifactCaller(user=current_user, db=db),
            spec=request,
            workspace_id=workspace_id,
        )
    except SdkArtifactError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail
        ) from exc


@router.post("/artifacts/spreadsheet")
async def sdk_render_spreadsheet_artifact(
    request: SpreadsheetArtifactSpec,
    current_user: CurrentUser,
    workspace_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Render and store a trusted XLSX artifact."""
    from shared.sdk_artifact_generation import (
        sdk_render_spreadsheet_artifact as _render_spreadsheet,
    )
    from shared.sdk_artifacts import ArtifactCaller

    return await _render_spreadsheet(
        ArtifactCaller(user=current_user, db=db),
        spec=request,
        workspace_id=workspace_id,
    )


@router.post("/artifacts/text")
async def sdk_render_text_artifact(
    request: TextArtifactSpec,
    current_user: CurrentUser,
    workspace_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Render and store a trusted text-family artifact."""
    from shared.sdk_artifact_generation import (
        sdk_render_text_artifact as _render_text,
    )
    from shared.sdk_artifacts import ArtifactCaller

    return await _render_text(
        ArtifactCaller(user=current_user, db=db),
        spec=request,
        workspace_id=workspace_id,
    )


@router.post("/artifacts/image")
async def sdk_generate_image_artifact(
    request: ImageArtifactSpec,
    current_user: CurrentUser,
    workspace_id: UUID | None = None,
    execution_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Generate and store an image with the configured provider."""
    from shared.sdk_artifact_generation import (
        sdk_generate_image_artifact as _generate_image,
    )
    from shared.sdk_artifacts import ArtifactCaller

    return await _generate_image(
        ArtifactCaller(user=current_user, db=db),
        spec=request,
        workspace_id=workspace_id,
        execution_id=execution_id,
    )


@router.post(
    "/artifacts/video",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sdk_generate_video_artifact(
    request: VideoArtifactSpec,
    current_user: CurrentUser,
    response: Response,
    workspace_id: UUID | None = None,
    execution_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> PlatformJobAccepted:
    """Queue durable video generation into canonical artifact storage."""
    from shared.sdk_video import (
        enqueue_sdk_video_job,
        finalize_sdk_video_job,
        sdk_video_job_accepted,
    )

    job, reused = await enqueue_sdk_video_job(
        db,
        current_user,
        spec=request,
        workspace_id=workspace_id,
        execution_id=execution_id,
    )
    await finalize_sdk_video_job(db, job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return sdk_video_job_accepted(job, reused)


@router.get("/artifacts/{artifact_id}/content")
async def sdk_read_artifact(
    artifact_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    preview: bool = False,
) -> Response:
    """Read an opaque artifact after enforcing caller scope."""
    from shared.sdk_artifacts import (
        ArtifactCaller,
        SdkArtifactError,
        sdk_read_artifact as _read,
    )

    try:
        result = await _read(
            ArtifactCaller(user=current_user, db=db),
            artifact_id=artifact_id,
            preview=preview,
        )
    except SdkArtifactError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail
        ) from exc
    if result.preview_html is not None:
        return Response(
            content=result.preview_html,
            media_type="text/html",
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )
    headers = {"X-Content-Type-Options": "nosniff"}
    if result.content_disposition is not None:
        headers["Content-Disposition"] = result.content_disposition
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers=headers,
    )


@router.get("/artifacts/{artifact_id}/download-url")
async def sdk_artifact_download_url(
    artifact_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ArtifactDownloadResponse:
    """Create a short-lived download URL for an opaque artifact."""
    from shared.sdk_artifacts import (
        ArtifactCaller,
        SdkArtifactError,
        sdk_artifact_download_url as _download_url,
    )

    try:
        return await _download_url(
            ArtifactCaller(user=current_user, db=db),
            artifact_id=artifact_id,
        )
    except SdkArtifactError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail
        ) from exc


@router.post(
    "/ai/complete",
    summary="Generate AI completion",
)
async def cli_ai_complete(
    request: "CLIAICompleteRequest",
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> "CLIAICompleteResponse":
    """Generate an AI completion using platform-configured LLM.

    Thin HTTP adapter over the shared operation
    (``shared.sdk_ai.complete_sdk_ai``), which the engine-local
    dispatcher calls for the same inputs.
    """
    from src.models.contracts.cli import CLIAICompleteResponse

    from shared.sdk_ai import SdkAIError, complete_sdk_ai

    try:
        result = await complete_sdk_ai(
            db,
            current_user,
            messages=request.messages,
            max_tokens=request.max_tokens,
            model=request.model,
            profile=request.profile,
            execution_id=request.execution_id,
            scope=request.org_id,
            input_files=request.input_files,
        )
    except SdkAIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None

    return CLIAICompleteResponse(**result)


@router.post(
    "/ai/stream",
    summary="Stream AI completion",
)
async def cli_ai_stream(
    request: CLIAICompleteRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Generate a streaming AI completion using SSE.

    Thin HTTP adapter over the shared operation
    (``shared.sdk_ai.stream_sdk_ai``), which the engine-local
    dispatcher calls for the same inputs. Scope is resolved here —
    before headers are sent — so authorization failures stay HTTP
    status errors; everything after the stream starts surfaces as SSE
    error events. Each shared payload dict is serialized to one
    ``data:`` line, with the terminal ``[DONE]`` appended after the
    done payload.
    """
    from shared.sdk_ai import stream_sdk_ai

    # Resolve scope upfront against the authenticated user so the
    # streaming body doesn't have to re-derive bypass after CurrentUser
    # falls out of scope. 403/422 here stay HTTP errors (headers not
    # yet sent); later failures are SSE error events.
    resolved_org_id = await _resolve_sdk_org_id(current_user, request.org_id, db)

    async def sse():
        async for event in stream_sdk_ai(
            db,
            current_user,
            messages=request.messages,
            max_tokens=request.max_tokens,
            model=request.model,
            # The established HTTP stream endpoint always selected the
            # platform default profile. Keep that SDK-visible behavior;
            # worker-local callers may select a profile directly.
            execution_id=request.execution_id,
            resolved_org_id=resolved_org_id,
            input_files=request.input_files,
        ):
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("done") is True:
                yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get(
    "/ai/info",
    summary="Get AI model information",
)
async def cli_ai_info(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> "CLIAIInfoResponse":
    """Get information about the configured LLM.

    Thin HTTP adapter over the shared operation
    (``shared.sdk_ai.get_sdk_model_info``), which the engine-local
    dispatcher calls for the same inputs.
    """
    from src.models.contracts.cli import CLIAIInfoResponse

    from shared.sdk_ai import SdkAIError, get_sdk_model_info

    try:
        result = await get_sdk_model_info(db, current_user)
    except SdkAIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None

    return CLIAIInfoResponse(**result)


# =============================================================================
# SDK Knowledge Store Endpoints
# =============================================================================


def _deny_external_knowledge(current_user: UserPrincipal) -> None:
    """403 an external principal off the direct knowledge surface.

    The knowledge store has no grant axis (no roles, no access_level, no row
    policies), so its direct endpoints are implicitly "any signed-in user" —
    a tier external (portal/guest) users are excluded from. Externals reach
    knowledge content only THROUGH workflows/agents they were granted (the
    engine sentinel keeps the full cascade).
    """
    if current_user.is_external:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External users cannot access the knowledge store directly",
        )


@router.post(
    "/knowledge/store",
    summary="Store a document in knowledge store",
)
async def cli_knowledge_store(
    request: "CLIKnowledgeStoreRequest",
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Store a document with its embedding in the knowledge store."""
    _deny_external_knowledge(current_user)
    from shared.sdk_knowledge import SDKKnowledgeError, store_knowledge_document

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    org_uuid = UUID(org_id) if org_id else None

    try:
        return await store_knowledge_document(
            db,
            content=request.content,
            namespace=request.namespace,
            key=request.key,
            metadata=request.metadata,
            org_id=org_uuid,
            created_by=current_user.user_id,
        )
    except SDKKnowledgeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.post(
    "/knowledge/store-many",
    summary="Store multiple documents",
)
async def cli_knowledge_store_many(
    request: "CLIKnowledgeStoreManyRequest",
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Store multiple documents with batch embedding."""
    _deny_external_knowledge(current_user)
    from shared.sdk_knowledge import (
        SDKKnowledgeError,
        store_many_knowledge_documents,
    )

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    org_uuid = UUID(org_id) if org_id else None

    try:
        return await store_many_knowledge_documents(
            db,
            documents=request.documents,
            namespace=request.namespace,
            org_id=org_uuid,
            created_by=current_user.user_id,
        )
    except SDKKnowledgeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.post(
    "/knowledge/search",
    summary="Hybrid-search knowledge documents",
)
async def cli_knowledge_search(
    request: "CLIKnowledgeSearchRequest",
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[CLIKnowledgeDocumentResponse]:
    """Search knowledge using fused lexical and vector rankings."""
    _deny_external_knowledge(current_user)
    from shared.sdk_knowledge import SDKKnowledgeError, search_knowledge_documents

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    org_uuid = UUID(org_id) if org_id else None

    try:
        items = await search_knowledge_documents(
            db,
            query=request.query,
            namespace=request.namespace,
            limit=request.limit,
            min_score=request.min_score,
            metadata_filter=request.metadata_filter,
            fallback=request.fallback,
            org_id=org_uuid,
        )
    except SDKKnowledgeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None

    return [CLIKnowledgeDocumentResponse(**item) for item in items]


@router.post(
    "/knowledge/delete",
    summary="Delete a document by key",
)
async def cli_knowledge_delete(
    request: "CLIKnowledgeDeleteRequest",
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a document by key from the knowledge store."""
    _deny_external_knowledge(current_user)
    from shared.sdk_knowledge import SDKKnowledgeError, delete_knowledge_document

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    org_uuid = UUID(org_id) if org_id else None

    try:
        return await delete_knowledge_document(
            db,
            key=request.key,
            namespace=request.namespace,
            org_id=org_uuid,
        )
    except SDKKnowledgeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.delete(
    "/knowledge/namespace/{namespace}",
    summary="Delete all documents in namespace",
)
async def cli_knowledge_delete_namespace(
    namespace: str,
    scope: str | None = None,
    current_user: CurrentEngineOrBypassUser = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete all documents in a namespace."""
    _deny_external_knowledge(current_user)
    from shared.sdk_knowledge import SDKKnowledgeError, delete_knowledge_namespace

    try:
        org_id = await _resolve_sdk_org_id(current_user, scope, db)
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    org_uuid = UUID(org_id) if org_id else None

    try:
        return await delete_knowledge_namespace(
            db,
            namespace=namespace,
            org_id=org_uuid,
        )
    except SDKKnowledgeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.get(
    "/knowledge/namespaces",
    summary="List namespaces with document counts",
)
async def cli_knowledge_list_namespaces(
    scope: str | None = None,
    include_global: bool = True,
    current_user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
) -> list[CLIKnowledgeNamespaceInfo]:
    """List all namespaces with document counts per scope."""
    _deny_external_knowledge(current_user)
    from shared.sdk_knowledge import SDKKnowledgeError, list_knowledge_namespaces

    try:
        org_id = await _resolve_sdk_org_id(current_user, scope, db)
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    org_uuid = UUID(org_id) if org_id else None

    try:
        items = await list_knowledge_namespaces(
            db,
            org_id=org_uuid,
            include_global=include_global,
        )
    except SDKKnowledgeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None

    return [CLIKnowledgeNamespaceInfo(**item) for item in items]


@router.get(
    "/knowledge/get",
    summary="Get a document by key",
)
async def cli_knowledge_get(
    key: str,
    namespace: str = "default",
    scope: str | None = None,
    current_user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
) -> CLIKnowledgeDocumentResponse | None:
    """Get a document by key from the knowledge store."""
    _deny_external_knowledge(current_user)
    from shared.sdk_knowledge import SDKKnowledgeError, get_knowledge_document

    try:
        org_id = await _resolve_sdk_org_id(current_user, scope, db)
    except HTTPException:
        raise
    org_uuid = UUID(org_id) if org_id else None

    try:
        item = await get_knowledge_document(
            db,
            key=key,
            namespace=namespace,
            org_id=org_uuid,
        )
    except SDKKnowledgeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None

    return CLIKnowledgeDocumentResponse(**item)


# =============================================================================
# CLI Download (generates installable package)
# =============================================================================


@install_router.get(
    "/download",
    summary="Legacy CLI download URL",
    description="Redirect to the installer-compatible CLI download URL",
)
async def download_cli() -> RedirectResponse:
    """Preserve the historical URL for HTTP clients that follow redirects."""
    return RedirectResponse(
        url=f"/api/cli/download/{CLI_DOWNLOAD_ALIAS}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@install_router.get(
    f"/download/{CLI_DOWNLOAD_ALIAS}",
    summary="Download CLI package",
    description="Redirect to the versioned, pip-installable CLI artifact",
)
async def download_cli_alias() -> RedirectResponse:
    """Give uv/pipx a suffix-bearing URL before any network request occurs."""
    from shared.cli_artifact import cli_artifact_filename
    from shared.version import get_version

    filename = cli_artifact_filename(get_version())
    return RedirectResponse(
        url=f"/api/cli/artifacts/{filename}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@install_router.get(
    "/artifacts/{filename}",
    summary="Serve a versioned CLI artifact",
    description="Serve the immutable CLI artifact bundled into the API image",
)
async def download_cli_artifact(filename: str) -> FileResponse:
    """Serve only the artifact matching this API build."""
    from shared.cli_artifact import cli_artifact_filename
    from shared.version import get_version

    expected_filename = cli_artifact_filename(get_version())
    if filename != expected_filename:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CLI artifact not found",
        )

    artifact_path = CLI_ARTIFACT_DIR / expected_filename
    if not artifact_path.is_file():
        logger.error("Bundled CLI artifact is missing: %s", artifact_path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CLI artifact unavailable",
        )

    return FileResponse(
        path=artifact_path,
        media_type="application/gzip",
        filename=expected_filename,
    )


@router.get(
    "/download",
    summary="Download the bifrost web SDK package",
    description=(
        "Serve the `bifrost` web SDK as an npm-installable tarball. The Solution "
        "CLI installs it transiently from the selected instance for local "
        "development; the platform vendors its local SDK into server-side builds."
    ),
)
async def download_sdk() -> Response:
    """Build + serve the installable ``bifrost`` SDK package (npm tarball).

    Like ``/api/cli/download/bifrost-cli.tar.gz`` (the Python CLI tarball), this
    package is tied to the API build. The web SDK remains bundled on demand from
    source shipped in the image, while the Python CLI artifact is built into the
    image ahead of time.
    """
    from shared.version import get_version
    from src.services.sdk_package import build_sdk_tarball

    version = get_version()
    tarball = await asyncio.to_thread(build_sdk_tarball, version)
    return Response(
        content=tarball,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f"attachment; filename=bifrost-sdk-{version}.tgz",
        },
    )


# =============================================================================
# Tables SDK Endpoints
# =============================================================================


@router.post(
    "/tables/create",
    summary="Create a table",
)
async def cli_create_table(
    request: SDKTableCreateRequest,
    ctx: Context,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> SDKTableInfo:
    """Create a new table via SDK."""
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from shared.sdk_table_metadata import (
        SDKTableMetadataError,
        create_sdk_table,
        ensure_sdk_table_create_allowed,
    )
    from src.services.solution_scope import solution_context_id

    # A solution execution context (?solution= or X-Bifrost-App, resolved by
    # auth onto ctx) may not create tables ad hoc — tables are declared by the
    # solution manifest and created at deploy.
    solution_present = await solution_context_id(db, ctx) is not None

    # The historical endpoint returns the Solution restriction before it
    # examines a requested scope. Keep that ordering for malformed or
    # forbidden scopes as well as valid ones.
    try:
        ensure_sdk_table_create_allowed(solution_present)
    except SDKTableMetadataError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None

    try:
        org_uuid = await resolve_sdk_scope(
            request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    try:
        result = await create_sdk_table(
            db,
            name=request.name,
            table_schema=request.table_schema,
            description=request.description,
            org_id=org_uuid,
            actor_email=current_user.email,
            solution_present=solution_present,
        )
    except SDKTableMetadataError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    return SDKTableInfo(**result)


@router.post(
    "/tables/list",
    summary="List tables",
)
async def cli_list_tables(
    request: SDKTableListRequest,
    current_user: CurrentEngineOrBypassUser,
    db: AsyncSession = Depends(get_db),
) -> list[SDKTableInfo]:
    """List tables via SDK.

    Engine sentinel: the SDK has already resolved scope, so non-external
    principals get is_superuser=True and we trust the org_uuid. The base
    class handles the cascade (org + global) for us. EXTERNAL principals
    do not inherit sentinel trust (OPEN-B) — they get the normal user
    cascade (org + global table names/schemas; row data is policy-gated).
    """
    # Local import keeps the router file's top-level imports lean.
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from shared.sdk_table_metadata import list_sdk_tables

    try:
        org_uuid = await resolve_sdk_scope(
            request.scope,
            caller_org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            session=db,
        )
    except ScopeResolutionError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    items = await list_sdk_tables(
        db,
        org_id=org_uuid,
        external=current_user.is_external,
    )

    return [SDKTableInfo(**item) for item in items]
