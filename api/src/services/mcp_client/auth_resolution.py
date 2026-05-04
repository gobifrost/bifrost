"""Token selection for external MCP dispatch — the 5-path resolution table.

There is one place that decides whether the vendor sees the user identity
or the service identity: ``resolve_token`` here. Both the chat executor
and the autonomous executor route through it. The five paths match the
table in the v2 spec (``docs/superpowers/specs/2026-04-25-external-mcp-client-design.md``)
§ Auth resolution and the wireframe in ``/tmp/bifrost-mcp-mockup.html`` §10.

| # | caller_user_id | per-user creds healthy | flag                           | outcome             |
|---|---|---|---|---|
| 1 | present        | yes                    | —                              | USER_TOKEN          |
| 2 | present        | no                     | available_in_chat + svc healthy| SERVICE_FALLBACK_CHAT |
| 3 | present        | no                     | no fallback                    | NeedsReauthError    |
| 4 | None           | n/a                    | available_to_autonomous + svc  | SERVICE_FALLBACK_AUTONOMOUS |
| 5 | None           | n/a                    | none of the above              | MisconfigError      |

Path 5 must never reach this function in normal operation — the planner
filters MCP tools out at ``resolve_agent_tools()`` when no token can
resolve. We raise ``MisconfigError`` rather than silently fail-closed so
the bug is visible.

Token freshness check: if a token's ``expires_at`` is in the past (or
within a small skew margin), we attempt a single refresh through the
shared ``oauth_provider`` primitives. On refresh success, the new token
is persisted via the same path the scheduler uses. On refresh failure,
we treat the credential as unrecoverable and proceed to the fallback
path (or raise NeedsReauthError if no fallback exists).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.security import decrypt_secret
from src.models.orm.external_mcp import MCPConnection, UserMCPCredential
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.services.mcp_client.errors import MisconfigError, NeedsReauthError
from src.services.oauth_provider import (
    build_token_refresh_context,
    refresh_oauth_token_http,
)

logger = logging.getLogger(__name__)


# Tokens within this margin of expiration are treated as expired and
# eligible for refresh. Matches the scheduler's REFRESH_BUFFER_MINUTES so
# the dispatch-time check is consistent with the background sweep.
_TOKEN_EXPIRY_MARGIN = timedelta(minutes=5)


class ResolutionPath(StrEnum):
    """Which of the five auth resolution paths fired.

    Returned alongside the access token so the dispatch layer can record
    the resolution decision in the audit log — auditors need to know
    "did this run use the user's permissions or the service account's?"
    """

    USER_TOKEN = "user_token"
    SERVICE_FALLBACK_CHAT = "service_fallback_chat"
    SERVICE_FALLBACK_AUTONOMOUS = "service_fallback_autonomous"
    NEEDS_REAUTH = "needs_reauth"
    MISCONFIG = "misconfig"


def _is_token_fresh(token: OAuthToken) -> bool:
    """A token is fresh if ``expires_at`` is in the future (with a margin).

    ``expires_at IS NULL`` is treated as fresh — some vendors don't return
    ``expires_in`` and we don't know when the token expires; we'll let the
    vendor reject it on first use rather than guess.
    """
    if token.expires_at is None:
        return True
    return token.expires_at > datetime.now(timezone.utc) + _TOKEN_EXPIRY_MARGIN


async def _refresh_token_in_place(
    token: OAuthToken,
    provider: OAuthProvider,
    db: AsyncSession,
) -> bool:
    """Attempt a single refresh of an OAuthToken; persist on success.

    Returns True if the token now holds a fresh access token, False if
    refresh failed (network error, expired refresh token, etc.). The
    caller is responsible for falling through to the next resolution
    path on False.

    This intentionally re-uses the scheduler's primitives — the only
    place in the codebase that knows how to talk to OAuth providers.
    """
    try:
        td = await build_token_refresh_context(
            db=db, provider=provider, token=token, org_id=None
        )
        outcome = await refresh_oauth_token_http(td)
    except Exception:
        # Don't log the exception verbatim — OAuth provider error responses
        # propagated through here can echo client tokens back. Caller logs
        # the resolution failure with a stable category.
        logger.warning("MCP auth: refresh raised for token %s", token.id)
        return False

    if not outcome.get("success"):
        # Don't log outcome["error"] verbatim — OAuth provider error responses can
        # echo client tokens or other sensitive material back. We only need to
        # know that refresh failed; the error type/category is captured upstream
        # by the OAuth provider service's own logs.
        logger.info("MCP auth: refresh failed for token %s", token.id)
        return False

    token.encrypted_access_token = outcome["encrypted_access_token"]
    token.expires_at = outcome["expires_at"]
    if outcome.get("encrypted_refresh_token"):
        token.encrypted_refresh_token = outcome["encrypted_refresh_token"]
    if outcome.get("scopes"):
        token.scopes = outcome["scopes"]
    await db.commit()
    return True


def _decode_access_token(token: OAuthToken) -> str:
    """Decrypt the access token bytes from an OAuthToken row."""
    raw = token.encrypted_access_token
    return decrypt_secret(raw.decode() if isinstance(raw, bytes) else raw)


async def _service_token_healthy(
    connection: MCPConnection, db: AsyncSession
) -> tuple[OAuthToken, OAuthProvider] | None:
    """Return the healthy service token for a connection, or None.

    "Healthy" means the row exists, refreshes successfully if expired,
    and yields a usable access token after refresh. The check is cheap
    (one read of an already-eager-loaded relationship + at most one
    refresh round-trip) and is deliberately not cached — a vendor
    revoking a token mid-conversation should fail-closed on the next
    call, not serve stale "healthy" verdicts.
    """
    token = connection.service_oauth_token
    if token is None:
        return None

    # Load the provider so we can refresh if needed. The token's provider
    # is not eager-loaded by default on this relationship.
    result = await db.execute(
        select(OAuthProvider).where(OAuthProvider.id == token.provider_id)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        logger.warning(
            "MCP auth: service token %s on connection %s has no provider",
            token.id,
            connection.id,
        )
        return None

    if _is_token_fresh(token):
        return token, provider

    if await _refresh_token_in_place(token, provider, db):
        return token, provider

    return None


async def _user_token_healthy(
    connection: MCPConnection,
    caller_user_id: UUID,
    db: AsyncSession,
) -> OAuthToken | None:
    """Return the caller's healthy per-user token, or None.

    Looks up the ``UserMCPCredential`` row for ``(caller_user_id,
    connection.id)``, joins to the underlying ``OAuthToken``, and
    refreshes if expired.
    """
    result = await db.execute(
        select(UserMCPCredential)
        .where(
            UserMCPCredential.user_id == caller_user_id,
            UserMCPCredential.connection_id == connection.id,
        )
        .options(selectinload(UserMCPCredential.oauth_token))
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        return None

    token = credential.oauth_token
    if token is None:
        return None

    if _is_token_fresh(token):
        return token

    provider_result = await db.execute(
        select(OAuthProvider).where(OAuthProvider.id == token.provider_id)
    )
    provider = provider_result.scalar_one_or_none()
    if provider is None:
        return None

    if await _refresh_token_in_place(token, provider, db):
        return token

    return None


def _build_reauth_url(connection: MCPConnection) -> str:
    """Server-relative URL the chat surface opens to start per-user reauth.

    The frontend opens this verbatim — it does not assemble OAuth state
    itself. Phase 4 (the OAuth router) builds the actual vendor authorize
    URL behind this endpoint, primes PKCE/state, and 302s the popup.
    """
    return f"/me/connections/{connection.id}/connect"


async def resolve_token(
    connection: MCPConnection,
    caller_user_id: UUID | None,
    db: AsyncSession,
) -> tuple[str, ResolutionPath]:
    """Resolve which access token to use for an MCP dispatch.

    Args:
        connection: The ``MCPConnection`` row. Its ``service_oauth_token``
            relationship must already be loaded (the ORM does this with
            ``lazy="joined"`` on the FK).
        caller_user_id: The user invoking the tool, or ``None`` for an
            autonomous run (scheduled, webhook without user claim).
        db: Active async session. Refreshes that succeed are committed
            here; the caller's outer transaction sees the updated tokens.

    Returns:
        ``(access_token, resolution_path)``. The path enum is recorded by
        the dispatch layer in the per-call audit row.

    Raises:
        NeedsReauthError: Path 3 — chat caller has no fallback.
        MisconfigError: Path 5 — autonomous caller hit a connection that
            wasn't filtered at planning. This is a Bifrost-side bug, not
            an operator-side misconfig despite the name.
    """
    # Determine if this connection's OAuth flow allows per-user delegation.
    # client_credentials has NO per-user mode — there's no user OAuth flow
    # to invoke; the only credential is the service token.
    provider = (
        connection.server.oauth_provider
        if connection.server is not None
        else None
    )
    flow_type = provider.oauth_flow_type if provider else None
    user_delegation_possible = flow_type != "client_credentials"

    if caller_user_id is not None:
        # Path 1: per-user credential healthy (chat OR webhook with claim).
        # Only meaningful for authorization_code flow; for client_credentials
        # there's no user OAuth path so this lookup is skipped.
        if user_delegation_possible:
            user_token = await _user_token_healthy(
                connection, caller_user_id, db
            )
            if user_token is not None:
                return (
                    _decode_access_token(user_token),
                    ResolutionPath.USER_TOKEN,
                )

        # Path 2: chat fallback to service token
        if connection.available_in_chat:
            healthy = await _service_token_healthy(connection, db)
            if healthy is not None:
                token, _ = healthy
                return (
                    _decode_access_token(token),
                    ResolutionPath.SERVICE_FALLBACK_CHAT,
                )

        # Path 3: chat caller, no fallback. For authorization_code flows the
        # user can recover by re-OAuthing personally → NeedsReauthError. For
        # client_credentials there's no user-facing recovery; only an admin
        # can fix by enabling the Available-in-chat flag → MisconfigError.
        if not user_delegation_possible:
            raise MisconfigError(
                connection_id=connection.id,
                reason=(
                    "Connection's OAuth flow is client_credentials "
                    "(server-to-server only) and 'Available in user chat' "
                    "is disabled. Admin must enable that flag for chat "
                    "users to invoke these tools — there is no per-user "
                    "OAuth path for this connection."
                ),
            )
        raise NeedsReauthError(
            reauth_url=_build_reauth_url(connection),
            connection_id=connection.id,
        )

    # Path 4: autonomous + flag set + service token healthy
    if connection.available_to_autonomous:
        healthy = await _service_token_healthy(connection, db)
        if healthy is not None:
            token, _ = healthy
            return (
                _decode_access_token(token),
                ResolutionPath.SERVICE_FALLBACK_AUTONOMOUS,
            )

    # Path 5: should never reach here — planner missed a case
    raise MisconfigError(
        connection_id=connection.id,
        reason=(
            "autonomous caller reached dispatch on a connection without "
            "available_to_autonomous=True and a healthy service token; "
            "resolve_agent_tools should have filtered this tool out at planning"
        ),
    )
