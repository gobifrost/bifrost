"""
OAuth Provider Client
Handles HTTP communication with OAuth providers for token exchange and refresh
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

import aiohttp
from sqlalchemy import select

from src.core.security import decrypt_secret, encrypt_secret

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.orm.oauth import OAuthProvider, OAuthToken

logger = logging.getLogger(__name__)


def resolve_url_template(
    url: str,
    entity_id: str | None = None,
    defaults: dict[str, str] | None = None
) -> str:
    """
    Replace {placeholders} in URL with values.
    Falls back to defaults if value not provided.

    Args:
        url: URL template with {placeholder} syntax
        entity_id: Value for {entity_id} placeholder
        defaults: Default values for placeholders when not provided
                 Example: {"entity_id": "common"}

    Returns:
        URL with placeholders resolved

    Examples:
        >>> resolve_url_template(
        ...     "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token",
        ...     entity_id="tenant-123"
        ... )
        'https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token'

        >>> resolve_url_template(
        ...     "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token",
        ...     defaults={"entity_id": "common"}
        ... )
        'https://login.microsoftonline.com/common/oauth2/v2.0/token'
    """
    if not url:
        return url

    # Check if URL contains any placeholders
    if "{" not in url:
        logger.debug(f"URL has no placeholders: {url}")
        return url

    result = url
    defaults = defaults or {}

    # Find all placeholders in the URL
    placeholders = re.findall(r"\{(\w+)\}", url)

    for placeholder in placeholders:
        # Determine the replacement value
        replacement = None

        if placeholder == "entity_id" and entity_id:
            replacement = entity_id
            logger.debug(
                f"Resolving {{entity_id}} in URL with provided entity_id: {entity_id}"
            )
        elif placeholder in defaults:
            replacement = defaults[placeholder]
            logger.debug(
                f"Resolving {{{placeholder}}} in URL with default value: {replacement}"
            )
        else:
            logger.warning(
                f"URL placeholder {{{placeholder}}} has no value or default, leaving unresolved"
            )
            continue

        # Replace the placeholder
        result = result.replace(f"{{{placeholder}}}", replacement)

    return result


def append_query_params(url: str, params: dict[str, Any]) -> str:
    """
    Append query parameters to a URL, preserving any existing query string.

    Uses ``&`` as the separator when the URL already contains ``?``, and ``?``
    otherwise. This matters for authorize URLs that bake provider-specific
    flags into the configured template (e.g. Google's ``access_type=offline``
    or Microsoft's ``prompt=login``) — without this, a naive ``f"{url}?{...}"``
    produces ``...?baked=1?client_id=...`` which providers reject.

    Args:
        url: Base URL, with or without an existing query string.
        params: Mapping of parameter names to values to append.

    Returns:
        URL with the new parameters appended.

    Examples:
        >>> append_query_params("https://example.com/auth", {"client_id": "abc"})
        'https://example.com/auth?client_id=abc'

        >>> append_query_params(
        ...     "https://accounts.google.com/o/oauth2/v2/auth?access_type=offline",
        ...     {"client_id": "abc"},
        ... )
        'https://accounts.google.com/o/oauth2/v2/auth?access_type=offline&client_id=abc'
    """
    from urllib.parse import urlencode

    if not params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


class OAuthProviderClient:
    """
    Client for interacting with OAuth 2.0 providers

    Features:
    - Token exchange (authorization code → access token)
    - Token refresh (refresh token → new access token)
    - Client credentials flow
    - Retry logic with exponential backoff
    - Timeout handling
    """

    def __init__(self, timeout: int = 10, max_retries: int = 3):
        """
        Initialize OAuth provider client

        Args:
            timeout: Request timeout in seconds (default: 10)
            max_retries: Maximum number of retry attempts (default: 3)
        """
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        logger.debug(f"OAuthProviderClient initialized (timeout={timeout}s, max_retries={max_retries})")

    async def exchange_code_for_token(
        self,
        token_url: str,
        code: str,
        client_id: str,
        client_secret: str | None,
        redirect_uri: str,
        scopes: str | None = None,
        audience: str | None = None,
    ) -> tuple[bool, dict]:
        """
        Exchange authorization code for access token (authorization code flow)

        Args:
            token_url: OAuth provider's token endpoint
            code: Authorization code from OAuth callback
            client_id: OAuth client ID
            client_secret: OAuth client secret (optional, omit for PKCE flow)
            redirect_uri: Redirect URI used in authorization request

        Returns:
            Tuple of (success, result_dict)
            - If success: result_dict contains token data
            - If failure: result_dict contains error information
        """
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri
        }

        # Only include client_secret if provided (PKCE flow omits this)
        if client_secret:
            payload["client_secret"] = client_secret
            logger.info(f"Exchanging authorization code for token at {token_url} (with client_secret)")
        else:
            logger.info(f"Exchanging authorization code for token at {token_url} (PKCE flow - no client_secret)")

        if audience:
            payload["audience"] = audience

        if scopes:
            payload["scope"] = scopes

        return await self._make_token_request(token_url, payload)

    async def refresh_access_token(
        self,
        token_url: str,
        refresh_token: str,
        client_id: str,
        client_secret: str | None,
        scopes: str | None = None,
        audience: str | None = None,
    ) -> tuple[bool, dict]:
        """
        Refresh access token using refresh token

        Args:
            token_url: OAuth provider's token endpoint
            refresh_token: Refresh token
            client_id: OAuth client ID
            client_secret: OAuth client secret (optional, omit for PKCE flow)

        Returns:
            Tuple of (success, result_dict)
        """
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id
        }

        # Only include client_secret if provided (PKCE flow omits this)
        if client_secret:
            payload["client_secret"] = client_secret
            logger.info(f"Refreshing access token at {token_url} (with client_secret)")
        else:
            logger.info(f"Refreshing access token at {token_url} (PKCE flow - no client_secret)")

        if audience:
            payload["audience"] = audience

        if scopes:
            payload["scope"] = scopes

        return await self._make_token_request(token_url, payload)

    async def get_client_credentials_token(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: str = "",
        audience: str | None = None,
    ) -> tuple[bool, dict]:
        """
        Get token using client credentials flow (service-to-service)

        Args:
            token_url: OAuth provider's token endpoint
            client_id: OAuth client ID
            client_secret: OAuth client secret
            scopes: Space or comma-separated list of scopes

        Returns:
            Tuple of (success, result_dict)
        """
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }

        if scopes:
            # Normalize scopes to space-separated (OAuth 2.0 standard)
            payload["scope"] = scopes.replace(",", " ")

        if audience:
            payload["audience"] = audience

        logger.info(f"Requesting client credentials token at {token_url}")

        return await self._make_token_request(token_url, payload)

    async def _make_token_request(
        self,
        token_url: str,
        payload: dict
    ) -> tuple[bool, dict]:
        """
        Make token request with retry logic

        Args:
            token_url: Token endpoint URL
            payload: Request payload

        Returns:
            Tuple of (success, result_dict)
        """
        last_error = None

        for attempt in range(self.max_retries):
            try:
                # Create connector with proper cleanup settings
                connector = aiohttp.TCPConnector(force_close=True)
                async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
                    async with session.post(
                        token_url,
                        data=payload,
                        headers={"Content-Type": "application/x-www-form-urlencoded"}
                    ) as response:
                        response_data = await response.json()

                        # Success (2xx status codes)
                        if 200 <= response.status < 300:
                            logger.info(f"Token request successful (status={response.status})")
                            logger.debug(f"Raw OAuth response keys: {list(response_data.keys())}")

                            # Parse token response
                            result = self._parse_token_response(response_data)
                            return (True, result)

                        # Client errors (4xx) - don't retry
                        elif 400 <= response.status < 500:
                            error_msg = response_data.get("error_description") or response_data.get("error") or f"HTTP {response.status}"
                            logger.error(f"Token request failed with client error: {error_msg}")
                            return (False, {
                                "error": response_data.get("error", "client_error"),
                                "error_description": error_msg,
                                "status_code": response.status
                            })

                        # Server errors (5xx) - retry
                        else:
                            error_msg = f"Server error: HTTP {response.status}"
                            logger.warning(f"Token request failed: {error_msg} (attempt {attempt + 1}/{self.max_retries})")
                            last_error = error_msg

                            if attempt < self.max_retries - 1:
                                # Exponential backoff: 1s, 2s, 4s
                                wait_time = 2 ** attempt
                                await asyncio.sleep(wait_time)
                                continue

            except aiohttp.ClientError as e:
                logger.warning(f"Network error during token request: {str(e)} (attempt {attempt + 1}/{self.max_retries})")
                last_error = str(e)

                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue

            except TimeoutError:
                logger.warning(f"Token request timed out (attempt {attempt + 1}/{self.max_retries})")
                last_error = "Request timed out"

                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue

            except Exception as e:
                logger.error(f"Unexpected error during token request: {str(e)}", exc_info=True)
                return (False, {
                    "error": "unexpected_error",
                    "error_description": str(e)
                })

        # All retries exhausted
        logger.error(f"Token request failed after {self.max_retries} attempts: {last_error}")
        return (False, {
            "error": "max_retries_exceeded",
            "error_description": f"Failed after {self.max_retries} attempts: {last_error}"
        })

    def _parse_token_response(self, response_data: dict) -> dict:
        """
        Parse OAuth token response and calculate expiration.

        Pass-through by default: every field the provider returned is preserved
        on the result dict so downstream code (entity_id capture, picker
        enumeration) can see id_token, realmId, team.*, and anything else
        provider-specific. Only ``expires_at`` is derived (from ``expires_in``)
        and the standard fields are surfaced as explicit keys with their
        documented defaults. The picker/extractor apply their own deny-list
        scrubbing — we don't drop fields here.
        """
        result: dict = dict(response_data)
        result["access_token"] = response_data.get("access_token")
        result["token_type"] = response_data.get("token_type", "Bearer")
        result["refresh_token"] = response_data.get("refresh_token")
        result["scope"] = response_data.get("scope", "")

        # Calculate expires_at from expires_in (seconds)
        expires_in = response_data.get("expires_in")
        if expires_in:
            result["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        else:
            # Default to 1 hour if not specified
            logger.warning("OAuth response missing expires_in, defaulting to 1 hour")
            result["expires_at"] = datetime.now(timezone.utc) + timedelta(hours=1)

        # Log refresh token presence at INFO level for debugging
        if result['refresh_token'] is not None:
            logger.info("✓ Token response includes refresh_token")
        else:
            logger.info("✗ Token response does NOT include refresh_token (will use fallback if available)")

        logger.debug(f"Parsed token response: expires_at={result['expires_at']}, has_refresh={result['refresh_token'] is not None}")

        return result


# =============================================================================
# Shared OAuth refresh primitives
#
# These helpers are the single source of truth for OAuth token refresh used
# by all three refresh entry points:
#   - the scheduler (src/jobs/schedulers/oauth_token_refresh.py)
#   - the SDK endpoint (src/routers/cli.py:sdk_integrations_refresh_token)
#   - the connections router (src/routers/oauth_connections.py:refresh_token)
#
# The {entity_id} URL placeholder fallback lives in exactly one place here
# (build_token_refresh_context) so the three sites cannot drift.
# =============================================================================


async def get_url_resolution_defaults(
    db: "AsyncSession",
    provider: "OAuthProvider",
) -> dict[str, str]:
    """
    Build the ``defaults`` dict for :func:`resolve_url_template` from a
    provider and its linked integration.

    This is the shared helper used by non-refresh OAuth URL resolution paths
    (authorize URL building, callback token exchange). Refresh paths should
    use :func:`build_token_refresh_context` instead, which also carries the
    encrypted secrets and token metadata.

    Resolves ``{entity_id}`` via the integration fallback chain
    (``default_entity_id`` → ``entity_id``). Note that the authorize/callback
    flows don't have an org context, so the org-mapping lookup is skipped.
    """
    from src.models.orm import Integration

    defaults: dict[str, str] = (
        dict(provider.token_url_defaults) if provider.token_url_defaults else {}
    )

    if provider.integration_id:
        result = await db.execute(
            select(Integration).where(Integration.id == provider.integration_id)
        )
        integration = result.scalar_one_or_none()
        if integration:
            resolved = integration.default_entity_id or integration.entity_id
            if resolved:
                defaults["entity_id"] = resolved

    return defaults


async def build_token_refresh_context(
    db: "AsyncSession",
    provider: "OAuthProvider",
    token: "OAuthToken | None" = None,
    org_id: UUID | None = None,
) -> dict[str, Any]:
    """
    Build the token context dict consumed by ``refresh_oauth_token_http``.

    Handles ``{entity_id}`` URL placeholder resolution with the canonical
    fallback chain:

      1. org-scoped integration mapping's ``entity_id`` (if ``org_id`` is
         provided and a mapping exists for this integration)
      2. ``integration.default_entity_id``
      3. ``integration.entity_id``

    The resolved value is injected into the returned ``token_url_defaults``
    dict under the ``entity_id`` key so ``resolve_url_template`` picks it up
    via the ``defaults`` path (keeping all three refresh sites on the same
    signature).

    Args:
        db: Active async session (used to load the linked integration and,
            if ``org_id`` is set, the org mapping).
        provider: The ``OAuthProvider`` being refreshed.
        token: Optional stored ``OAuthToken`` (required for authorization_code
            flows, ignored for client_credentials).
        org_id: Optional organization context; when set, an org mapping is
            consulted before falling back to the integration defaults.

    Returns:
        A dict matching the shape ``refresh_oauth_token_http`` expects.
    """
    from src.models.orm import Integration, IntegrationMapping

    token_url_defaults: dict[str, str] = (
        dict(provider.token_url_defaults) if provider.token_url_defaults else {}
    )

    resolved_entity_id: str | None = None
    if provider.integration_id:
        # 1. Org mapping (explicit per-tenant override)
        if org_id is not None:
            mapping_result = await db.execute(
                select(IntegrationMapping).where(
                    IntegrationMapping.integration_id == provider.integration_id,
                    IntegrationMapping.organization_id == org_id,
                )
            )
            mapping = mapping_result.scalar_one_or_none()
            if mapping and mapping.entity_id:
                resolved_entity_id = mapping.entity_id

        # 2/3. Integration defaults — always consulted if mapping didn't resolve
        if resolved_entity_id is None:
            integration_result = await db.execute(
                select(Integration).where(Integration.id == provider.integration_id)
            )
            integration = integration_result.scalar_one_or_none()
            if integration:
                resolved_entity_id = (
                    integration.default_entity_id or integration.entity_id
                )

    if resolved_entity_id:
        token_url_defaults["entity_id"] = resolved_entity_id

    return {
        "token_id": token.id if token else None,
        "provider_id": provider.id,
        "provider_name": provider.provider_name,
        "oauth_flow_type": provider.oauth_flow_type,
        "client_id": provider.client_id,
        "encrypted_client_secret": provider.encrypted_client_secret,
        "token_url": provider.token_url,
        "token_url_defaults": token_url_defaults,
        "scopes": provider.scopes,
        "audience": provider.audience,
        "encrypted_refresh_token": token.encrypted_refresh_token if token else None,
    }


async def refresh_oauth_token_http(td: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a single OAuth token refresh over HTTP.

    Does **not** touch the database. Callers supply a context dict built
    by ``build_token_refresh_context`` (or an equivalently-shaped dict for
    the scheduler's batch loop) and handle persistence themselves because
    the three call sites have divergent persistence shapes (cache
    invalidation, repo.store_token vs in-place update, different response
    contracts).

    Handles URL template resolution, client_secret decryption, and the
    ``client_credentials`` vs ``authorization_code`` branching.

    Args:
        td: Token context dict. See ``build_token_refresh_context`` for the
            expected shape.

    Returns:
        An outcome dict with at minimum ``success: bool``. On success, also
        includes ``encrypted_access_token``, ``expires_at``, optionally
        ``encrypted_refresh_token`` and ``scopes``. On failure, includes
        ``error: str``.
    """
    outcome: dict[str, Any] = {
        "token_id": td.get("token_id"),
        "provider_id": td["provider_id"],
        "success": False,
    }

    try:
        client_secret: str | None = None
        if td["encrypted_client_secret"]:
            raw = td["encrypted_client_secret"]
            client_secret = decrypt_secret(
                raw.decode() if isinstance(raw, bytes) else raw
            )

        if not td["token_url"]:
            outcome["error"] = (
                f"No token URL configured for provider {td['provider_name']}"
            )
            return outcome

        # Resolve URL template placeholders via the defaults dict.
        # entity_id (when present) was injected into token_url_defaults by
        # build_token_refresh_context, so this single call path is shared.
        token_url = resolve_url_template(
            url=td["token_url"],
            defaults=td["token_url_defaults"],
        )

        oauth_client = OAuthProviderClient()

        if td["oauth_flow_type"] == "client_credentials":
            if not client_secret:
                outcome["error"] = (
                    f"No client secret for client_credentials provider "
                    f"{td['provider_name']}"
                )
                return outcome

            scopes = " ".join(td["scopes"]) if td["scopes"] else ""
            success, result = await oauth_client.get_client_credentials_token(
                token_url=token_url,
                client_id=td["client_id"],
                client_secret=client_secret,
                scopes=scopes,
                audience=td["audience"],
            )
        else:
            if not td["encrypted_refresh_token"]:
                outcome["error"] = (
                    f"No refresh token for token {td.get('token_id')}"
                )
                return outcome

            raw_refresh = td["encrypted_refresh_token"]
            refresh_token_value = decrypt_secret(
                raw_refresh.decode() if isinstance(raw_refresh, bytes) else raw_refresh
            )
            success, result = await oauth_client.refresh_access_token(
                token_url=token_url,
                refresh_token=refresh_token_value,
                client_id=td["client_id"],
                client_secret=client_secret,
                audience=td["audience"],
            )

        if not success:
            error_msg = result.get(
                "error_description", result.get("error", "Refresh failed")
            )
            outcome["error"] = f"Token refresh failed: {error_msg}"
            return outcome

        new_access_token = result.get("access_token")
        if not new_access_token:
            outcome["error"] = (
                f"No access token in refresh response for {td['provider_name']}"
            )
            return outcome

        outcome["success"] = True
        outcome["access_token"] = new_access_token
        outcome["encrypted_access_token"] = encrypt_secret(new_access_token).encode()
        outcome["expires_at"] = result.get("expires_at")

        # Only authorization_code flow rotates refresh tokens
        if td["oauth_flow_type"] != "client_credentials":
            old_refresh = (
                decrypt_secret(
                    td["encrypted_refresh_token"].decode()
                    if isinstance(td["encrypted_refresh_token"], bytes)
                    else td["encrypted_refresh_token"]
                )
                if td["encrypted_refresh_token"]
                else None
            )
            new_refresh = result.get("refresh_token") or old_refresh
            if new_refresh:
                outcome["refresh_token"] = new_refresh
                outcome["encrypted_refresh_token"] = encrypt_secret(
                    new_refresh
                ).encode()

        new_scopes = result.get("scope")
        if new_scopes:
            outcome["scopes"] = new_scopes.split(" ")

        return outcome

    except Exception as e:
        logger.error(f"Error refreshing OAuth token: {e}", exc_info=True)
        outcome["error"] = f"Token refresh failed: {str(e)[:200]}"
        return outcome


async def get_token_for_org(
    db: "AsyncSession",
    integration_id: UUID,
    org_id: UUID,
) -> "OAuthToken | None":
    """Resolve the OAuth token to use for (integration, org) at workflow runtime.

    Priority:
    1. The integration mapping's ``oauth_token_id`` (per-row connect)
    2. The integration-level token (provider's most-recent token with
       organization_id IS NULL)
    3. None — caller must surface a clear error
    """
    # Function-local imports to match the existing pattern in this module
    # and avoid circular import risk (same pattern as build_token_refresh_context).
    from src.models.orm import OAuthToken
    from src.models.orm.integrations import IntegrationMapping
    from src.models.orm.oauth import OAuthProvider

    # 1. Mapping-scoped token
    mapping_result = await db.execute(
        select(IntegrationMapping).where(
            IntegrationMapping.integration_id == integration_id,
            IntegrationMapping.organization_id == org_id,
        )
    )
    mapping = mapping_result.scalar_one_or_none()
    if mapping and mapping.oauth_token_id:
        token = await db.get(OAuthToken, mapping.oauth_token_id)
        if token:
            return token

    # 2. Integration-level fallback — most-recent token with organization_id IS NULL
    #    for the provider linked to this integration.
    provider_result = await db.execute(
        select(OAuthProvider).where(OAuthProvider.integration_id == integration_id)
    )
    provider = provider_result.scalar_one_or_none()
    if not provider:
        return None

    fallback_result = await db.execute(
        select(OAuthToken)
        .where(
            OAuthToken.provider_id == provider.id,
            OAuthToken.organization_id.is_(None),
        )
        .order_by(OAuthToken.created_at.desc())
    )
    return fallback_result.scalar_one_or_none()
