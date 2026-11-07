"""Refresh coordination for immutable SDK2 bundles, never data authentication.

An old SDK retries transient closures but cannot refresh a 4001 itself. Its
same-document host can. Route an internal request through the existing Redis
user channel, then let the host's normal 4001 recovery rotate its own cookie.
Every protected operation still requires a currently valid access credential.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from shared.browser_impersonation import IMPERSONATION_COOKIE_NAME
from src.core.auth import select_websocket_auth_token
from src.core.database import get_db_context
from src.core.pubsub import manager
from src.core.security import decode_expired_access_token, decode_token
from src.models.orm.human_sessions import HumanAuthSession
from src.models.orm.users import User
from src.services.authorization.tokens import (
    HumanAccessTokenClaims,
    authenticate_human_token,
    parse_human_claims,
)

_AUTH_REFRESH_REQUEST = "bifrost_internal_auth_refresh"


@dataclass(frozen=True)
class RefreshSession:
    user_id: UUID
    session_id: UUID


def _signed_human_claims(websocket: WebSocket) -> tuple[HumanAccessTokenClaims, bool] | None:
    token = select_websocket_auth_token(websocket)
    if not token:
        return None
    payload = decode_token(token, expected_type="access")
    expired = payload is None
    if expired:
        payload = decode_expired_access_token(token)
    claims = parse_human_claims(payload) if payload is not None else None
    if claims is None or claims.sid is None:
        return None
    return claims, expired


async def refreshable_websocket_session(websocket: WebSocket) -> RefreshSession | None:
    """Only expiry/epoch refusal of a live durable human family can retry.

    Parsing an expired credential here never authenticates it. Missing authority
    state, revoked families and ineligible users cannot request recovery.
    """
    signed = _signed_human_claims(websocket)
    if signed is None:
        return None
    claims, expired = signed
    async with get_db_context() as db:
        result = await authenticate_human_token(db, claims)
        if not result.principal and not result.refresh_required:
            return None
        if not expired and not result.refresh_required:
            return None
        family = await db.scalar(
            select(HumanAuthSession.id)
            .join(User, User.id == HumanAuthSession.user_id)
            .where(
                HumanAuthSession.id == claims.sid,
                HumanAuthSession.user_id == claims.sub,
                HumanAuthSession.revoked_at.is_(None),
                HumanAuthSession.expires_at > datetime.now(timezone.utc),
                User.is_active.is_(True),
                User.is_system.is_(False),
                User.identity_kind.is_(None),
            )
        )
    return RefreshSession(claims.sub, family) if family is not None else None


def _same_origin_query_socket(websocket: WebSocket) -> bool:
    if (
        not websocket.query_params.get("token")
        or websocket.headers.get("authorization")
        or websocket.cookies.get(IMPERSONATION_COOKIE_NAME)
    ):
        return False
    # Browser Fetch Metadata describes the public origin even through a TLS
    # terminator or a proxy that rewrites Host/port. It selects retry behavior
    # only; the signed human credential and durable family are still required.
    return websocket.headers.get("sec-fetch-site") == "same-origin"


async def request_legacy_websocket_refresh(websocket: WebSocket) -> bool:
    """Ask the matching host to refresh; True selects a transient retry only."""
    if not _same_origin_query_socket(websocket):
        return False
    session = await refreshable_websocket_session(websocket)
    if session is None:
        return False
    await manager.broadcast(f"user:{session.user_id}", {
        "type": _AUTH_REFRESH_REQUEST,
        "session_id": str(session.session_id),
    })
    return True


async def consume_auth_refresh_request(
    websocket: WebSocket, channel: str, payload: dict[str, Any],
) -> bool:
    """Internal control frames never reach browsers or authorize an operation."""
    if payload.get("type") != _AUTH_REFRESH_REQUEST:
        return False
    if (
        websocket.query_params.get("token")
        or websocket.headers.get("authorization")
        or websocket.cookies.get(IMPERSONATION_COOKIE_NAME)
    ):
        return True
    signed = _signed_human_claims(websocket)
    if signed is None:
        return True
    claims, _ = signed
    if channel != f"user:{claims.sub}" or payload.get("session_id") != str(claims.sid):
        return True
    # A newer, valid host credential ignores old requests. An old/stolen query
    # token cannot repeatedly rotate an already refreshed host session.
    if await refreshable_websocket_session(websocket) is not None:
        await websocket.close(code=4001, reason="Authentication refresh required")
        raise WebSocketDisconnect(code=4001)
    return True
