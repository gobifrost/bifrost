"""OAuth callback handler for external MCP connections.

This is the deterministic redirect endpoint the vendor calls after the
user consents in the popup. It's mounted at ``/api/mcp/oauth/callback``
(matching the URL the connect endpoints embed in the authorize URL via
``settings.public_url``).

Behavior:

1. Decode the signed ``state`` JWT (carries connection_id, flow_type,
   pkce_verifier, optional user_id, nonce).
2. Verify the nonce is unused (Redis check-and-delete).
3. Resolve the connection's OAuth provider details.
4. Exchange the ``code`` for access + refresh tokens via the existing
   ``OAuthProviderClient.exchange_code_for_token`` (which we found
   already exists in ``src/services/oauth_provider.py``).
5. Encrypt the resulting tokens, persist via ``OAuthToken``.
6. For service flow: update ``mcp_connections.service_oauth_token_id``.
   For user flow: upsert ``user_mcp_credentials`` for the (user, connection).
7. Render an HTML page that ``window.opener.postMessage(...)`` and closes
   the popup. The caller (admin or My Connections page) listens for the
   message and refreshes its UI state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from starlette.responses import HTMLResponse

from src.core.log_safety import log_safe

from src.config import get_settings
from src.core.database import DbSession
from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.external_mcp import (
    MCPConnection,
    UserMCPCredential,
)
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.services.mcp_client.oauth_state import (
    StateDecodeError,
    consume_nonce,
    decode_state,
)
from src.services.oauth_provider import (
    OAuthProviderClient,
    get_url_resolution_defaults,
    resolve_url_template,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/oauth", tags=["MCP Connections"])


# =============================================================================
# HTML response helpers
# =============================================================================


def _popup_response(*, success: bool, connection_id: str, error: str | None = None) -> HTMLResponse:
    """Render the popup-closing page that posts back to the opener.

    Pattern matches the standard "popup OAuth flow" — the opener listens
    for a ``message`` event with ``type === 'mcp_oauth_success'`` (or
    ``'mcp_oauth_error'``) and updates its state, then we close.

    The HTML deliberately uses inline JS rather than a script tag with
    src — popups have a known content-security-policy gotcha where
    same-origin scripts may load before ``window.opener`` is available
    on slow browsers. Inline JS sidesteps it.
    """
    if success:
        message = (
            "{type: 'mcp_oauth_success', connection_id: '"
            + connection_id
            + "'}"
        )
        body_text = "Connected. You can close this window."
    else:
        # Escape any quotes the error string might carry; the simple
        # repr->slice trick works because error is server-controlled.
        safe_error = (error or "Unknown error").replace("'", "\\'")
        message = (
            "{type: 'mcp_oauth_error', connection_id: '"
            + connection_id
            + "', error: '"
            + safe_error
            + "'}"
        )
        body_text = f"Connection failed: {error or 'Unknown error'}"

    html = f"""<!doctype html>
<html><head><title>MCP OAuth</title></head>
<body>
<p>{body_text}</p>
<script>
(function() {{
  try {{
    if (window.opener) {{
      window.opener.postMessage({message}, window.location.origin);
    }}
  }} catch (e) {{
    // Cross-origin opener — best effort, parent will time out.
  }}
  setTimeout(function() {{ window.close(); }}, 100);
}})();
</script>
</body></html>"""
    return HTMLResponse(content=html, status_code=200 if success else 400)


# =============================================================================
# Token exchange + persist
# =============================================================================


async def _resolve_oauth_provider(
    connection: MCPConnection, db: DbSession
) -> OAuthProvider:
    """Fetch the OAuth provider attached to a connection's server template."""
    if connection.server is None or connection.server.oauth_provider_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection's server template has no OAuth provider",
        )
    result = await db.execute(
        select(OAuthProvider).where(
            OAuthProvider.id == connection.server.oauth_provider_id
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth provider missing for this connection",
        )
    return provider


async def _exchange_code_for_token(
    *,
    connection: MCPConnection,
    provider: OAuthProvider,
    code: str,
    pkce_verifier: str,
    redirect_uri: str,
    db: DbSession,
) -> dict[str, Any]:
    """Run the authorization-code exchange.

    Reuses ``OAuthProviderClient.exchange_code_for_token`` but adds the
    PKCE verifier (the existing helper takes ``client_secret`` and
    ``audience`` but does not currently pass ``code_verifier``). Since
    every MCP OAuth flow we ship uses PKCE, the simplest path is to
    POST directly with the existing low-level transport.
    """
    if not provider.token_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth provider missing token_url",
        )

    # Use the connection's per-org client_secret, not the provider's.
    # The provider here is shared across orgs (it's on the server template);
    # only the per-org connection holds the client_secret the user paid the
    # vendor for.
    encrypted = connection.encrypted_client_secret
    client_secret = decrypt_secret(
        encrypted.decode() if isinstance(encrypted, bytes) else encrypted
    )

    defaults = await get_url_resolution_defaults(db, provider)
    token_url = resolve_url_template(provider.token_url, defaults=defaults)

    # Build payload directly so we can include code_verifier (the existing
    # exchange_code_for_token helper doesn't accept PKCE). We still use
    # OAuthProviderClient._make_token_request for retry/parse semantics.
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": connection.client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code_verifier": pkce_verifier,
    }
    if provider.scopes:
        payload["scope"] = " ".join(provider.scopes)
    if provider.audience:
        payload["audience"] = provider.audience

    client = OAuthProviderClient()
    success, result = await client._make_token_request(token_url, payload)
    if not success:
        error_desc = result.get("error_description") or result.get("error") or "exchange failed"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token exchange failed: {error_desc}",
        )
    return result


async def _persist_token(
    *,
    provider_id: UUID,
    organization_id: UUID,
    user_id: UUID | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
    scopes: list[str],
    db: DbSession,
) -> OAuthToken:
    """Insert a new ``OAuthToken`` row carrying the freshly-exchanged tokens.

    We always insert a new row rather than update — at the OAuth-callback
    moment we don't know if a previous token exists for this scope, and
    callers that want to invalidate the old one drop the FK reference and
    the row becomes orphaned (cleaned by garbage-collection later). This
    matches the "rotate on consent" pattern in the spec.
    """
    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    token = OAuthToken(
        organization_id=organization_id,
        provider_id=provider_id,
        user_id=user_id,
        encrypted_access_token=encrypt_secret(access_token).encode(),
        encrypted_refresh_token=(
            encrypt_secret(refresh_token).encode() if refresh_token else None
        ),
        expires_at=expires_at,
        scopes=scopes,
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    return token


async def _upsert_user_credential(
    *,
    user_id: UUID,
    connection_id: UUID,
    oauth_token_id: UUID,
    granted_scopes: list[str],
    db: DbSession,
) -> None:
    """Insert or update the per-user credential row for (user, connection).

    The unique index is (user_id, connection_id) — if a row already
    exists we replace the FK target so the user always points at their
    most recent token.
    """
    result = await db.execute(
        select(UserMCPCredential).where(
            UserMCPCredential.user_id == user_id,
            UserMCPCredential.connection_id == connection_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.oauth_token_id = oauth_token_id
        existing.consent_granted_at = datetime.now(timezone.utc)
        existing.granted_scopes = granted_scopes
    else:
        credential = UserMCPCredential(
            user_id=user_id,
            connection_id=connection_id,
            oauth_token_id=oauth_token_id,
            consent_granted_at=datetime.now(timezone.utc),
            granted_scopes=granted_scopes,
        )
        db.add(credential)
    await db.flush()


# =============================================================================
# Endpoint
# =============================================================================


@router.get(
    "/callback",
    summary="MCP OAuth callback",
    description=(
        "Vendor-facing redirect URL. Decodes ``state``, exchanges "
        "``code`` for tokens, persists them, then renders an HTML page "
        "that messages ``window.opener`` and closes itself."
    ),
    include_in_schema=False,
)
async def mcp_oauth_callback(
    db: DbSession,
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """Handle the vendor's redirect after the user consents (or refuses)."""
    # Vendor-side error: surface immediately without any state work.
    if error:
        logger.info("MCP OAuth callback received vendor error: %s", log_safe(error))
        return _popup_response(
            success=False,
            connection_id="",
            error=f"{error}: {error_description}" if error_description else error,
        )

    # 1. Decode + verify state
    try:
        payload = decode_state(state)
    except StateDecodeError as exc:
        logger.warning(
            "MCP OAuth callback: invalid state (%s)", log_safe(str(exc))
        )
        return _popup_response(success=False, connection_id="", error=str(exc))

    nonce = payload["nonce"]
    connection_id = UUID(payload["connection_id"])
    flow_type = payload["flow_type"]
    pkce_verifier = payload["pkce_verifier"]
    redirect_uri = payload.get("redirect_uri")
    user_id = UUID(payload["user_id"]) if "user_id" in payload else None

    # 2. Single-use nonce check
    if not await consume_nonce(nonce):
        logger.warning(
            "MCP OAuth callback: state nonce already used or unknown (%s…)",
            log_safe(nonce[:8]),
        )
        return _popup_response(
            success=False,
            connection_id=str(connection_id),
            error="state already used or expired",
        )

    if redirect_uri is None:
        # Older state token without redirect_uri (shouldn't happen post-deploy);
        # fall back to the deterministic public URL.
        public_url = get_settings().public_url.rstrip("/")
        redirect_uri = f"{public_url}/api/mcp/oauth/callback"

    # 3. Resolve the connection + provider
    result = await db.execute(
        select(MCPConnection).where(MCPConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        return _popup_response(
            success=False,
            connection_id=str(connection_id),
            error="connection not found",
        )

    try:
        provider = await _resolve_oauth_provider(connection, db)
    except HTTPException as exc:
        return _popup_response(
            success=False,
            connection_id=str(connection_id),
            error=exc.detail if isinstance(exc.detail, str) else "provider error",
        )

    # 4. Exchange code for tokens
    try:
        token_result = await _exchange_code_for_token(
            connection=connection,
            provider=provider,
            code=code,
            pkce_verifier=pkce_verifier,
            redirect_uri=redirect_uri,
            db=db,
        )
    except HTTPException as exc:
        return _popup_response(
            success=False,
            connection_id=str(connection_id),
            error=exc.detail if isinstance(exc.detail, str) else "exchange failed",
        )

    access_token = token_result.get("access_token")
    if not access_token:
        return _popup_response(
            success=False,
            connection_id=str(connection_id),
            error="no access_token in response",
        )

    refresh_token = token_result.get("refresh_token")
    expires_at = token_result.get("expires_at")
    scope = token_result.get("scope") or ""
    granted_scopes = scope.split() if scope else list(provider.scopes or [])

    # 5. Persist token
    token = await _persist_token(
        provider_id=provider.id,
        organization_id=connection.organization_id,
        user_id=user_id,  # NULL for service flow
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=granted_scopes,
        db=db,
    )

    # 6. Wire to the right entity
    if flow_type == "service":
        connection.service_oauth_token_id = token.id
        await db.flush()
        logger.info(
            f"MCP OAuth callback: stored service token for connection {connection_id}"
        )
    else:
        assert user_id is not None  # decode_state already enforced this
        await _upsert_user_credential(
            user_id=user_id,
            connection_id=connection_id,
            oauth_token_id=token.id,
            granted_scopes=granted_scopes,
            db=db,
        )
        logger.info(
            f"MCP OAuth callback: stored user credential for user {user_id}, "
            f"connection {connection_id}"
        )

    await db.commit()

    return _popup_response(success=True, connection_id=str(connection_id))
