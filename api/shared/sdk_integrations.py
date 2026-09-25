"""Shared business service for SDK ``integrations`` read operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/cli.py::sdk_integrations_*``) serving
  external SDK/CLI callers, and
- the engine-local dispatcher
  (``api/src/services/execution/sdk_local_dispatch.py``) serving workflow
  and ``@service`` children through the parent-side local transport.

Both paths share authenticated scope input and resolve it after integration
lookup, preserving the historical missing-name response. They also share the
provider-bypass enumerate-all rule, entity-ID lookup boundaries, the
declared-Solution 424, missing integration/mapping nulls, merged configs,
external-user behavior, OAuth token cascade/decryption, and the optional
scope-based token fetch — so HTTP and local results are identical by
construction.

Only the three read operations (``get``, ``list_mappings``, ``get_mapping``)
plus the mapping mutations (``upsert_mapping``, ``delete_mapping``) and
OAuth token refresh live here. The HTTP handlers
(``api/src/routers/cli.py::sdk_integrations_*``) and the engine-local
dispatcher (``api/src/services/execution/sdk_local_dispatch.py``) call
these same functions, so HTTP and local results are identical by
construction.

Parent-side only: imports SQLAlchemy repositories and the OAuth provider
client. The child never imports this module (it stays DB-free behind the
dedicated local channel).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)


class IntegrationServiceError(Exception):
    """SDK integrations failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the local dispatcher (``ok: false`` frames) can map the same
    failure to their own transport. Currently only used for the
    declared-Solution 424; scope failures arrive as ``ScopeResolutionError``
    from the shared scope resolver, and missing entities are ``None``.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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


async def connection_is_declared(
    session: AsyncSession, solution_id: UUID | str | None, name: str
) -> bool:
    """True if ``solution_id`` declares an integration named ``name`` via a
    SolutionConnectionSchema row. Drives the RequiredConnectionUnset 424."""
    from src.models.orm.solution_connection_schema import SolutionConnectionSchema

    if solution_id is None or solution_id == "":
        return False
    try:
        sid = solution_id if isinstance(solution_id, UUID) else UUID(str(solution_id))
    except (ValueError, TypeError):
        return False
    row = (
        await session.execute(
            select(SolutionConnectionSchema.id).where(
                SolutionConnectionSchema.solution_id == sid,
                SolutionConnectionSchema.integration_name == name,
            )
        )
    ).first()
    return row is not None


async def build_oauth_data(
    provider: Any,
    token: Any,
    entity_id: str | None,
    resolve_url_template: Any,
    decrypt_secret: Any,
    oauth_scope: str | None = None,
    external: bool = False,
) -> dict[str, Any]:
    """Build the OAuth data dict from provider and token.

    The canonical builder shared by the HTTP handler (via the service
    functions below) and the engine-local dispatcher. ``resolve_url_template``
    and ``decrypt_secret`` are explicit parameters (not module imports) so
    unit tests can inject fakes; production callers pass the real
    implementations from ``src.services.oauth_provider`` and
    ``src.core.security``.

    Returns a plain dict matching ``SDKIntegrationsOAuthData`` so both the
    HTTP handler (which wraps it in the response model) and the local
    transport (which needs JSON-serializable frames) share one builder.

    Args:
        provider: OAuth provider configuration
        token: Stored OAuth token (may be None)
        entity_id: External entity ID for URL templating
        resolve_url_template: Function to resolve {entity_id} in URLs
        decrypt_secret: Function to decrypt encrypted values
        oauth_scope: Override scope for token request (triggers fresh token fetch)
        external: When True (EXT-1 OPEN-E), an EXTERNAL portal caller — the
            provider's ``client_secret`` is a GLOBAL third-party credential, so
            it is never decrypted/returned and the client-credentials
            auto-refresh (which needs it) is suppressed. Only a stored,
            org-bound ``access_token`` (already scoped by the caller's repo)
            is returned. Engine/sentinel/normal callers leave this False.
    """

    # Decrypt client secret (needed for both stored tokens and auto-refresh).
    # An external never receives it (global third-party credential — OPEN-E).
    client_secret = None
    if provider.encrypted_client_secret and not external:
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
        logger.info("Auto-refreshing integration token")

        if client_secret and resolved_token_url:
            from src.services.oauth_provider import OAuthProviderClient

            oauth_client = OAuthProviderClient()
            # Use oauth_scope override if provided, otherwise use provider's default
            scopes = (
                oauth_scope
                if oauth_scope
                else (" ".join(provider.scopes) if provider.scopes else "")
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
                error_msg = result.get(
                    "error_description", result.get("error", "Unknown error")
                )
                logger.error(f"Auto-refresh token failed: {log_safe(error_msg)}")
        else:
            logger.warning(
                "Cannot auto-refresh: missing client_secret or resolved_token_url"
            )
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

    return {
        "connection_name": provider.provider_name,
        "client_id": provider.client_id,
        "client_secret": client_secret,
        "authorization_url": provider.authorization_url,
        "token_url": resolved_token_url,
        "scopes": provider.scopes or [],
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
    }


async def get_sdk_integration_dict(
    session: AsyncSession,
    *,
    name: str,
    org_id: UUID | None,
    oauth_scope: str | None = None,
    solution_id: UUID | str | None = None,
    external: bool = False,
) -> dict[str, Any] | None:
    """Resolve one integration for an SDK caller (shared by both paths).

    Args:
        session: Short-lived parent/HTTP database session.
        name: Integration name.
        org_id: Already-resolved effective scope (None for global).
        oauth_scope: Override OAuth scope for token request.
        solution_id: Parent-derived Solution install id (local path) or the
            HTTP request's solution claim. When set and the named
            integration is missing but DECLARED by this solution, raises
            :class:`IntegrationServiceError` (424) instead of returning
            None. Malformed values behave as unset (silent None), matching
            the historical handler.
        external: True for a direct EXTERNAL portal caller — drops the
            global tier on both the config merge and the OAuth-token
            cascade. Engine executions always pass False (sentinel path
            keeps the full org+global merge).

    Returns:
        JSON-serializable dict matching ``SDKIntegrationsGetResponse``,
        or None when the integration is not set up (and not declared).

    Raises:
        IntegrationServiceError: 424 for a declared-but-missing
            integration. Unexpected failures propagate to the caller: the
            HTTP handler maps them to null (historical), the local
            dispatcher to a 500 frame.
    """
    from src.repositories.integrations import IntegrationsRepository
    from src.repositories.oauth import OAuthTokenRepository

    repo = IntegrationsRepository(session)

    # Real helpers for the shared OAuth builder (unit tests inject fakes).
    from src.core.security import decrypt_secret as _decrypt_secret
    from src.services.oauth_provider import (
        resolve_url_template as _resolve_url_template,
    )

    # Try to get org-specific mapping first
    mapping = None
    if org_id:
        mapping = await repo.get_integration_for_org(name, org_id)

    if mapping:
        # Org-specific mapping found. EXTERNAL callers (OPEN-E) drop the
        # global tier on both the config merge and the OAuth-token cascade.
        config = await repo.get_config_for_mapping(
            mapping.integration_id, org_id, external=external
        )
        integration = mapping.integration
        entity_id = mapping.entity_id or (
            integration.default_entity_id if integration else None
        )

        secret_keys = (
            [s.key for s in integration.config_schema if s.type == "secret"]
            if integration
            else []
        )
        oauth: dict[str, Any] | None = None

        # Build OAuth data if provider exists
        if integration and integration.oauth_provider:
            token = mapping.oauth_token
            if not token:
                # Cascade: prefer org-scoped token, fall back to global.
                # See api/src/repositories/README.md for the pattern.
                # External callers get org-only (no global token — OPEN-E).
                oauth_token_repo = OAuthTokenRepository(
                    session,
                    org_id=org_id,
                    is_superuser=not external,
                    is_external=external,
                )
                token = await oauth_token_repo.get_org_level_for_provider(
                    integration.oauth_provider.id
                )
            oauth = await build_oauth_data(
                integration.oauth_provider,
                token,
                entity_id,
                _resolve_url_template,
                _decrypt_secret,
                oauth_scope=oauth_scope,
                external=external,
            )

        logger.info(f"SDK retrieved integration '{log_safe(name)}' (org mapping)")
        return {
            "integration_id": str(mapping.integration_id),
            "entity_id": entity_id,
            "entity_name": mapping.entity_name,
            "config": config or {},
            "oauth": oauth,
            "config_secret_keys": secret_keys,
        }

    # Fall back to integration defaults
    integration = await repo.get_integration_by_name(name)
    if not integration:
        # RequiredConnectionUnset: if the missing integration was DECLARED by
        # the calling solution, escalate to a loud 424 (mirrors
        # RequiredConfigUnset) instead of a silent None. Loose (non-solution
        # or non-declared) calls keep the silent-None behavior.
        if solution_id and await connection_is_declared(session, solution_id, name):
            raise IntegrationServiceError(
                424,
                f"Required integration '{name}' is not set up. "
                f"Set it up in the Integrations settings, or in the "
                f"solution's Setup tab.",
            )
        logger.debug(f"SDK integrations.get('{log_safe(name)}'): integration not found")
        return None

    entity_id = integration.default_entity_id or integration.entity_id
    # Integration DEFAULTS are the global (org_id=NULL) tier — an EXTERNAL
    # caller (OPEN-E) reading them would receive decrypted global secrets,
    # so external=True returns no defaults at all.
    config = await repo.get_integration_defaults(
        integration.id, external=external
    )

    secret_keys = [s.key for s in integration.config_schema if s.type == "secret"]
    oauth = None

    # Build OAuth data if provider exists. This is the DEFAULTS path: the
    # only provider here is the INTEGRATION-LEVEL (global) one, and
    # build_oauth_data decrypts its client_secret. An EXTERNAL caller
    # (OPEN-E) must get NO OAuth block at all — there is no org-tier
    # provider on this branch to fall back to.
    if integration.oauth_provider and not external:
        # Cascade: prefer org-scoped token, fall back to global.
        # See api/src/repositories/README.md for the pattern.
        oauth_token_repo = OAuthTokenRepository(
            session, org_id=org_id, is_superuser=True
        )
        token = await oauth_token_repo.get_org_level_for_provider(
            integration.oauth_provider.id
        )
        oauth = await build_oauth_data(
            integration.oauth_provider,
            token,
            entity_id,
            _resolve_url_template,
            _decrypt_secret,
            oauth_scope=oauth_scope,
        )

    logger.info(f"SDK retrieved integration '{log_safe(name)}' (defaults)")
    return {
        "integration_id": str(integration.id),
        "entity_id": entity_id,
        "entity_name": None,  # No mapping = no entity name
        "config": config or {},
        "oauth": oauth,
        "config_secret_keys": secret_keys,
    }


async def _mapping_item_dict(
    repo: Any,
    integration_id: UUID,
    mapping: Any,
    *,
    external: bool,
) -> dict[str, Any]:
    """One JSON-serializable mapping dict (merged config + echo fields)."""
    # Get merged config (integration defaults + org overrides).
    # External callers drop the global tier (NEW-G).
    config = await repo.get_config_for_mapping(
        integration_id,
        mapping.organization_id,
        external=external,
    )
    return {
        "id": str(mapping.id),
        "integration_id": str(mapping.integration_id),
        "organization_id": str(mapping.organization_id),
        "entity_id": mapping.entity_id,
        "entity_name": mapping.entity_name,
        "oauth_token_id": str(mapping.oauth_token_id)
        if mapping.oauth_token_id
        else None,
        "config": config,
        "created_at": mapping.created_at.isoformat(),
        "updated_at": mapping.updated_at.isoformat(),
    }


async def list_sdk_integration_mappings(
    session: AsyncSession,
    *,
    name: str,
    scope: str | None,
    caller_org_id: UUID | None,
    is_platform_admin: bool,
    is_provider_org: bool | None = None,
    external: bool = False,
) -> list[dict[str, Any]] | None:
    """List mappings for an integration (shared by both paths).

    Args:
        session: Short-lived parent/HTTP database session.
        name: Integration name.
        scope: Raw validated scope string (None/"" is UNSET). Only used
            to distinguish "caller has no org" (empty list) from an
            explicit bypassed-global request (all mappings) when
            ``org_id`` is None.
        caller_org_id: Authenticated caller's organization.
        external: True for a direct EXTERNAL portal caller — merged
            configs drop the global tier (NEW-G). Engine executions pass
            False.

    Returns:
        List of JSON-serializable mapping dicts, ``[]`` for a scopeless
        caller on UNSET, or None if the integration is not found.
    """
    from src.models import Organization
    from src.repositories.integrations import IntegrationsRepository

    repo = IntegrationsRepository(session)
    integration = await repo.get_integration_by_name(name)

    if not integration:
        logger.warning(
            f"SDK integrations.list_mappings: integration '{log_safe(name)}' not found"
        )
        return None

    # Preserve the HTTP contract: an unknown integration returns null before
    # scope validation. Both transports resolve against the same session.
    from shared.sdk_config import resolve_sdk_scope

    org_id = await resolve_sdk_scope(
        scope,
        caller_org_id=caller_org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )

    # Apply the C2 gate outcome: explicit ``scope`` (UUID or "global")
    # required platform-admin or provider-org bypass before this call.
    # UNSET (None / "") falls back to the caller's own org.
    # ``scope="global"`` resolves to None — list all mappings (bypass
    # already enforced). A resolved provider org is also an enumerate-all
    # scope for mapping listing: providers need to see every customer
    # mapping by default.
    if org_id is None and scope in (None, ""):
        # Caller has no org — system account on UNSET. Return empty.
        mappings = []
    elif org_id is None:
        # Bypass verified by the resolver — request was "global".
        mappings = await repo.list_mappings(integration.id)
    else:
        org_row = await session.execute(
            select(Organization.is_provider).where(Organization.id == org_id)
        )
        if bool(org_row.scalar_one_or_none()):
            mappings = await repo.list_mappings(integration.id)
        else:
            mappings = await repo.list_mappings(
                integration.id, organization_id=org_id
            )

    logger.info(
        f"SDK listed {len(mappings)} mappings for integration '{log_safe(name)}'"
    )

    return [
        await _mapping_item_dict(
            repo, integration.id, mapping, external=external
        )
        for mapping in mappings
    ]


async def get_sdk_integration_mapping_dict(
    session: AsyncSession,
    *,
    name: str,
    scope: str | None,
    caller_org_id: UUID | None,
    is_platform_admin: bool,
    is_provider_org: bool | None = None,
    entity_id: str | None = None,
    external: bool = False,
) -> dict[str, Any] | None:
    """Get one mapping by org scope or entity ID (shared by both paths).

    Args:
        session: Short-lived parent/HTTP database session.
        name: Integration name.
        scope: Requested scope string.
        caller_org_id: Authenticated caller's organization.
        entity_id: External entity ID fallback lookup, scoped by the
            resolved org: for a global-scoped caller (bypass) the search
            spans all mappings; otherwise it is restricted to the
            caller's resolved org so non-bypass callers can't probe
            other orgs' entity_ids.
        external: True for a direct EXTERNAL portal caller — merged
            config drops the global tier (NEW-G). Engine executions pass
            False.

    Returns:
        JSON-serializable mapping dict, or None if not found.
    """
    from src.repositories.integrations import IntegrationsRepository

    repo = IntegrationsRepository(session)
    integration = await repo.get_integration_by_name(name)

    if not integration:
        logger.warning(
            f"SDK integrations.get_mapping: integration '{log_safe(name)}' not found"
        )
        return None

    from shared.sdk_config import resolve_sdk_scope

    org_id = await resolve_sdk_scope(
        scope,
        caller_org_id=caller_org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )

    mapping = None

    # Direct lookup by org_id.
    if org_id is not None:
        mapping = await repo.get_mapping_by_org(integration.id, org_id)

    # entity_id fallback search, scoped by the resolved org.
    if not mapping and entity_id:
        candidates = await repo.list_mappings(
            integration.id,
            organization_id=org_id,
        )
        for m in candidates:
            if m.entity_id == entity_id:
                mapping = m
                break

    if not mapping:
        return None

    # Get merged config for the mapping. External callers drop the global
    # tier (NEW-G).
    logger.info(f"SDK retrieved mapping for integration '{log_safe(name)}'")
    return await _mapping_item_dict(
        repo, integration.id, mapping, external=external
    )


async def upsert_sdk_integration_mapping(
    session: AsyncSession,
    *,
    name: str,
    scope: str | None,
    caller_org_id: UUID | None,
    is_platform_admin: bool,
    is_provider_org: bool | None = None,
    external: bool = False,
    entity_id: str,
    entity_name: str | None = None,
    config: dict[str, Any] | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Create or update an org mapping for an integration (shared).

    Preserves the HTTP endpoint order: the missing-integration 404 comes
    before scope validation, and a global scope is a 400 (not a valid
    mapping target). The existing row keeps its OAuth link: updates never
    touch ``oauth_token_id``. Config writes go through the repository's
    override semantics (``None``/``""`` deletes the override). The
    post-write echo carries the merged config, dropping the global tier
    for external callers.

    Args:
        session: Short-lived parent/HTTP database session.
        name: Integration name.
        scope: Requested scope string (must resolve to an org).
        caller_org_id: Authenticated caller's organization.
        is_platform_admin: Whether the caller is a platform admin.
        is_provider_org: Known provider-org membership, or None for a
            live lookup on ``session`` when the scope needs a bypass
            check (supervised-service callers).
        external: True for a direct EXTERNAL portal caller — the merged
            echo config drops the global tier (NEW-G). Engine executions
            pass False.
        entity_id: External entity ID.
        entity_name: Optional display name.
        config: Optional org-specific configuration overrides.
        actor_email: Audit identity for config writes (``updated_by``).
            The dispatcher guarantees this from the parent-owned context;
            the HTTP handler passes the authenticated user's email.

    Returns:
        JSON-serializable mapping dict (same shape as ``get_mapping``).

    Raises:
        IntegrationServiceError: 404 for a missing integration, 400 for
            a global scope, 500 when the write cannot be audited or the
            update fails.
        ScopeResolutionError: 422/403 from the mutation scope gate.
    """
    from src.models.contracts.integrations import (
        IntegrationMappingCreate,
        IntegrationMappingUpdate,
    )
    from src.repositories.integrations import IntegrationsRepository

    repo = IntegrationsRepository(session)
    integration = await repo.get_integration_by_name(name)

    if not integration:
        raise IntegrationServiceError(404, f"Integration '{name}' not found")

    # Apply the C2 gate before touching another org's mapping row.
    from shared.sdk_config import resolve_sdk_scope

    org_id = await resolve_sdk_scope(
        scope,
        caller_org_id=caller_org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )
    if org_id is None:
        raise IntegrationServiceError(
            400,
            "upsert_mapping requires an org scope; global is not a valid mapping target",
        )
    if not actor_email:
        raise IntegrationServiceError(
            500,
            "upsert_mapping failed: parent dispatch identity has no caller email",
        )

    existing_mapping = await repo.get_mapping_by_org(integration.id, org_id)

    if existing_mapping:
        update_data = IntegrationMappingUpdate(
            entity_id=entity_id,
            entity_name=entity_name,
            config=config,
        )
        mapping = await repo.update_mapping(
            existing_mapping.id,
            update_data,
            updated_by=actor_email,
        )
        if not mapping:
            raise IntegrationServiceError(500, "Failed to update mapping")
        logger.info(
            f"SDK updated mapping for integration '{log_safe(name)}', org '{log_safe(str(org_id))}' by {actor_email}"
        )
    else:
        create_data = IntegrationMappingCreate(
            organization_id=org_id,
            entity_id=entity_id,
            entity_name=entity_name,
            config=config,
        )
        mapping = await repo.create_mapping(
            integration.id,
            create_data,
            updated_by=actor_email,
        )
        logger.info(
            f"SDK created mapping for integration '{log_safe(name)}', org '{log_safe(str(org_id))}' by {actor_email}"
        )

    await session.commit()

    return await _mapping_item_dict(
        repo, integration.id, mapping, external=external
    )


async def delete_sdk_integration_mapping(
    session: AsyncSession,
    *,
    name: str,
    scope: str | None,
    caller_org_id: UUID | None,
    is_platform_admin: bool,
    is_provider_org: bool | None = None,
) -> dict[str, bool]:
    """Delete an org mapping for an integration (shared).

    Preserves the HTTP endpoint order and nulls: a missing integration,
    a global scope, or a missing mapping all return ``{"deleted": False}``
    instead of an error. Scope-grammar/authorization failures (422/403)
    still raise.

    Args:
        session: Short-lived parent/HTTP database session.
        name: Integration name.
        scope: Requested scope string (must resolve to an org).
        caller_org_id: Authenticated caller's organization.
        is_platform_admin: Whether the caller is a platform admin.
        is_provider_org: Known provider-org membership, or None for a
            live lookup on ``session`` when the scope needs a bypass
            check (supervised-service callers).

    Returns:
        ``{"deleted": True}`` when a row was deleted, else
        ``{"deleted": False}``.

    Raises:
        ScopeResolutionError: 422/403 from the mutation scope gate.
    """
    from src.repositories.integrations import IntegrationsRepository

    repo = IntegrationsRepository(session)
    integration = await repo.get_integration_by_name(name)

    if not integration:
        logger.warning(
            f"SDK integrations.delete_mapping: integration '{log_safe(name)}' not found"
        )
        return {"deleted": False}

    # Apply the C2 gate before touching another org's mapping row.
    from shared.sdk_config import resolve_sdk_scope

    org_id = await resolve_sdk_scope(
        scope,
        caller_org_id=caller_org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )
    if org_id is None:
        return {"deleted": False}

    mapping = await repo.get_mapping_by_org(integration.id, org_id)

    if not mapping:
        logger.warning(
            f"SDK integrations.delete_mapping: mapping not found for org '{log_safe(str(org_id))}'"
        )
        return {"deleted": False}

    deleted = await repo.delete_mapping(mapping.id)
    await session.commit()

    logger.info(
        f"SDK deleted mapping for integration '{log_safe(name)}', org '{log_safe(str(org_id))}'"
    )

    return {"deleted": deleted}


async def refresh_sdk_oauth_token(
    session: AsyncSession,
    *,
    connection_name: str,
    scope: str | None,
    caller_org_id: UUID | None,
    is_platform_admin: bool,
    is_provider_org: bool | None = None,
    external: bool = False,
) -> dict[str, Any]:
    """Refresh an OAuth token for an integration (shared).

    Preserves the HTTP endpoint order: scope validation first, then the
    org-or-global provider cascade (externals get org-only), then the
    locked token lookup for ``authorization_code`` flows
    (``get_org_level_for_provider(..., for_update=True)``) so concurrent
    refreshes cannot both submit a rotating refresh token. The refresh
    HTTP itself runs through the shared
    :func:`src.services.oauth_provider.refresh_oauth_token_http`
    primitive; this service owns the context build and persistence
    (in-place update, or a new ``user_id=NULL`` row when none exists).

    Args:
        session: Short-lived parent/HTTP database session.
        connection_name: OAuth provider/connection name.
        scope: Requested scope string (None resolves to the caller's own
            org — the SDK ``refresh()`` call passes no scope).
        caller_org_id: Authenticated caller's organization.
        is_platform_admin: Whether the caller is a platform admin.
        is_provider_org: Known provider-org membership, or None for a
            live lookup on ``session`` when the scope needs a bypass
            check (supervised-service callers).
        external: True for a direct EXTERNAL portal caller — provider
            and token lookups stay org-only (OPEN-E). Engine executions
            pass False.

    Returns:
        ``{"access_token": <fresh token>, "expires_at": <ISO str | None>}``.
        The caller registers the fresh token with the SDK secret
        scrubber (the SDK facades do this on both paths).

    Raises:
        ScopeResolutionError: 422/403 from the scope gate.
        IntegrationServiceError: 404 for a missing provider, 400 when no
            refresh token is stored, 502 when the provider refresh fails
            or returns no access token.
    """
    from datetime import datetime, timezone

    from src.models.orm.oauth import OAuthToken
    from src.repositories.oauth import (
        OAuthProviderRepository,
        OAuthTokenRepository,
    )
    from src.services.oauth_provider import (
        build_token_refresh_context,
        refresh_oauth_token_http,
    )
    from shared.sdk_config import resolve_sdk_scope

    org_id = await resolve_sdk_scope(
        scope,
        caller_org_id=caller_org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
        session=session,
    )

    # Cascade: prefer org-scoped provider, fall back to global.
    # See api/src/repositories/README.md for the pattern.
    # EXTERNAL callers (OPEN-E) get org-only on BOTH the provider lookup
    # and the token lookup: a portal user must never use a global
    # provider's client credentials to mint a token. The generic name
    # cascade includes global providers even for externals, so use the
    # repository's explicit org-only lookup for this endpoint.
    provider_repo = OAuthProviderRepository(
        session,
        org_id=org_id,
        is_superuser=not external,
        is_external=external,
    )
    provider = (
        await provider_repo.get_org_level_by_provider_name(connection_name)
        if external
        else await provider_repo.get(provider_name=connection_name)
    )

    if not provider:
        raise IntegrationServiceError(
            404, f"OAuth provider '{connection_name}' not found"
        )

    # For authorization_code flow we need the stored token up front so
    # build_token_refresh_context can carry the encrypted refresh token.
    token_repo = OAuthTokenRepository(
        session,
        org_id=org_id,
        is_superuser=not external,
        is_external=external,
    )
    stored_token = None
    if provider.oauth_flow_type == "authorization_code":
        # Providers may rotate refresh tokens. Hold a row lock through
        # refresh + persistence so concurrent workflow 401 retries cannot
        # both submit the same one-time refresh token.
        stored_token = await token_repo.get_org_level_for_provider(
            provider.id,
            for_update=True,
        )
        if not stored_token or not stored_token.encrypted_refresh_token:
            raise IntegrationServiceError(
                400,
                "Cannot refresh: no refresh_token stored for this connection",
            )

    # Build the context dict and delegate to the shared primitive.
    # build_token_refresh_context handles the {entity_id} fallback chain
    # (org mapping → integration.default_entity_id → integration.entity_id)
    # in one place so the SDK endpoint, scheduler, and connections router
    # cannot drift.
    td = await build_token_refresh_context(
        db=session,
        provider=provider,
        token=stored_token,
        org_id=org_id,
    )
    outcome = await refresh_oauth_token_http(td)

    if not outcome["success"]:
        raise IntegrationServiceError(
            502, outcome.get("error", "Token refresh failed")
        )

    access_token = outcome.get("access_token")
    if not access_token:
        raise IntegrationServiceError(
            502, "Token refresh returned no access_token"
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
            expires_at=expires_at_dt
            if expires_at_dt and hasattr(expires_at_dt, "isoformat")
            else None,
            scopes=provider.scopes or [],
        )
        session.add(new_token)

    provider.status = "completed"
    provider.status_message = None
    provider.last_token_refresh = datetime.now(timezone.utc)

    await session.commit()

    logger.info(
        f"SDK refreshed OAuth token for '{log_safe(connection_name)}'"
    )

    return {"access_token": access_token, "expires_at": expires_at}
