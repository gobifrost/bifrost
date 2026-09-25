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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context, CurrentUser
from src.core.principal import UserPrincipal
from src.core.database import get_db
from src.core.log_safety import log_safe
from src.models import Organization
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
    CLIRegisteredWorkflow,
    CLISessionContinueRequest,
    CLISessionContinueResponse,
    CLISessionExecutionSummary,
    CLISessionListResponse,
    CLISessionLogRequest,
    CLISessionPendingResponse,
    CLISessionRegisterRequest,
    CLISessionResponse,
    CLISessionResultRequest,
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
    publish_cli_session_update,
    publish_execution_log,
    publish_execution_update,
    publish_history_update,
)
from src.repositories.cli_sessions import CLISessionRepository

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
# Helper Functions
# =============================================================================


def _session_to_response(
    session,
    is_connected: bool,
) -> CLISessionResponse:
    """Convert CLISession ORM to response model."""
    from sqlalchemy import inspect as sa_inspect

    executions = []
    # Use SQLAlchemy inspect to check if executions were eagerly loaded
    # This avoids triggering a lazy load which would fail in async context
    state = sa_inspect(session)
    if "executions" in state.dict and state.dict["executions"]:
        for ex in sorted(
            state.dict["executions"], key=lambda e: e.created_at, reverse=True
        )[:10]:
            executions.append(
                CLISessionExecutionSummary(
                    id=str(ex.id),
                    workflow_name=ex.workflow_name,
                    status=ex.status.value
                    if hasattr(ex.status, "value")
                    else str(ex.status),
                    created_at=ex.created_at,
                    duration_ms=ex.duration_ms,
                )
            )

    workflows = []
    if session.workflows:
        for w in session.workflows:
            workflows.append(
                CLIRegisteredWorkflow(
                    name=w.get("name", ""),
                    description=w.get("description", ""),
                    parameters=w.get("parameters", []),
                )
            )

    return CLISessionResponse(
        id=str(session.id),
        user_id=str(session.user_id),
        file_path=session.file_path,
        workflows=workflows,
        selected_workflow=session.selected_workflow,
        params=session.params,
        pending=session.pending,
        last_seen=session.last_seen,
        created_at=session.created_at,
        is_connected=is_connected,
        executions=executions,
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
    which the engine-local dispatcher can call with the same inputs.
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

    The C2 gate: platform admins (``is_superuser``) AND provider-org members
    can bypass scope restrictions. The caller's "own org" is sourced from
    the auth-verified ``current_user.organization_id`` — never from a
    mutable per-user default. Provider-org membership is checked by a
    single ``SELECT is_provider`` against the caller's org, only when the
    requested scope is not UNSET / not the caller's own org.

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
    from fastapi import HTTPException, status
    from shared.scope_resolver import (
        UNSET,
        ScopeNotAllowed,
        resolve_effective_scope,
    )

    # Parse the requested scope into the resolver's input domain.
    requested: object
    if scope is None or scope == "":
        # Empty string preserved as "unset" for backwards compat with
        # CLI clients that pass `--scope ''` to mean "use my default."
        requested = UNSET
    elif scope == "global":
        requested = None
    else:
        try:
            requested = UUID(scope)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"scope must be 'global', a UUID, or null; got {scope!r}",
            ) from None

    caller_org_id: UUID | None = current_user.organization_id
    is_platform_admin = current_user.is_superuser

    # Provider-org membership is only needed if the caller is requesting
    # something other than UNSET / their own org. UNSET resolves to
    # caller_org_id without any bypass check.
    is_provider_org = False
    needs_bypass_check = requested is not UNSET and requested != caller_org_id
    if needs_bypass_check and not is_platform_admin and caller_org_id is not None:
        org_row = await db.execute(
            select(Organization.is_provider).where(Organization.id == caller_org_id)
        )
        is_provider_org = bool(org_row.scalar_one_or_none())

    try:
        resolved = resolve_effective_scope(
            caller_org_id=caller_org_id,
            is_platform_admin=is_platform_admin,
            is_provider_org=is_provider_org,
            requested_scope=requested,  # type: ignore[arg-type]
        )
    except ScopeNotAllowed as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from None

    return str(resolved) if resolved is not None else None


@router.post(
    "/config/get",
    response_model=CLIConfigValue | None,
    summary="Get config value",
)
async def cli_get_config(
    request: CLIConfigGetRequest,
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    engine-local dispatcher calls for the same inputs.
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
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsListMappingsResponse | None:
    """List all mappings for an integration via SDK.

    Scope resolution and response construction live in the shared
    integrations service (``shared.sdk_integrations``), which the
    engine-local dispatcher calls for the same inputs.
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
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsMappingItem | None:
    """Get a specific integration mapping by org_id or entity_id via SDK.

    Scope resolution and response construction live in the shared
    integrations service (``shared.sdk_integrations``), which the
    engine-local dispatcher calls for the same inputs.
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
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsMappingItem:
    """Create or update an integration mapping for an organization via SDK.

    Mutation rules live in the shared integrations service
    (``shared.sdk_integrations``), which the engine-local dispatcher calls
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
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete an integration mapping for an organization via SDK.

    Mutation rules live in the shared integrations service
    (``shared.sdk_integrations``), which the engine-local dispatcher calls
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
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SDKIntegrationsRefreshTokenResponse:
    """Programmatically refresh an OAuth token for an integration.

    For client_credentials flows: fetches a fresh token from the provider.
    For authorization_code flows: uses the stored refresh_token to get a new access token.

    The new token is persisted to the database so subsequent integrations.get() calls
    also benefit from the refreshed token.

    The refresh rules live in the shared integrations service
    (``shared.sdk_integrations``), which the engine-local dispatcher calls
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
# CLI Session Endpoints (Database-backed)
# =============================================================================


@router.post(
    "/sessions",
    summary="Register/create a CLI session",
    response_model=CLISessionResponse,
)
async def register_cli_session(
    request: CLISessionRegisterRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> CLISessionResponse:
    """
    Register workflows discovered by CLI for web UI.

    Called by `bifrost run <file>` to register workflows before
    opening the browser to the CLI session page.
    """
    repo = CLISessionRepository(db)

    # Convert workflows to dict format for storage
    workflows_data = [
        {
            "name": w.name,
            "description": w.description,
            "parameters": [
                p.model_dump() if hasattr(p, "model_dump") else p for p in w.parameters
            ],
        }
        for w in request.workflows
    ]

    session = await repo.create_session(
        session_id=UUID(request.session_id),
        user_id=current_user.user_id,
        file_path=request.file_path,
        workflows=workflows_data,
        selected_workflow=request.selected_workflow,
    )
    await db.commit()

    logger.info(
        f"CLI session registered: {len(request.workflows)} workflows from {log_safe(request.file_path)} "
        f"for user {current_user.email}, session_id={log_safe(request.session_id)}"
    )

    response = _session_to_response(session, is_connected=True)

    # Broadcast state update via websocket
    await publish_cli_session_update(
        str(current_user.user_id), request.session_id, response.model_dump(mode="json")
    )

    return response


@router.get(
    "/sessions",
    summary="List user's CLI sessions",
    response_model=CLISessionListResponse,
)
async def list_cli_sessions(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> CLISessionListResponse:
    """List all CLI sessions for the current user."""
    repo = CLISessionRepository(db)
    sessions = await repo.get_user_sessions(current_user.user_id)

    return CLISessionListResponse(
        sessions=[
            _session_to_response(s, is_connected=repo.is_connected(s)) for s in sessions
        ]
    )


@router.get(
    "/sessions/{session_id}",
    summary="Get CLI session state",
    response_model=CLISessionResponse,
)
async def get_cli_session(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> CLISessionResponse:
    """Get current CLI session state for web UI."""
    repo = CLISessionRepository(db)

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    session = await repo.get_session_for_user(session_uuid, current_user.user_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return _session_to_response(session, is_connected=repo.is_connected(session))


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete CLI session",
)
async def delete_cli_session(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a CLI session."""
    repo = CLISessionRepository(db)

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    session = await repo.get_session_for_user(session_uuid, current_user.user_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await repo.delete(session)
    await db.commit()

    logger.info(
        f"CLI session deleted: {log_safe(session_id)} for user {current_user.email}"
    )


@router.post(
    "/sessions/{session_id}/continue",
    summary="Continue workflow execution",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CLISessionContinueResponse,
)
async def continue_cli_session(
    session_id: str,
    request: CLISessionContinueRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> CLISessionContinueResponse:
    """
    Submit parameters to continue workflow execution.

    Called by web UI when user clicks "Continue".
    Creates a real Execution record and sets pending=True so CLI can pick up.
    """
    from src.repositories.executions import ExecutionRepository
    from src.models.enums import ExecutionStatus

    repo = CLISessionRepository(db)

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    session = await repo.get_session_for_user(session_uuid, current_user.user_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active CLI session. Run `bifrost run <file>` first.",
        )

    # Validate workflow exists
    workflow_names = [w.get("name") for w in session.workflows]
    if request.workflow_name not in workflow_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow '{request.workflow_name}' not found. Available: {workflow_names}",
        )

    # Resolve workflow ID from name
    from src.models.orm.workflows import Workflow as WorkflowORM

    wf_result = await db.execute(
        select(WorkflowORM.id).where(WorkflowORM.name == request.workflow_name).limit(1)
    )
    wf_row = wf_result.scalar_one_or_none()
    resolved_workflow_id = str(wf_row) if wf_row else None

    # Create a real Execution record
    execution_id = str(uuid4())
    exec_repo = ExecutionRepository(db)

    await exec_repo.create_execution(
        execution_id=execution_id,
        workflow_name=request.workflow_name,
        parameters=request.params,
        org_id=str(current_user.organization_id)
        if current_user.organization_id
        else None,
        user_id=str(current_user.user_id),
        user_name=current_user.name or current_user.email,
        status=ExecutionStatus.PENDING,
        is_local_execution=True,
        workflow_id=resolved_workflow_id,
    )

    # Link execution to session
    from src.models.orm import Execution

    stmt = select(Execution).where(Execution.id == UUID(execution_id))
    result = await db.execute(stmt)
    execution = result.scalar_one_or_none()
    if execution:
        execution.session_id = session_uuid
        await db.flush()  # Ensure session_id is persisted for history page icon

    # Update session state
    await repo.set_pending(
        session_uuid,
        request.workflow_name,
        request.params,
    )
    await db.commit()

    logger.info(
        f"CLI session continue: workflow={log_safe(request.workflow_name)}, "
        f"execution_id={execution_id}, session_id={log_safe(session_id)}, user={current_user.email}"
    )

    # Broadcast state update via websocket
    updated_session = await repo.get_session_with_executions(session_uuid)
    if updated_session:
        response_data = _session_to_response(
            updated_session, is_connected=repo.is_connected(updated_session)
        )
        await publish_cli_session_update(
            str(current_user.user_id), session_id, response_data.model_dump(mode="json")
        )

    return CLISessionContinueResponse(
        status="pending",
        execution_id=execution_id,
        workflow=request.workflow_name,
    )


@router.get(
    "/sessions/{session_id}/pending",
    summary="Poll for pending execution",
    response_model=CLISessionPendingResponse,
    responses={204: {"description": "No pending execution"}},
)
async def get_pending_execution(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> CLISessionPendingResponse | Response:
    """
    Poll for pending workflow execution.

    Returns 204 No Content if no execution pending.
    Returns execution_id, params and clears pending flag when execution is ready.
    """
    from src.repositories.executions import ExecutionRepository
    from src.models.enums import ExecutionStatus

    repo = CLISessionRepository(db)

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    session = await repo.get_session_for_user(session_uuid, current_user.user_id)

    if session is None or not session.pending:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    workflow_name = session.selected_workflow
    params = session.params

    if workflow_name is None or params is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Find the most recent pending execution for this session
    from src.models.orm import Execution

    stmt = (
        select(Execution)
        .where(
            Execution.session_id == session_uuid,
            Execution.status == ExecutionStatus.PENDING,
        )
        .order_by(Execution.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    execution = result.scalar_one_or_none()

    if not execution:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    execution_id = str(execution.id)

    # Update execution status to RUNNING
    exec_repo = ExecutionRepository(db)
    await exec_repo.update_execution(
        execution_id=execution_id,
        status=ExecutionStatus.RUNNING,
    )

    # Clear pending and update last_seen
    await repo.clear_pending(session_uuid)
    await repo.update_last_seen(session_uuid)
    await db.commit()

    # Broadcast execution status update
    await publish_execution_update(execution_id, "Running")
    await publish_history_update(
        execution_id=execution_id,
        status="Running",
        executed_by=execution.executed_by,
        executed_by_name=execution.executed_by_name,
        workflow_name=workflow_name,
        org_id=execution.organization_id,
        started_at=execution.started_at,
    )

    logger.info(
        f"CLI session pending picked up: workflow={log_safe(workflow_name)}, execution_id={execution_id}, session_id={log_safe(session_id)}"
    )

    # Broadcast session state update
    updated_session = await repo.get_session_with_executions(session_uuid)
    if updated_session:
        response_data = _session_to_response(
            updated_session, is_connected=repo.is_connected(updated_session)
        )
        await publish_cli_session_update(
            str(current_user.user_id), session_id, response_data.model_dump(mode="json")
        )

    return CLISessionPendingResponse(
        execution_id=execution_id,
        workflow_name=workflow_name,
        params=params,
    )


@router.post(
    "/sessions/{session_id}/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Update session heartbeat",
)
async def session_heartbeat(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Update session's last_seen timestamp (CLI heartbeat)."""
    repo = CLISessionRepository(db)

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    session = await repo.get_session_for_user(session_uuid, current_user.user_id)
    if session:
        await repo.update_last_seen(session_uuid)
        await db.commit()


@router.post(
    "/sessions/{session_id}/executions/{execution_id}/log",
    summary="Stream log entry from CLI",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def post_cli_log(
    session_id: str,
    execution_id: str,
    request: CLISessionLogRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Stream a log entry from CLI to the execution."""
    from src.models.orm import Execution

    try:
        exec_uuid = UUID(execution_id)
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID format",
        )

    stmt = select(Execution).where(
        Execution.id == exec_uuid,
        Execution.session_id == session_uuid,
        Execution.executed_by == current_user.user_id,
        Execution.is_local_execution == True,  # noqa: E712
    )
    result = await db.execute(stmt)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found or not authorized",
        )

    timestamp = None
    if request.timestamp:
        try:
            # Parse timestamp and strip timezone to match engine behavior
            # (engine uses datetime.now(timezone.utc) which is timezone-naive)
            ts = datetime.fromisoformat(request.timestamp.replace("Z", "+00:00"))
            timestamp = ts.replace(tzinfo=None) if ts.tzinfo else ts
        except ValueError:
            timestamp = datetime.now(timezone.utc)

    try:
        # Use unified log function - same as workflow engine
        # Writes to Redis Stream (for persistence) AND publishes to PubSub (for WebSocket)
        from bifrost._logging import log_and_broadcast_async

        await log_and_broadcast_async(
            execution_id=execution_id,
            level=request.level,
            message=request.message,
            metadata=request.metadata,
            timestamp=timestamp,
        )
    except ImportError:
        logger.warning(
            f"Log streaming not available, log skipped: {log_safe(request.message)}"
        )


@router.post(
    "/sessions/{session_id}/executions/{execution_id}/result",
    summary="Post execution result from CLI",
    status_code=status.HTTP_200_OK,
)
async def post_cli_result(
    session_id: str,
    execution_id: str,
    request: CLISessionResultRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Post execution result from CLI."""
    from src.models.orm import Execution
    from src.models.enums import ExecutionStatus
    from src.repositories.executions import ExecutionRepository

    try:
        exec_uuid = UUID(execution_id)
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID format",
        )

    stmt = select(Execution).where(
        Execution.id == exec_uuid,
        Execution.session_id == session_uuid,
        Execution.executed_by == current_user.user_id,
        Execution.is_local_execution == True,  # noqa: E712
    )
    result = await db.execute(stmt)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found or not authorized",
        )

    if request.status.lower() in ("success", "completed"):
        status_enum = ExecutionStatus.SUCCESS
    else:
        status_enum = ExecutionStatus.FAILED

    repo = ExecutionRepository(db)
    await repo.update_execution(
        execution_id=execution_id,
        status=status_enum,
        result=request.result,
        error_message=request.error_message,
        duration_ms=request.duration_ms,  # completed_at is set automatically when duration_ms is provided
    )

    # Persist logs directly from request (avoids race conditions)
    logs_persisted = 0
    if request.logs:
        from src.models.orm import ExecutionLog

        logs_to_insert = []
        for seq, log in enumerate(request.logs):
            try:
                # Parse timestamp, strip timezone for DB
                if log.timestamp:
                    ts = datetime.fromisoformat(log.timestamp)
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                else:
                    ts = datetime.now(timezone.utc)

                log_entry = ExecutionLog(
                    execution_id=exec_uuid,
                    level=log.level.upper(),
                    message=log.message,
                    log_metadata=log.metadata,
                    timestamp=ts,
                    sequence=seq,
                )
                logs_to_insert.append(log_entry)

                # Also broadcast to WebSocket for real-time UI update
                await publish_execution_log(
                    execution_id,
                    log.level,
                    log.message,
                    {"metadata": log.metadata, "timestamp": ts.isoformat()},
                )
            except Exception as e:
                logger.warning(f"Failed to process log entry: {log_safe(e)}")
                continue

        if logs_to_insert:
            db.add_all(logs_to_insert)
            logs_persisted = len(logs_to_insert)
            logger.debug(
                f"Persisted {logs_persisted} logs directly for CLI execution {log_safe(execution_id)}"
            )

    await db.commit()

    # Fallback: flush any logs from Redis Stream (backwards compatibility)
    logs_flushed = 0
    if not request.logs:
        try:
            from bifrost._logging import flush_logs_to_postgres

            logs_flushed = await flush_logs_to_postgres(execution_id)
            if logs_flushed > 0:
                logger.debug(
                    f"Flushed {logs_flushed} logs from stream for CLI execution {log_safe(execution_id)}"
                )
        except ImportError as e:
            # bifrost._logging optional (CLI bundle may not include it) — skip stream flush
            logger.debug(f"bifrost._logging not available, skipping stream flush: {e}")
        except Exception as e:
            logger.warning(f"Failed to flush logs from stream: {log_safe(e)}")

    # Match workflow engine format exactly for unified UI handling
    update_data: dict[str, Any] = {
        "result": request.result,
        "durationMs": request.duration_ms if request.duration_ms else 0,
    }
    if request.error_message:
        update_data["error"] = request.error_message

    await publish_execution_update(
        execution_id,
        status_enum.value,
        update_data,
    )
    await publish_history_update(
        execution_id=execution_id,
        status=status_enum.value,
        executed_by=execution.executed_by,
        executed_by_name=execution.executed_by_name,
        workflow_name=execution.workflow_name,
        org_id=execution.organization_id,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        duration_ms=request.duration_ms or 0,
    )

    # Broadcast updated session state
    session_repo = CLISessionRepository(db)
    updated_session = await session_repo.get_session_with_executions(session_uuid)
    if updated_session:
        response_data = _session_to_response(
            updated_session, is_connected=session_repo.is_connected(updated_session)
        )
        await publish_cli_session_update(
            str(current_user.user_id), session_id, response_data.model_dump(mode="json")
        )

    total_logs = logs_persisted + logs_flushed
    logger.info(
        f"CLI result: execution_id={log_safe(execution_id)}, session_id={log_safe(session_id)}, status={status_enum.value}, "
        f"logs_persisted={logs_persisted}, logs_flushed={logs_flushed}, user={current_user.email}"
    )

    return {
        "status": status_enum.value,
        "logs_persisted": logs_persisted,
        "logs_flushed": logs_flushed,
        "total_logs": total_logs,
    }


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

    from shared.artifact_generation import (
        generate_document,
        generate_document_with_images,
    )
    from src.services.artifacts import ArtifactService, artifact_ref

    service = ArtifactService(db)
    image_content: dict[str, bytes] = {}
    if workspace_id is not None:
        for image in (
            image for section in request.sections for image in section.images
        ):
            stored_image = await service.resolve_workspace_path(
                workspace_id,
                image.path,
                user_id=current_user.user_id,
                organization_id=current_user.organization_id,
                is_platform_admin=current_user.is_platform_admin,
            )
            if not stored_image.content_type.startswith("image/"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{image.path} is not an image artifact.",
                )
            image_content[image.path] = await service.read(stored_image)
    generated = await asyncio.to_thread(
        generate_document_with_images if image_content else generate_document,
        request,
        *([image_content] if image_content else []),
    )
    artifact = await service.store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=current_user.user_id,
        organization_id=current_user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    return artifact_ref(artifact)


@router.post("/artifacts/spreadsheet")
async def sdk_render_spreadsheet_artifact(
    request: SpreadsheetArtifactSpec,
    current_user: CurrentUser,
    workspace_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Render and store a trusted XLSX artifact."""

    from shared.artifact_generation import generate_spreadsheet
    from src.services.artifacts import ArtifactService, artifact_ref

    generated = await asyncio.to_thread(generate_spreadsheet, request)
    artifact = await ArtifactService(db).store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=current_user.user_id,
        organization_id=current_user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    return artifact_ref(artifact)


@router.post("/artifacts/text")
async def sdk_render_text_artifact(
    request: TextArtifactSpec,
    current_user: CurrentUser,
    workspace_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Render and store a trusted text-family artifact."""

    from shared.artifact_generation import generate_text
    from src.services.artifacts import ArtifactService, artifact_ref

    generated = await asyncio.to_thread(generate_text, request)
    artifact = await ArtifactService(db).store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=current_user.user_id,
        organization_id=current_user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    return artifact_ref(artifact)


@router.post("/artifacts/image")
async def sdk_generate_image_artifact(
    request: ImageArtifactSpec,
    current_user: CurrentUser,
    workspace_id: UUID | None = None,
    execution_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ArtifactRef:
    """Generate and store an image with the configured provider."""

    from src.services.artifacts import ArtifactService, artifact_ref
    from src.services.media_generation import generate_image, record_media_usage

    generated = await generate_image(
        db,
        filename=request.filename,
        prompt=request.prompt,
    )
    artifact = await ArtifactService(db).store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=current_user.user_id,
        organization_id=current_user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    await record_media_usage(
        db,
        generated,
        execution_id=execution_id,
        organization_id=current_user.organization_id,
        user_id=current_user.user_id,
    )
    return artifact_ref(artifact)


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
    from src.jobs.platform.video_generation import (
        SDK_VIDEO_GENERATION_DEFINITION,
        SDKVideoGenerationPayload,
    )
    from src.services.platform_jobs import (
        enqueue_platform_job,
        ensure_platform_job_notification,
        publish_platform_job_update,
    )

    job, reused = await enqueue_platform_job(
        db,
        SDK_VIDEO_GENERATION_DEFINITION,
        SDKVideoGenerationPayload(
            filename=request.filename,
            prompt=request.prompt,
            workspace_id=workspace_id,
            execution_id=execution_id,
        ),
        dedupe_key=None,
        organization_id=current_user.organization_id,
        requested_by_user_id=current_user.user_id,
        requested_by_email=current_user.email,
        requested_by_name=current_user.name or current_user.email,
        resource_type="artifact",
        resource_id=request.filename,
        title=f"Generating {request.filename}",
        action_url=None,
    )
    try:
        await ensure_platform_job_notification(db, job)
    except Exception:
        logger.warning(
            "SDK video generation queued without a progress notification",
            extra={"platform_job_id": str(job.id)},
            exc_info=True,
        )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        notification_id=job.notification_id,
        status=job.status,
        reused=reused,
    )


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
    """Generate an AI completion using platform-configured LLM."""
    from src.models.contracts.cli import CLIAICompleteResponse
    import base64

    from src.services.llm import LLMInputFile, LLMMessage, get_llm_client

    try:
        client = await get_llm_client(db, profile_name=request.profile)

        # Convert to LLMMessage objects
        llm_messages = [
            LLMMessage(role=msg["role"], content=msg["content"])  # type: ignore[arg-type]
            for msg in request.messages
        ]
        if request.input_files:
            user_message = next(
                (
                    message
                    for message in reversed(llm_messages)
                    if message.role == "user"
                ),
                None,
            )
            if user_message is None:
                raise ValueError("AI file inputs require a user message.")
            user_message.input_files = [
                LLMInputFile(
                    filename=item.filename,
                    media_type=item.content_type,
                    data=base64.b64decode(item.data_base64, validate=True),
                )
                for item in request.input_files
            ]

        response = await client.complete(
            messages=llm_messages,
            max_tokens=request.max_tokens,
            model=request.model,
        )

        logger.info(
            f"CLI AI complete: model={log_safe(response.model)}, tokens={response.input_tokens}/{response.output_tokens}"
        )

        # Record AI usage
        try:
            from src.services.ai_usage_service import record_ai_usage
            from src.core.cache import get_shared_redis

            redis_client = await get_shared_redis()
            org_id = await _resolve_sdk_org_id(current_user, request.org_id, db)
            await record_ai_usage(
                session=db,
                redis_client=redis_client,
                provider=client.provider_name,
                model=response.model or client.model_name,
                input_tokens=response.input_tokens or 0,
                output_tokens=response.output_tokens or 0,
                cache_read_tokens=response.cache_read_tokens,
                cache_write_tokens=response.cache_write_tokens,
                provider_cost=response.provider_cost,
                execution_id=UUID(request.execution_id)
                if request.execution_id
                else None,
                organization_id=UUID(org_id) if org_id else None,
                user_id=current_user.user_id,
            )
        except Exception as e:
            logger.warning(f"Failed to record AI usage: {log_safe(e)}")

        return CLIAICompleteResponse(
            content=response.content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            model=response.model,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except Exception as e:
        # Check for authentication errors from LLM providers
        error_type = type(e).__name__
        error_module = type(e).__module__
        if error_type == "AuthenticationError" and error_module in (
            "anthropic",
            "openai",
        ):
            provider = "Anthropic" if error_module == "anthropic" else "OpenAI"
            logger.error(
                f"CLI AI complete failed: {provider} authentication error - invalid API key"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"{provider} API key is invalid or expired. Please update the API key in System Settings > AI Configuration.",
            )
        logger.error(f"CLI AI complete failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI completion failed. See server logs for details.",
        )


@router.post(
    "/ai/stream",
    summary="Stream AI completion",
)
async def cli_ai_stream(
    request: CLIAICompleteRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Generate a streaming AI completion using SSE."""
    import base64

    from src.services.llm import LLMInputFile, LLMMessage, get_llm_client

    # Capture context for usage recording. Resolve scope upfront against
    # the authenticated user so the streaming closure doesn't have to
    # re-derive bypass after CurrentUser falls out of scope.
    user_id = current_user.user_id
    resolved_org_id = await _resolve_sdk_org_id(current_user, request.org_id, db)
    execution_id_str = request.execution_id

    async def generate():
        try:
            client = await get_llm_client(db)

            # Convert to LLMMessage objects
            llm_messages = [
                LLMMessage(role=msg["role"], content=msg["content"])  # type: ignore[arg-type]
                for msg in request.messages
            ]
            if request.input_files:
                user_message = next(
                    (
                        message
                        for message in reversed(llm_messages)
                        if message.role == "user"
                    ),
                    None,
                )
                if user_message is None:
                    raise ValueError("AI file inputs require a user message.")
                user_message.input_files = [
                    LLMInputFile(
                        filename=item.filename,
                        media_type=item.content_type,
                        data=base64.b64decode(item.data_base64, validate=True),
                    )
                    for item in request.input_files
                ]

            async for chunk in client.stream(
                messages=llm_messages,
                max_tokens=request.max_tokens,
                model=request.model,
            ):
                if chunk.type == "delta":
                    yield f"data: {json.dumps({'content': chunk.content})}\n\n"
                elif chunk.type == "done":
                    yield f"data: {json.dumps({'done': True, 'input_tokens': chunk.input_tokens, 'output_tokens': chunk.output_tokens})}\n\n"
                    yield "data: [DONE]\n\n"

                    # Record AI usage after stream completes
                    try:
                        from src.services.ai_usage_service import record_ai_usage
                        from src.core.cache import get_shared_redis

                        redis_client = await get_shared_redis()
                        await record_ai_usage(
                            session=db,
                            redis_client=redis_client,
                            provider=client.provider_name,
                            model=client.model_name,
                            input_tokens=chunk.input_tokens or 0,
                            output_tokens=chunk.output_tokens or 0,
                            cache_read_tokens=chunk.cache_read_tokens,
                            cache_write_tokens=chunk.cache_write_tokens,
                            provider_cost=chunk.provider_cost,
                            execution_id=UUID(execution_id_str)
                            if execution_id_str
                            else None,
                            organization_id=UUID(resolved_org_id)
                            if resolved_org_id
                            else None,
                            user_id=user_id,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to record AI usage: {log_safe(e)}")
                elif chunk.type == "error":
                    yield f"data: {json.dumps({'error': chunk.error})}\n\n"
                    break
        except ValueError as e:
            logger.warning(f"CLI AI stream rejected: {e}")
            yield f"data: {json.dumps({'error': 'AI stream is unavailable. See server logs for details.'})}\n\n"
        except Exception as e:
            # Check for authentication errors from LLM providers
            error_type = type(e).__name__
            error_module = type(e).__module__
            if error_type == "AuthenticationError" and error_module in (
                "anthropic",
                "openai",
            ):
                provider = "Anthropic" if error_module == "anthropic" else "OpenAI"
                logger.error(
                    f"CLI AI stream failed: {provider} authentication error - invalid API key"
                )
                yield f"data: {json.dumps({'error': f'{provider} API key is invalid or expired. Please update the API key in System Settings > AI Configuration.'})}\n\n"
            else:
                logger.error(f"CLI AI stream failed: {log_safe(e)}")
                yield f"data: {json.dumps({'error': 'AI stream failed. See server logs for details.'})}\n\n"

    return StreamingResponse(
        generate(),
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
    """Get information about the configured LLM."""
    from src.models.contracts.cli import CLIAIInfoResponse
    from src.services.llm.factory import get_llm_config

    try:
        config = await get_llm_config(db)

        return CLIAIInfoResponse(
            provider=config.provider,
            model=config.model,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


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
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    current_user: CurrentUser = None,
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
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
