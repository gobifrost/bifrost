"""
OAuth Token Refresh Scheduler

Automatically refreshes OAuth tokens that are about to expire.
Runs every 15 minutes to check for tokens expiring before the next run.

Ported from Azure Functions timer trigger: functions/timer/oauth_refresh_timer.py
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.core.database import get_db_context
from src.models import OAuthToken, OAuthProvider
from src.services.oauth_provider import (
    build_token_refresh_context,
    refresh_oauth_token_http,
)

logger = logging.getLogger(__name__)

# Refresh interval and buffer - tokens expiring within (interval + buffer) will be refreshed
OAUTH_REFRESH_INTERVAL_MINUTES = 15
REFRESH_BUFFER_MINUTES = 5


async def refresh_expiring_tokens() -> dict[str, Any]:
    """
    Refresh OAuth tokens that are about to expire.

    Finds all tokens expiring before the next scheduled run (plus buffer)
    and attempts to refresh them using their associated provider's configuration.

    Returns:
        Summary of refresh results
    """
    threshold = OAUTH_REFRESH_INTERVAL_MINUTES + REFRESH_BUFFER_MINUTES
    return await run_refresh_job(trigger_type="automatic", refresh_threshold_minutes=threshold)


async def run_refresh_job(
    trigger_type: str = "automatic",
    trigger_user: str | None = None,
    refresh_threshold_minutes: int | None = None,
) -> dict[str, Any]:
    """
    Run the OAuth token refresh job.

    This method contains the shared logic for refreshing expiring OAuth tokens.
    Can be called by both the scheduler and HTTP endpoint.

    Args:
        trigger_type: Type of trigger ("automatic" or "manual")
        trigger_user: Email of user who triggered (for manual triggers)
        refresh_threshold_minutes: Threshold in minutes (default: 30 for automatic, None for manual refreshes all)

    Returns:
        Dictionary with job results
    """
    start_time = datetime.now(timezone.utc)
    logger.info(f"▶ OAuth token refresh starting (trigger={trigger_type})")

    results: dict[str, Any] = {
        "total_connections": 0,
        "needs_refresh": 0,
        "refreshed_successfully": 0,
        "refresh_failed": 0,
        "errors": [],
        "trigger_type": trigger_type,
        "trigger_user": trigger_user,
    }

    try:
        # Phase 1: Load tokens needing refresh (short-lived session).
        #
        # The WHERE clause sweeps three populations of tokens that need
        # to stay refreshed:
        #
        # 1. Anything with a refresh_token — classic authorization_code
        #    flow tokens for both Integrations and per-user MCP creds
        #    (``user_mcp_credentials.oauth_token_id`` always points at a
        #    refresh-bearing token).
        # 2. ``client_credentials`` flow tokens — no refresh_token, but
        #    the scheduler can mint a new access_token by re-presenting
        #    the client credentials. Halopsa-mcp's service token is the
        #    forcing example.
        # 3. Tokens explicitly referenced by ``mcp_connections.service_oauth_token_id``
        #    or ``user_mcp_credentials.oauth_token_id``. (1)+(2) already
        #    cover these in practice, but the explicit subqueries are
        #    self-documenting and defend against future flow types
        #    being added without the scheduler being updated.
        from src.models.orm.external_mcp import MCPConnection, UserMCPCredential

        async with get_db_context() as db:
            mcp_service_token_ids = select(MCPConnection.service_oauth_token_id).where(
                MCPConnection.service_oauth_token_id.isnot(None)
            )
            user_mcp_token_ids = select(UserMCPCredential.oauth_token_id)

            query = (
                select(OAuthToken)
                .join(OAuthToken.provider)
                .options(selectinload(OAuthToken.provider))
                .where(
                    or_(
                        OAuthToken.encrypted_refresh_token.isnot(None),
                        OAuthProvider.oauth_flow_type == "client_credentials",
                        OAuthToken.id.in_(mcp_service_token_ids),
                        OAuthToken.id.in_(user_mcp_token_ids),
                    )
                )
            )
            result = await db.execute(query)
            all_tokens = list(result.scalars().all())

            results["total_connections"] = len(all_tokens)

            now = datetime.now(timezone.utc)

            if refresh_threshold_minutes is not None:
                refresh_threshold = now + timedelta(minutes=refresh_threshold_minutes)
                tokens_to_refresh = [
                    t for t in all_tokens
                    if t.expires_at and t.expires_at <= refresh_threshold
                ]
            else:
                tokens_to_refresh = list(all_tokens)

            results["needs_refresh"] = len(tokens_to_refresh)

            # Build refresh context while the session is active. Phase 2 runs
            # over HTTP without holding a DB connection, so everything the
            # shared primitive needs (including the resolved entity_id) must
            # be baked into these dicts before we leave this block.
            token_refresh_data = []
            for token in tokens_to_refresh:
                provider = token.provider
                if not provider:
                    results["errors"].append({
                        "token_id": str(token.id),
                        "error": "Provider not found",
                    })
                    results["refresh_failed"] += 1
                    continue

                td = await build_token_refresh_context(
                    db=db,
                    provider=provider,
                    token=token,
                    org_id=None,  # scheduler refreshes globally; no org context
                )
                token_refresh_data.append(td)

        # Phase 2: Refresh tokens via HTTP (no DB connection held)
        refresh_outcomes: list[dict] = []
        for td in token_refresh_data:
            try:
                outcome = await refresh_oauth_token_http(td)
                refresh_outcomes.append(outcome)

                if outcome["success"]:
                    results["refreshed_successfully"] += 1
                else:
                    results["refresh_failed"] += 1
                    results["errors"].append({
                        "token_id": str(td["token_id"]),
                        "provider": td["provider_name"],
                        "error": outcome.get("error", "Refresh failed"),
                    })

            except Exception as e:
                results["refresh_failed"] += 1
                results["errors"].append({
                    "token_id": str(td["token_id"]),
                    "error": str(e),
                })
                logger.error(f"Error refreshing token {td['token_id']}: {e}", exc_info=True)

        # Phase 3: Persist refresh results (short-lived session)
        if refresh_outcomes:
            async with get_db_context() as db:
                for outcome in refresh_outcomes:
                    token = await db.get(OAuthToken, outcome["token_id"])
                    provider = await db.get(OAuthProvider, outcome["provider_id"])
                    if not token or not provider:
                        continue

                    if outcome["success"]:
                        token.encrypted_access_token = outcome["encrypted_access_token"]
                        token.expires_at = outcome["expires_at"]
                        if outcome.get("encrypted_refresh_token"):
                            token.encrypted_refresh_token = outcome["encrypted_refresh_token"]
                        if outcome.get("scopes"):
                            token.scopes = outcome["scopes"]
                        provider.status = "completed"
                        provider.last_token_refresh = datetime.now(timezone.utc)
                        provider.status_message = None
                    else:
                        provider.status = "failed"
                        provider.status_message = outcome.get("error", "Refresh failed")[:200]

                await db.commit()

        # Calculate duration
        end_time = datetime.now(timezone.utc)
        duration_seconds = (end_time - start_time).total_seconds()
        results["duration_seconds"] = duration_seconds
        results["start_time"] = start_time.isoformat()
        results["end_time"] = end_time.isoformat()

        # Log completion with visual marker
        success = results["refreshed_successfully"]
        failed = results["refresh_failed"]
        if failed > 0:
            logger.warning(
                f"⚠ OAuth token refresh completed with errors: "
                f"{success} refreshed, {failed} failed ({duration_seconds:.1f}s)"
            )
        else:
            logger.info(
                f"✓ OAuth token refresh completed: "
                f"{success} refreshed, {failed} failed ({duration_seconds:.1f}s)"
            )

    except Exception as e:
        logger.error(f"✗ OAuth token refresh failed: {e}", exc_info=True)
        results["errors"].append({"error": str(e)})

    return results
