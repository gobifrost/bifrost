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
import io
import json
import logging
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import CurrentUser
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
    SDKIntegrationsOAuthData,
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
from src.core.pubsub import publish_cli_session_update, publish_execution_log, publish_execution_update, publish_history_update
from src.repositories.cli_sessions import CLISessionRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sdk", tags=["SDK"])

# /api/cli/download is the permanent home for the CLI install
# endpoint. It lives at /api/cli/ (not /api/sdk/) because users type
# the URL — `pip install <SERVER_URL>/api/cli/download` — so the path
# is intentionally "the CLI." Not a legacy shim; not a compat carve-out.
# The rest of /api/cli/* moved to /api/sdk/* in the 2026-05 overhaul
# because those endpoints are the SDK execution surface, not the CLI.
install_router = APIRouter(prefix="/api/cli", tags=["CLI Install"])


# =============================================================================
# Helper Functions
# =============================================================================


def should_auto_refresh_token(
    provider: Any, entity_id: str | None, oauth_scope: str | None = None
) -> bool:
    """
    Determine if we should auto-fetch a fresh token instead of using stored token.

    Auto-refresh when:
    1. OAuth flow is client_credentials (not authorization_code)
    2. AND one of:
       a. Token URL contains {entity_id} placeholder AND entity_id is provided
       b. oauth_scope override is provided (different resource audience)

    This enables:
    - Multi-tenant client credentials where each tenant requires a different token endpoint
    - Same credentials used for different resources (Graph vs Exchange vs SharePoint)
    """
    if not provider:
        return False

    if not provider.token_url:
        return False

    # Only auto-refresh for client_credentials flow
    if provider.oauth_flow_type != "client_credentials":
        return False

    # Trigger auto-refresh if oauth_scope override is provided
    if oauth_scope:
        return True

    # Trigger auto-refresh if URL has {entity_id} placeholder and entity_id is provided
    if entity_id and "{entity_id}" in provider.token_url:
        return True

    return False


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
    default_parameters: dict = Field(default={}, description="Default workflow parameters")
    track_executions: bool = Field(default=True, description="Whether to track executions in history")


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
    if 'executions' in state.dict and state.dict['executions']:
        for ex in sorted(state.dict['executions'], key=lambda e: e.created_at, reverse=True)[:10]:
            executions.append(CLISessionExecutionSummary(
                id=str(ex.id),
                workflow_name=ex.workflow_name,
                status=ex.status.value if hasattr(ex.status, 'value') else str(ex.status),
                created_at=ex.created_at,
                duration_ms=ex.duration_ms,
            ))

    workflows = []
    if session.workflows:
        for w in session.workflows:
            workflows.append(CLIRegisteredWorkflow(
                name=w.get("name", ""),
                description=w.get("description", ""),
                parameters=w.get("parameters", []),
            ))

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
    """
    # Resolve which org to return.
    if org_id is not None and org_id != current_user.organization_id:
        # Explicit override of another org — C2 gate: platform admin or
        # provider-org member only. Provider-org membership is looked up
        # against the caller's own org's ``is_provider`` flag.
        is_provider_org = False
        if not current_user.is_superuser and current_user.organization_id is not None:
            row = await db.execute(
                select(Organization.is_provider).where(
                    Organization.id == current_user.organization_id
                )
            )
            is_provider_org = bool(row.scalar_one_or_none())
        if not (current_user.is_superuser or is_provider_org):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform admins or provider-org members can target another organization",
            )
        target_org_id = org_id
    elif org_id is not None:
        target_org_id = org_id
    else:
        target_org_id = current_user.organization_id

    org_data = None
    if target_org_id is not None:
        stmt = select(Organization).where(Organization.id == target_org_id)
        result = await db.execute(stmt)
        org = result.scalar_one_or_none()
        if org is None or not org.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization {target_org_id} not found or inactive",
            )
        org_data = {
            "id": str(org.id),
            "name": org.name,
            "is_active": org.is_active,
            "is_provider": org.is_provider,
        }

    return DeveloperContextResponse(
        user={
            "id": str(current_user.user_id),
            "email": current_user.email,
            "name": current_user.name,
            "is_superuser": current_user.is_superuser,
        },
        organization=org_data,
        default_parameters={},
        track_executions=True,
    )


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
    from src.repositories.config import ConfigRepository

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None

    # Canonical SDK config load: cascade (global + org-specific) merged.
    repo = ConfigRepository(db, org_id=org_uuid, is_superuser=True)
    all_config = await repo.merged_for_sdk()

    if request.key not in all_config:
        return None

    entry = all_config[request.key]
    raw_value = entry.get("value")
    config_type = entry.get("type", "string")

    if config_type == "secret" and raw_value:
        from src.core.security import decrypt_secret
        try:
            raw_value = decrypt_secret(raw_value)
        except Exception:
            raw_value = None
    elif config_type == "json" and isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as e:
            # Stored value is not valid JSON — return raw string as fallback
            logger.debug(f"config {log_safe(request.key)} stored as json but failed to parse, returning raw: {log_safe(e)}")
    elif config_type == "bool":
        raw_value = str(raw_value).lower() == "true" if isinstance(raw_value, str) else bool(raw_value)
    elif config_type == "int":
        try:
            raw_value = int(raw_value)
        except (ValueError, TypeError) as e:
            # Stored value isn't coercible to int — return raw value
            logger.debug(f"config {log_safe(request.key)} stored as int but failed to coerce, returning raw: {log_safe(e)}")

    return CLIConfigValue(
        key=request.key,
        value=raw_value,
        config_type=config_type,
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
    from src.models import Config as ConfigModel
    from src.models.enums import ConfigType as ConfigTypeEnum

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None
    now = datetime.now(timezone.utc)

    if request.is_secret:
        from src.core.security import encrypt_secret
        config_type = ConfigTypeEnum.SECRET
        stored_value = await asyncio.to_thread(encrypt_secret, str(request.value))
    elif isinstance(request.value, dict) or isinstance(request.value, list):
        config_type = ConfigTypeEnum.JSON
        stored_value = request.value
    elif isinstance(request.value, bool):
        config_type = ConfigTypeEnum.BOOL
        stored_value = request.value
    elif isinstance(request.value, int):
        config_type = ConfigTypeEnum.INT
        stored_value = request.value
    else:
        config_type = ConfigTypeEnum.STRING
        stored_value = request.value

    config_value = {"value": stored_value}

    stmt = select(ConfigModel).where(
        ConfigModel.key == request.key,
        ConfigModel.organization_id == org_uuid,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = config_value
        existing.config_type = config_type
        existing.updated_at = now
        existing.updated_by = current_user.email
    else:
        config = ConfigModel(
            key=request.key,
            value=config_value,
            config_type=config_type,
            organization_id=org_uuid,
            created_at=now,
            updated_at=now,
            updated_by=current_user.email,
        )
        db.add(config)

    await db.commit()

    try:
        from src.core.cache import upsert_config
        config_type_str = config_type.value
        await upsert_config(org_id, request.key, stored_value, config_type_str)
    except ImportError as e:
        # cache module is optional in some deploys; DB write already committed
        logger.debug(f"cache module unavailable, skipping config cache upsert: {e}")

    logger.info(f"CLI set config {log_safe(request.key)} for user {current_user.email}")


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
    from src.repositories.config import ConfigRepository

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None

    repo = ConfigRepository(db, org_id=org_uuid, is_superuser=True)
    all_config = await repo.merged_for_sdk()

    if not all_config:
        return {}

    config_dict: dict[str, Any] = {}
    for config_key, entry in all_config.items():
        raw_value = entry.get("value")
        config_type = entry.get("type", "string")

        if config_type == "secret":
            config_dict[config_key] = "[SECRET]"
        elif config_type == "json" and isinstance(raw_value, str):
            try:
                config_dict[config_key] = json.loads(raw_value)
            except json.JSONDecodeError:
                config_dict[config_key] = raw_value
        elif config_type == "bool":
            config_dict[config_key] = str(raw_value).lower() == "true" if isinstance(raw_value, str) else bool(raw_value)
        elif config_type == "int":
            try:
                config_dict[config_key] = int(raw_value)
            except (ValueError, TypeError):
                config_dict[config_key] = raw_value
        else:
            config_dict[config_key] = raw_value

    return config_dict


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
    from src.models import Config as ConfigModel

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None

    stmt = select(ConfigModel).where(
        ConfigModel.key == request.key,
        ConfigModel.organization_id == org_uuid,
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        return False

    await db.delete(config)
    await db.commit()

    try:
        from src.core.cache import invalidate_config
        await invalidate_config(org_id, request.key)
    except ImportError as e:
        # cache module is optional; DB delete already committed
        logger.debug(f"cache module unavailable, skipping config cache invalidate: {e}")

    logger.info(f"CLI deleted config {log_safe(request.key)} for user {current_user.email}")
    return True


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
    """
    from src.repositories.integrations import IntegrationsRepository
    from src.repositories.oauth import OAuthTokenRepository
    from src.services.oauth_provider import resolve_url_template
    from src.core.security import decrypt_secret

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None

    try:
        repo = IntegrationsRepository(db)

        # Try to get org-specific mapping first
        mapping = None
        if org_uuid:
            mapping = await repo.get_integration_for_org(request.name, org_uuid)

        if mapping:
            # Org-specific mapping found
            config = await repo.get_config_for_mapping(mapping.integration_id, org_uuid)
            integration = mapping.integration
            entity_id = mapping.entity_id or (integration.default_entity_id if integration else None)

            secret_keys = [s.key for s in integration.config_schema if s.type == "secret"] if integration else []
            response_data: dict[str, Any] = {
                "integration_id": str(mapping.integration_id),
                "entity_id": entity_id,
                "entity_name": mapping.entity_name,
                "config": config or {},
                "oauth": None,
                "config_secret_keys": secret_keys,
            }

            # Build OAuth data if provider exists
            if integration and integration.oauth_provider:
                token = mapping.oauth_token
                if not token:
                    # Cascade: prefer org-scoped token, fall back to global.
                    # See api/src/repositories/README.md for the pattern.
                    oauth_token_repo = OAuthTokenRepository(
                        db, org_id=org_uuid, is_superuser=True
                    )
                    token = await oauth_token_repo.get_org_level_for_provider(
                        integration.oauth_provider.id
                    )
                response_data["oauth"] = await _build_oauth_data(
                    integration.oauth_provider, token, entity_id, resolve_url_template, decrypt_secret,
                    oauth_scope=request.oauth_scope,
                )

            logger.info(f"SDK retrieved integration '{log_safe(request.name)}' (org mapping) for user {current_user.email}")
            return SDKIntegrationsGetResponse(**response_data)

        # Fall back to integration defaults
        integration = await repo.get_integration_by_name(request.name)
        if not integration:
            logger.debug(f"SDK integrations.get('{log_safe(request.name)}'): integration not found")
            return None

        entity_id = integration.default_entity_id or integration.entity_id
        config = await repo.get_integration_defaults(integration.id)

        secret_keys = [s.key for s in integration.config_schema if s.type == "secret"]
        response_data = {
            "integration_id": str(integration.id),
            "entity_id": entity_id,
            "entity_name": None,  # No mapping = no entity name
            "config": config or {},
            "oauth": None,
            "config_secret_keys": secret_keys,
        }

        # Build OAuth data if provider exists
        if integration.oauth_provider:
            # Cascade: prefer org-scoped token, fall back to global.
            # See api/src/repositories/README.md for the pattern.
            oauth_token_repo = OAuthTokenRepository(
                db, org_id=org_uuid, is_superuser=True
            )
            token = await oauth_token_repo.get_org_level_for_provider(
                integration.oauth_provider.id
            )
            response_data["oauth"] = await _build_oauth_data(
                integration.oauth_provider, token, entity_id, resolve_url_template, decrypt_secret,
                oauth_scope=request.oauth_scope,
            )

        logger.info(f"SDK retrieved integration '{log_safe(request.name)}' (defaults) for user {current_user.email}")
        return SDKIntegrationsGetResponse(**response_data)

    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"SDK integrations.get failed: {log_safe(e)}")
        return None


async def _build_oauth_data(
    provider: Any,
    token: Any,
    entity_id: str | None,
    resolve_url_template: Any,
    decrypt_secret: Any,
    oauth_scope: str | None = None,
) -> SDKIntegrationsOAuthData:
    """Build OAuth data dict from provider and token for CLI response.

    Args:
        provider: OAuth provider configuration
        token: Stored OAuth token (may be None)
        entity_id: External entity ID for URL templating
        resolve_url_template: Function to resolve {entity_id} in URLs
        decrypt_secret: Function to decrypt encrypted values
        oauth_scope: Override scope for token request (triggers fresh token fetch)
    """
    # Decrypt client secret (needed for both stored tokens and auto-refresh)
    client_secret = None
    if provider.encrypted_client_secret:
        try:
            raw = provider.encrypted_client_secret
            client_secret = await asyncio.to_thread(
                decrypt_secret, raw.decode() if isinstance(raw, bytes) else raw
            )
        except Exception:
            logger.warning("Failed to decrypt client_secret")

    # Resolve token_url with entity_id if provided
    resolved_token_url = provider.token_url
    if provider.token_url and entity_id:
        resolved_token_url = resolve_url_template(
            url=provider.token_url,
            entity_id=entity_id,
            defaults=provider.token_url_defaults,
        )

    access_token = None
    refresh_token = None
    expires_at = None

    # Check if we should auto-fetch a fresh token
    if should_auto_refresh_token(provider, entity_id, oauth_scope):
        scope_info = f"oauth_scope={log_safe(oauth_scope)}" if oauth_scope else f"entity_id={entity_id}"
        logger.info(f"Auto-refreshing token ({scope_info})")

        if client_secret and resolved_token_url:
            from src.services.oauth_provider import OAuthProviderClient

            oauth_client = OAuthProviderClient()
            # Use oauth_scope override if provided, otherwise use provider's default
            scopes = oauth_scope if oauth_scope else (
                " ".join(provider.scopes) if provider.scopes else ""
            )

            success, result = await oauth_client.get_client_credentials_token(
                token_url=resolved_token_url,
                client_id=provider.client_id,
                client_secret=client_secret,
                scopes=scopes,
                audience=provider.audience,
            )

            if success:
                access_token = result.get("access_token")
                expires_at_dt = result.get("expires_at")
                if expires_at_dt:
                    expires_at = (
                        expires_at_dt.isoformat()
                        if hasattr(expires_at_dt, "isoformat")
                        else str(expires_at_dt)
                    )
                logger.info("Auto-refresh token successful")
            else:
                error_msg = result.get("error_description", result.get("error", "Unknown error"))
                logger.error(f"Auto-refresh token failed: {log_safe(error_msg)}")
        else:
            logger.warning("Cannot auto-refresh: missing client_secret or resolved_token_url")
    elif token:
        # Use stored token (existing behavior)
        if token.encrypted_access_token:
            try:
                raw = token.encrypted_access_token
                access_token = await asyncio.to_thread(
                    decrypt_secret, raw.decode() if isinstance(raw, bytes) else raw
                )
            except Exception:
                logger.warning("Failed to decrypt access_token")

        if token.encrypted_refresh_token:
            try:
                raw = token.encrypted_refresh_token
                refresh_token = await asyncio.to_thread(
                    decrypt_secret, raw.decode() if isinstance(raw, bytes) else raw
                )
            except Exception:
                logger.warning("Failed to decrypt refresh_token")

        if token.expires_at:
            expires_at = token.expires_at.isoformat()

    return SDKIntegrationsOAuthData(
        connection_name=provider.provider_name,
        client_id=provider.client_id,
        client_secret=client_secret,
        authorization_url=provider.authorization_url,
        token_url=resolved_token_url,
        scopes=provider.scopes or [],
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


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
    """List all mappings for an integration via SDK."""
    from src.repositories.integrations import IntegrationsRepository

    try:
        repo = IntegrationsRepository(db)
        integration = await repo.get_integration_by_name(request.name)

        if not integration:
            logger.warning(f"SDK integrations.list_mappings: integration '{log_safe(request.name)}' not found")
            return None

        # Apply the C2 gate: explicit ``scope`` (UUID or "global") requires
        # platform-admin or provider-org bypass. UNSET (None / "") falls
        # back to the caller's own org. ``scope="global"`` returns None
        # from the resolver — list all mappings (bypass already enforced).
        # A resolved provider org is also an enumerate-all scope for mapping
        # listing: providers need to see every customer mapping by default.
        resolved_org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        if resolved_org_id is None and request.scope in (None, ""):
            # Caller has no org — system account on UNSET. Return empty.
            mappings = []
        elif resolved_org_id is None:
            # Bypass verified by the resolver — request was "global".
            mappings = await repo.list_mappings(integration.id)
        else:
            resolved_org_uuid = UUID(resolved_org_id)
            org_row = await db.execute(
                select(Organization.is_provider).where(Organization.id == resolved_org_uuid)
            )
            if bool(org_row.scalar_one_or_none()):
                mappings = await repo.list_mappings(integration.id)
            else:
                mappings = await repo.list_mappings(
                    integration.id, organization_id=resolved_org_uuid
                )

        logger.info(f"SDK listed {len(mappings)} mappings for integration '{log_safe(request.name)}' for user {current_user.email}")

        items = []
        for mapping in mappings:
            # Get merged config (integration defaults + org overrides)
            config = await repo.get_config_for_mapping(integration.id, mapping.organization_id)
            items.append({
                "id": str(mapping.id),
                "integration_id": str(mapping.integration_id),
                "organization_id": str(mapping.organization_id),
                "entity_id": mapping.entity_id,
                "entity_name": mapping.entity_name,
                "oauth_token_id": str(mapping.oauth_token_id) if mapping.oauth_token_id else None,
                "config": config,
                "created_at": mapping.created_at.isoformat(),
                "updated_at": mapping.updated_at.isoformat(),
            })

        return SDKIntegrationsListMappingsResponse(items=items)

    except HTTPException:
        # Authorization failures (e.g. 403 from _resolve_sdk_org_id) must
        # surface to the client. The blanket ``except Exception`` below
        # would otherwise downgrade a 403 to a 200/null response and
        # make unauthorized requests indistinguishable from misses.
        raise
    except Exception as e:
        logger.error(f"SDK integrations.list_mappings failed: {log_safe(e)}")
        return None


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
    """Get a specific integration mapping by org_id or entity_id via SDK."""
    from src.repositories.integrations import IntegrationsRepository

    try:
        repo = IntegrationsRepository(db)
        integration = await repo.get_integration_by_name(request.name)

        if not integration:
            logger.warning(f"SDK integrations.get_mapping: integration '{log_safe(request.name)}' not found")
            return None

        # Apply the C2 gate. Non-bypass callers can only target their own
        # org; cross-org or "global" requires platform-admin / provider-org.
        resolved_org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        mapping = None

        # Direct lookup by org_id.
        if resolved_org_id is not None:
            mapping = await repo.get_mapping_by_org(
                integration.id, UUID(resolved_org_id)
            )

        # entity_id fallback search, scoped by the resolved org. For a
        # global-scoped caller (bypass), search across all mappings;
        # otherwise restrict to the caller's resolved org so non-bypass
        # callers can't probe other orgs' entity_ids.
        if not mapping and request.entity_id:
            candidates = await repo.list_mappings(
                integration.id,
                organization_id=UUID(resolved_org_id) if resolved_org_id else None,
            )
            for m in candidates:
                if m.entity_id == request.entity_id:
                    mapping = m
                    break

        if not mapping:
            return None

        # Get merged config for the mapping
        config = await repo.get_config_for_mapping(integration.id, mapping.organization_id)

        logger.info(f"SDK retrieved mapping for integration '{log_safe(request.name)}' for user {current_user.email}")

        return SDKIntegrationsMappingItem(
            id=str(mapping.id),
            integration_id=str(mapping.integration_id),
            organization_id=str(mapping.organization_id),
            entity_id=mapping.entity_id,
            entity_name=mapping.entity_name,
            oauth_token_id=str(mapping.oauth_token_id) if mapping.oauth_token_id else None,
            config=config,
            created_at=mapping.created_at.isoformat(),
            updated_at=mapping.updated_at.isoformat(),
        )

    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"SDK integrations.get_mapping failed: {log_safe(e)}")
        return None


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
    """Create or update an integration mapping for an organization via SDK."""
    from src.repositories.integrations import IntegrationsRepository
    from src.models.contracts.integrations import IntegrationMappingCreate, IntegrationMappingUpdate

    try:
        repo = IntegrationsRepository(db)
        integration = await repo.get_integration_by_name(request.name)

        if not integration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Integration '{request.name}' not found",
            )

        # Apply the C2 gate before touching another org's mapping row.
        resolved_org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        if resolved_org_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="upsert_mapping requires an org scope; global is not a valid mapping target",
            )
        org_uuid = UUID(resolved_org_id)

        # Check if mapping already exists
        existing_mapping = await repo.get_mapping_by_org(integration.id, org_uuid)

        if existing_mapping:
            # Update existing mapping
            update_data = IntegrationMappingUpdate(
                entity_id=request.entity_id,
                entity_name=request.entity_name,
                config=request.config,
            )
            mapping = await repo.update_mapping(
                existing_mapping.id,
                update_data,
                updated_by=current_user.email,
            )
            if not mapping:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update mapping",
                )
            logger.info(f"SDK updated mapping for integration '{log_safe(request.name)}', org '{log_safe(request.scope)}' by {current_user.email}")
        else:
            # Create new mapping
            create_data = IntegrationMappingCreate(
                organization_id=org_uuid,
                entity_id=request.entity_id,
                entity_name=request.entity_name,
                config=request.config,
            )
            mapping = await repo.create_mapping(
                integration.id,
                create_data,
                updated_by=current_user.email,
            )
            logger.info(f"SDK created mapping for integration '{log_safe(request.name)}', org '{log_safe(request.scope)}' by {current_user.email}")

        await db.commit()

        # Get merged config for response
        config = await repo.get_config_for_mapping(integration.id, mapping.organization_id)

        return SDKIntegrationsMappingItem(
            id=str(mapping.id),
            integration_id=str(mapping.integration_id),
            organization_id=str(mapping.organization_id),
            entity_id=mapping.entity_id,
            entity_name=mapping.entity_name,
            oauth_token_id=str(mapping.oauth_token_id) if mapping.oauth_token_id else None,
            config=config,
            created_at=mapping.created_at.isoformat(),
            updated_at=mapping.updated_at.isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SDK integrations.upsert_mapping failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upsert mapping: {str(e)}",
        )


@router.post(
    "/integrations/delete_mapping",
    summary="Delete a mapping for an organization",
)
async def sdk_integrations_delete_mapping(
    request: SDKIntegrationsDeleteMappingRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete an integration mapping for an organization via SDK."""
    from src.repositories.integrations import IntegrationsRepository

    try:
        repo = IntegrationsRepository(db)
        integration = await repo.get_integration_by_name(request.name)

        if not integration:
            logger.warning(f"SDK integrations.delete_mapping: integration '{log_safe(request.name)}' not found")
            return {"deleted": False}

        # Apply the C2 gate before touching another org's mapping row.
        resolved_org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        if resolved_org_id is None:
            return {"deleted": False}
        org_uuid = UUID(resolved_org_id)

        # Find the mapping
        mapping = await repo.get_mapping_by_org(integration.id, org_uuid)

        if not mapping:
            logger.warning(f"SDK integrations.delete_mapping: mapping not found for org '{log_safe(request.scope)}'")
            return {"deleted": False}

        # Delete the mapping
        deleted = await repo.delete_mapping(mapping.id)
        await db.commit()

        logger.info(f"SDK deleted mapping for integration '{log_safe(request.name)}', org '{log_safe(request.scope)}' by {current_user.email}")

        return {"deleted": deleted}

    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
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

    The HTTP refresh itself is delegated to the shared primitive
    :func:`src.services.oauth_provider.refresh_oauth_token_http`; this handler
    only owns the provider lookup, context build, and persistence.
    """
    from src.models.orm.oauth import OAuthToken
    from src.repositories.oauth import (
        OAuthProviderRepository,
        OAuthTokenRepository,
    )
    from src.services.oauth_provider import (
        build_token_refresh_context,
        refresh_oauth_token_http,
    )

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None

    try:
        # Cascade: prefer org-scoped provider, fall back to global.
        # See api/src/repositories/README.md for the pattern.
        provider_repo = OAuthProviderRepository(
            db, org_id=org_uuid, is_superuser=True
        )
        provider = await provider_repo.get(provider_name=request.connection_name)

        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"OAuth provider '{request.connection_name}' not found",
            )

        # For authorization_code flow we need the stored token up front so
        # build_token_refresh_context can carry the encrypted refresh token.
        token_repo = OAuthTokenRepository(
            db, org_id=org_uuid, is_superuser=True
        )
        stored_token = None
        if provider.oauth_flow_type == "authorization_code":
            stored_token = await token_repo.get_org_level_for_provider(provider.id)
            if not stored_token or not stored_token.encrypted_refresh_token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot refresh: no refresh_token stored for this connection",
                )

        # Build the context dict and delegate to the shared primitive.
        # build_token_refresh_context handles the {entity_id} fallback chain
        # (org mapping → integration.default_entity_id → integration.entity_id)
        # in one place so the SDK endpoint, scheduler, and connections router
        # cannot drift.
        td = await build_token_refresh_context(
            db=db,
            provider=provider,
            token=stored_token,
            org_id=org_uuid,
        )
        outcome = await refresh_oauth_token_http(td)

        if not outcome["success"]:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=outcome.get("error", "Token refresh failed"),
            )

        access_token = outcome.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Token refresh returned no access_token",
            )

        expires_at_dt = outcome.get("expires_at")
        expires_at = None
        if expires_at_dt:
            expires_at = (
                expires_at_dt.isoformat()
                if hasattr(expires_at_dt, "isoformat")
                else str(expires_at_dt)
            )

        # Persist the new token. The SDK endpoint creates a new user_id=NULL
        # token row if one doesn't already exist — this is distinct from the
        # connections router (which requires an existing row) and so persistence
        # remains per-caller.
        token_obj = stored_token
        if token_obj is None:
            # client_credentials path — fetch (or later create) the user_id=NULL row.
            # Cascade: prefer org-scoped token, fall back to global.
            token_obj = await token_repo.get_org_level_for_provider(provider.id)

        if token_obj:
            token_obj.encrypted_access_token = outcome["encrypted_access_token"]
            if outcome.get("encrypted_refresh_token"):
                token_obj.encrypted_refresh_token = outcome["encrypted_refresh_token"]
            if expires_at_dt and hasattr(expires_at_dt, "isoformat"):
                token_obj.expires_at = expires_at_dt
        else:
            new_token = OAuthToken(
                organization_id=provider.organization_id,
                provider_id=provider.id,
                encrypted_access_token=outcome["encrypted_access_token"],
                encrypted_refresh_token=outcome.get("encrypted_refresh_token"),
                expires_at=expires_at_dt if expires_at_dt and hasattr(expires_at_dt, "isoformat") else None,
                scopes=provider.scopes or [],
            )
            db.add(new_token)

        provider.status = "completed"
        provider.status_message = None
        provider.last_token_refresh = datetime.now(timezone.utc)

        await db.commit()

        logger.info(
            f"SDK refreshed OAuth token for '{log_safe(request.connection_name)}' "
            f"by {current_user.email}"
        )

        return SDKIntegrationsRefreshTokenResponse(
            access_token=access_token,
            expires_at=expires_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SDK integrations.refresh_token failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh failed: {str(e)}",
        )


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
            "parameters": [p.model_dump() if hasattr(p, 'model_dump') else p for p in w.parameters],
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
    await publish_cli_session_update(str(current_user.user_id), request.session_id, response.model_dump(mode="json"))

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
            _session_to_response(s, is_connected=repo.is_connected(s))
            for s in sessions
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

    logger.info(f"CLI session deleted: {log_safe(session_id)} for user {current_user.email}")


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
        org_id=str(current_user.organization_id) if current_user.organization_id else None,
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
        response_data = _session_to_response(updated_session, is_connected=repo.is_connected(updated_session))
        await publish_cli_session_update(str(current_user.user_id), session_id, response_data.model_dump(mode="json"))

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
    stmt = select(Execution).where(
        Execution.session_id == session_uuid,
        Execution.status == ExecutionStatus.PENDING,
    ).order_by(Execution.created_at.desc()).limit(1)
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

    logger.info(f"CLI session pending picked up: workflow={log_safe(workflow_name)}, execution_id={execution_id}, session_id={log_safe(session_id)}")

    # Broadcast session state update
    updated_session = await repo.get_session_with_executions(session_uuid)
    if updated_session:
        response_data = _session_to_response(updated_session, is_connected=repo.is_connected(updated_session))
        await publish_cli_session_update(str(current_user.user_id), session_id, response_data.model_dump(mode="json"))

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
        logger.warning(f"Log streaming not available, log skipped: {log_safe(request.message)}")


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
            logger.debug(f"Persisted {logs_persisted} logs directly for CLI execution {log_safe(execution_id)}")

    await db.commit()

    # Fallback: flush any logs from Redis Stream (backwards compatibility)
    logs_flushed = 0
    if not request.logs:
        try:
            from bifrost._logging import flush_logs_to_postgres
            logs_flushed = await flush_logs_to_postgres(execution_id)
            if logs_flushed > 0:
                logger.debug(f"Flushed {logs_flushed} logs from stream for CLI execution {log_safe(execution_id)}")
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
        response_data = _session_to_response(updated_session, is_connected=session_repo.is_connected(updated_session))
        await publish_cli_session_update(str(current_user.user_id), session_id, response_data.model_dump(mode="json"))

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
    from src.services.llm import get_llm_client, LLMMessage

    try:
        client = await get_llm_client(db)

        # Convert to LLMMessage objects
        llm_messages = [
            LLMMessage(role=msg["role"], content=msg["content"])  # type: ignore[arg-type]
            for msg in request.messages
        ]

        response = await client.complete(
            messages=llm_messages,
            max_tokens=request.max_tokens,
            model=request.model,
        )

        logger.info(f"CLI AI complete: model={log_safe(response.model)}, tokens={response.input_tokens}/{response.output_tokens}")

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
                execution_id=UUID(request.execution_id) if request.execution_id else None,
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
        if error_type == "AuthenticationError" and error_module in ("anthropic", "openai"):
            provider = "Anthropic" if error_module == "anthropic" else "OpenAI"
            logger.error(f"CLI AI complete failed: {provider} authentication error - invalid API key")
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
    from src.services.llm import get_llm_client, LLMMessage

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
                            execution_id=UUID(execution_id_str) if execution_id_str else None,
                            organization_id=UUID(resolved_org_id) if resolved_org_id else None,
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
            if error_type == "AuthenticationError" and error_module in ("anthropic", "openai"):
                provider = "Anthropic" if error_module == "anthropic" else "OpenAI"
                logger.error(f"CLI AI stream failed: {provider} authentication error - invalid API key")
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
        }
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
            max_tokens=config.max_tokens,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# =============================================================================
# SDK Knowledge Store Endpoints
# =============================================================================


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
    from src.repositories.knowledge import KnowledgeRepository
    from src.services.embeddings import get_embedding_client

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        org_uuid = UUID(org_id) if org_id else None

        embedding_client = await get_embedding_client(db)

        # Store document
        repo = KnowledgeRepository(db, org_id=org_uuid, is_superuser=True)
        doc_ids = await repo.store_chunked(
            content=request.content,
            namespace=request.namespace,
            key=request.key,
            metadata=request.metadata,
            created_by=current_user.user_id,
            embedder=embedding_client,
        )
        doc_id = doc_ids[0]

        await db.commit()

        logger.info(f"CLI knowledge store: namespace={log_safe(request.namespace)}, key={log_safe(request.key)}, doc_id={doc_id}")

        return {"id": doc_id}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"CLI knowledge store failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge store failed: {str(e)}",
        )


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
    from src.repositories.knowledge import KnowledgeRepository
    from src.services.embeddings import get_embedding_client

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        org_uuid = UUID(org_id) if org_id else None

        embedding_client = await get_embedding_client(db)

        # Store each document
        repo = KnowledgeRepository(db, org_id=org_uuid, is_superuser=True)
        doc_ids = []
        for doc in request.documents:
            inserted_ids = await repo.store_chunked(
                content=doc["content"],
                namespace=request.namespace,
                key=doc.get("key"),
                metadata=doc.get("metadata"),
                created_by=current_user.user_id,
                embedder=embedding_client,
            )
            doc_id = inserted_ids[0]
            doc_ids.append(doc_id)

        await db.commit()

        logger.info(f"CLI knowledge store-many: namespace={log_safe(request.namespace)}, count={len(doc_ids)}")

        return {"ids": doc_ids}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"CLI knowledge store-many failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge store failed: {str(e)}",
        )


@router.post(
    "/knowledge/search",
    summary="Search for similar documents",
)
async def cli_knowledge_search(
    request: "CLIKnowledgeSearchRequest",
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[CLIKnowledgeDocumentResponse]:
    """Search for similar documents using vector similarity."""
    from src.models.contracts.cli import CLIKnowledgeDocumentResponse
    from src.repositories.knowledge import KnowledgeRepository
    from src.services.embeddings import get_embedding_client

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        org_uuid = UUID(org_id) if org_id else None

        # Generate query embedding
        embedding_client = await get_embedding_client(db)
        query_embedding = await embedding_client.embed_single(request.query)

        # Search
        repo = KnowledgeRepository(db, org_id=org_uuid, is_superuser=True)
        results = await repo.search(
            query_embedding=query_embedding,
            namespace=request.namespace,
            limit=request.limit,
            min_score=request.min_score,
            metadata_filter=request.metadata_filter,
            fallback=request.fallback,
        )

        logger.info(f"CLI knowledge search: query={log_safe(request.query[:50])}..., results={len(results)}")

        return [
            CLIKnowledgeDocumentResponse(
                id=doc.id,
                namespace=doc.namespace,
                content=doc.content,
                metadata=doc.metadata,
                score=doc.score,
                organization_id=doc.organization_id,
                key=doc.key,
                created_at=doc.created_at.isoformat() if doc.created_at else None,
            )
            for doc in results
        ]
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"CLI knowledge search failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge search failed: {str(e)}",
        )


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
    from src.repositories.knowledge import KnowledgeRepository

    try:
        org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
        org_uuid = UUID(org_id) if org_id else None

        repo = KnowledgeRepository(db, org_id=org_uuid, is_superuser=True)
        deleted = await repo.delete_by_key(
            key=request.key,
            namespace=request.namespace,
        )

        await db.commit()

        logger.info(f"CLI knowledge delete: namespace={log_safe(request.namespace)}, key={log_safe(request.key)}, deleted={deleted}")

        return {"deleted": deleted}
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"CLI knowledge delete failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge delete failed: {str(e)}",
        )


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
    from src.repositories.knowledge import KnowledgeRepository

    try:
        org_id = await _resolve_sdk_org_id(current_user, scope, db)
        org_uuid = UUID(org_id) if org_id else None

        repo = KnowledgeRepository(db, org_id=org_uuid, is_superuser=True)
        deleted_count = await repo.delete_namespace(
            namespace=namespace,
        )

        await db.commit()

        logger.info(f"CLI knowledge delete namespace: namespace={log_safe(namespace)}, deleted_count={deleted_count}")

        return {"deleted_count": deleted_count}
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"CLI knowledge delete namespace failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge delete namespace failed: {str(e)}",
        )


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
    from src.models.contracts.cli import CLIKnowledgeNamespaceInfo
    from src.repositories.knowledge import KnowledgeRepository

    try:
        org_id = await _resolve_sdk_org_id(current_user, scope, db)
        org_uuid = UUID(org_id) if org_id else None

        repo = KnowledgeRepository(db, org_id=org_uuid, is_superuser=True)
        results = await repo.list_namespaces(
            include_global=include_global,
        )

        return [
            CLIKnowledgeNamespaceInfo(
                namespace=ns.namespace,
                scopes=ns.scopes,
            )
            for ns in results
        ]
    except HTTPException:
        # Auth/scope failures (e.g. 403 from _resolve_sdk_org_id) must surface.
        raise
    except Exception as e:
        logger.error(f"CLI knowledge list namespaces failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge list namespaces failed: {str(e)}",
        )


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
    from src.models.contracts.cli import CLIKnowledgeDocumentResponse
    from src.repositories.knowledge import KnowledgeRepository

    try:
        org_id = await _resolve_sdk_org_id(current_user, scope, db)
        org_uuid = UUID(org_id) if org_id else None

        repo = KnowledgeRepository(db, org_id=org_uuid, is_superuser=True)
        result = await repo.get_by_key(
            key=key,
            namespace=namespace,
        )

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )

        return CLIKnowledgeDocumentResponse(
            id=result.id,
            namespace=result.namespace,
            content=result.content,
            metadata=result.metadata,
            organization_id=result.organization_id,
            key=result.key,
            created_at=result.created_at.isoformat() if result.created_at else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CLI knowledge get failed: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge get failed: {str(e)}",
        )


# =============================================================================
# CLI Download (generates installable package)
# =============================================================================


def _to_pep440(version: str) -> str:
    """Coerce `git describe --tags --always --dirty` output to a PEP 440 version.

    Examples:
        "v0.6-219-g24b8acb9-dirty" -> "0.6.post219+g24b8acb9.dirty"
        "v1.2.3"                   -> "1.2.3"
        "v1.2.3-dirty"             -> "1.2.3+dirty"
        "abc1234"                  -> "0.0.0+gabc1234"   (no-tag fallback)
        "unknown"                  -> "0.0.0"
    """
    import re as _re

    if not version or version == "unknown":
        return "0.0.0"

    # Strip leading 'v' from tag prefix
    v = version[1:] if version.startswith("v") else version

    dirty = v.endswith("-dirty")
    if dirty:
        v = v[: -len("-dirty")]

    # Shape: <tag>-<N>-g<sha>
    m = _re.match(r"^(.+)-(\d+)-(g[0-9a-f]+)$", v)
    if m:
        tag, n, sha = m.group(1), m.group(2), m.group(3)
        local = f"{sha}.dirty" if dirty else sha
        return f"{tag}.post{n}+{local}"

    # Clean tag (e.g. "1.2.3")
    if _re.match(r"^\d+(\.\d+)*$", v):
        return f"{v}+dirty" if dirty else v

    # No-tag fallback: bare sha from `git describe --always`
    local = f"g{v}.dirty" if dirty else f"g{v}"
    return f"0.0.0+{local}"


@install_router.get(
    "/download",
    summary="Download CLI package",
    description="Download the Bifrost CLI as a pip-installable tarball",
)
async def download_cli() -> Response:
    """
    Serve CLI as installable package.

    Returns a tarball that can be installed with:
    pip install https://your-bifrost-instance.com/api/cli/download
    """
    from shared.version import get_version

    package_dir = Path(__file__).parent.parent.parent / "bifrost"

    if not package_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CLI package not found",
        )

    buffer = io.BytesIO()

    # Files to exclude (platform-only internal files)
    exclude_files = {
        "_internal.py",     # Platform-only permission checks
        "_write_buffer.py", # Platform-only, requires Redis
        "_logging.py",      # Platform-only logging
        "_sync.py",         # Platform-only sync utilities
    }

    def _generate_tarball():
        """Generate tarball synchronously in thread."""
        import re as _re
        import io as _io
        live_version = get_version()
        pep440_version = _to_pep440(live_version)

        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            # Add pyproject.toml at root level with PEP 440 version stamped
            # (setuptools validates project.version against PEP 440; git describe
            # output like "v0.6-219-gabc1234-dirty" must be coerced first)
            pyproject_path = package_dir / "pyproject.toml"
            if pyproject_path.exists():
                content = pyproject_path.read_text()
                content = _re.sub(
                    r'^version\s*=\s*"[^"]*"',
                    f'version = "{pep440_version}"',
                    content,
                    flags=_re.MULTILINE,
                )
                data = content.encode()
                info = tarfile.TarInfo(name="pyproject.toml")
                info.size = len(data)
                tar.addfile(info, fileobj=_io.BytesIO(data))

            # Add all Python files from bifrost/
            for file_path in package_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                if "__pycache__" in str(file_path):
                    continue
                if file_path.name in exclude_files:
                    continue
                if file_path.suffix not in (".py", ".toml"):
                    continue
                if file_path.name == "pyproject.toml":
                    continue  # Already added above

                arcname = f"bifrost/{file_path.relative_to(package_dir)}"

                # Stamp __version__ in __init__.py
                if file_path.name == "__init__.py" and file_path.parent == package_dir:
                    content = file_path.read_text()
                    content = _re.sub(
                        r"^__version__\s*=\s*_compute_version\(\)",
                        f'__version__ = "{live_version}"',
                        content,
                        flags=_re.MULTILINE,
                    )
                    data = content.encode()
                    info = tarfile.TarInfo(name=arcname)
                    info.size = len(data)
                    tar.addfile(info, fileobj=_io.BytesIO(data))
                else:
                    tar.add(file_path, arcname=arcname)

    await asyncio.to_thread(_generate_tarball)

    # Get the complete tarball content after it's fully finalized
    tarball_content = buffer.getvalue()

    return Response(
        content=tarball_content,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f"attachment; filename=bifrost-cli-{get_version()}.tar.gz",
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
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SDKTableInfo:
    """Create a new table via SDK."""
    from src.models.orm.tables import Table

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None

    # Exact-scope uniqueness check (not a cascade): "is there already a
    # table named X in MY scope?" Cascade would mask collisions when a
    # global Table with the same name exists. See repositories/README.md.
    stmt = select(Table).where(
        Table.name == request.name,
        Table.organization_id == org_uuid,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Table '{request.name}' already exists",
        )

    # Seed admin_bypass so platform admins can still operate on tables
    # created via the SDK. SDK callers can override this later by setting
    # explicit policies through the REST `PATCH /api/tables/{id}` endpoint.
    from shared.policies.probe import make_seed_admin_bypass

    table = Table(
        name=request.name,
        description=request.description,
        schema=request.table_schema,
        organization_id=org_uuid,
        created_by=current_user.email,
        access=make_seed_admin_bypass(),
    )
    db.add(table)
    await db.commit()
    await db.refresh(table)

    logger.info(f"CLI created table '{log_safe(request.name)}' for user {current_user.email}")

    return SDKTableInfo(
        id=str(table.id),
        name=table.name,
        organization_id=str(table.organization_id) if table.organization_id else None,
        table_schema=table.schema,
        description=table.description,
        created_at=table.created_at.isoformat(),
        updated_at=table.updated_at.isoformat(),
    )


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

    Engine sentinel: the SDK has already resolved scope, so we pass
    is_superuser=True to TableRepository and trust the org_uuid.
    The base class handles the cascade (org + global) for us.
    """
    # Local import keeps the router file's top-level imports lean.
    from src.repositories.tables import TableRepository

    org_id = await _resolve_sdk_org_id(current_user, request.scope, db)
    org_uuid = UUID(org_id) if org_id else None

    repo = TableRepository(db, org_id=org_uuid, is_superuser=True)
    tables = await repo.list()
    tables = sorted(tables, key=lambda t: t.name)

    return [
        SDKTableInfo(
            id=str(t.id),
            name=t.name,
            organization_id=str(t.organization_id) if t.organization_id else None,
            table_schema=t.schema,
            description=t.description,
            created_at=t.created_at.isoformat(),
            updated_at=t.updated_at.isoformat(),
        )
        for t in tables
    ]
