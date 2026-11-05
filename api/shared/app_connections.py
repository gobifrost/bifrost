"""Personal app approval shared by CLI device login and inbound MCP OAuth."""

import hmac
import json
import secrets
from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert

from shared.device_auth import decide_device_login
from shared.mcp_oauth import (
    TTL_MCP_AUTH_CODE, _mcp_auth_code_key,
    _mcp_state_key, _open_auth_code_record, _seal_auth_code_record, _secret_hmac,
    callback_response, redirect_matches, validate_redirect_uri,
)
from shared.models import AppConnectionDetails, AppConnectionResult, MCPClientRegistration
from src.core.cache import get_shared_redis
from src.core.database import get_db_context
from src.core.principal import UserPrincipal
from src.models.orm.oauth_clients import OAuthClientRegistration
from src.services.mcp_server.config_service import get_mcp_config_cached
from src.core.security import decode_token
from shared.human_sessions import has_live_refresh_token_jti


class OAuthConnectionError(ValueError):
    def __init__(self, error: str, description: str):
        self.payload = {"error": error, "error_description": description}
        super().__init__(description)


def require_app_approver(user: UserPrincipal) -> None:
    """Approval delegates only an active human's own account authority."""
    if user.impersonated_by_user_id is not None or user.impersonation_session_id is not None:
        raise HTTPException(403, "Exit impersonation before connecting an app")
    if not user.is_active or user.embed or user.runtime_authority is not None or user.service_id:
        raise HTTPException(403, "Sign in with your own account before connecting an app")


async def require_browser_app_approver(request: Request, user: UserPrincipal) -> None:
    """An access token alone cannot create another durable human session."""
    require_app_approver(user)
    refresh = request.cookies.get("refresh_token")
    claims = decode_token(refresh, expected_type="refresh") if refresh else None
    if (
        claims is None or user.auth_session_id is None
        or claims.get("sub") != str(user.user_id)
        or claims.get("sid") != str(user.auth_session_id)
        or not isinstance(claims.get("jti"), str)
    ):
        raise HTTPException(403, "Open this approval in your signed-in browser")
    async with get_db_context() as db:
        live = await has_live_refresh_token_jti(db, user.user_id, claims["jti"])
    if not live:
        raise HTTPException(403, "Sign in again before approving this connection")


async def register_mcp_client(body: Any) -> dict[str, Any]:
    try:
        metadata = MCPClientRegistration.model_validate(body)
        for uri in metadata.redirect_uris:
            validate_redirect_uri(uri)
    except (ValidationError, ValueError) as error:
        raise OAuthConnectionError("invalid_client_metadata", "Provide valid registered callback addresses") from error
    client_id = secrets.token_urlsafe(16)
    record = {
        "client_name": metadata.client_name,
        "redirect_uris": metadata.redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with get_db_context() as db:
        db.add(OAuthClientRegistration(client_id=client_id, client_metadata=record))
        await db.commit()
    return {"client_id": client_id, **record, "client_name": metadata.client_name or "MCP Client"}


async def begin_mcp_connection(
    client_id: str, redirect_uri: str, state: str, code_challenge: str, scope: str,
) -> str:
    redis = await get_shared_redis()
    async with get_db_context() as db:
        registration = await db.get(OAuthClientRegistration, client_id)
        if registration is None:
            # One-way import preserves pre-upgrade IDs, including registrations
            # created by old replicas during a rolling deployment. Authorization
            # always uses the durable row; Redis is never an authority fallback.
            legacy_key = f"bifrost:mcp:client:{client_id}"
            legacy = await redis.get(legacy_key)
            if legacy is not None:
                try:
                    metadata = MCPClientRegistration.model_validate_json(legacy)
                except ValidationError as error:
                    raise OAuthConnectionError("invalid_client", "Client metadata is invalid; register again") from error
                await db.execute(insert(OAuthClientRegistration).values(
                    client_id=client_id, client_metadata=metadata.model_dump(),
                ).on_conflict_do_nothing(index_elements=["client_id"]))
                await db.commit()
                registration = await db.get(OAuthClientRegistration, client_id)
                await redis.delete(legacy_key)
    if registration is None:
        raise OAuthConnectionError("invalid_client", "Client registration expired or is unknown; register again")
    client = registration.client_metadata
    try:
        metadata = MCPClientRegistration.model_validate(client)
    except ValidationError as error:
        raise OAuthConnectionError("invalid_client", "Client metadata is invalid; register again") from error
    if not any(redirect_matches(redirect_uri, uri) for uri in metadata.redirect_uris):
        raise OAuthConnectionError("invalid_request", "Callback address does not match this client's registration")
    if set(scope.split()) - {"mcp:access"}:
        raise OAuthConnectionError("invalid_scope", "Only mcp:access is supported")
    internal_state = secrets.token_urlsafe(32)
    record = {
        "client_id": client_id, "client_name": metadata.client_name,
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": code_challenge, "scope": "mcp:access",
    }
    await redis.setex(_mcp_state_key(internal_state), TTL_MCP_AUTH_CODE, _seal_auth_code_record(internal_state, record))
    return internal_state


async def _pending_mcp(state: str, user: UserPrincipal) -> tuple[str, dict[str, Any]]:
    require_app_approver(user)
    async with get_db_context() as db:
        config = await get_mcp_config_cached(db)
    if config is not None and not config.enabled:
        raise HTTPException(403, "External MCP access is disabled")
    redis = await get_shared_redis()
    raw = await redis.get(_mcp_state_key(state))
    if isinstance(raw, bytes):
        raw = raw.decode()
    record = _open_auth_code_record(state, raw) if raw else None
    if record is None:
        raise HTTPException(404, "This connection request has expired. Start again from your app.")
    return raw, record


def _consent_token(state: str, raw: str, user: UserPrincipal) -> str:
    binding = json.dumps([state, raw, str(user.user_id), str(user.auth_session_id)])
    return _secret_hmac(f"app-consent:{binding}")


async def preview_mcp_connection(state: str, user: UserPrincipal) -> AppConnectionDetails:
    raw, record = await _pending_mcp(state, user)
    name = record.get("client_name")
    return AppConnectionDetails(
        client_name=name.strip() if isinstance(name, str) and name.strip() else "Unnamed App",
        redirect_uri=record["redirect_uri"], consent_token=_consent_token(state, raw, user),
    )


async def decide_app_connection(
    kind: Literal["cli", "mcp"], request_id: str, user: UserPrincipal,
    approved: bool, consent_token: str = "",
) -> AppConnectionResult:
    require_app_approver(user)
    if kind == "cli":
        result = await decide_device_login(request_id, str(user.user_id), approved)
        if result == "expired":
            raise HTTPException(404, "Invalid or expired user code")
        if result == "decided":
            raise HTTPException(409, "This CLI login has already been approved or denied. Start a new login from your CLI.")
        return AppConnectionResult()

    raw, record = await _pending_mcp(request_id, user)
    if not hmac.compare_digest(_consent_token(request_id, raw, user), consent_token):
        raise HTTPException(403, "Review this connection in your signed-in browser before deciding")
    redis = await get_shared_redis()
    # Only one authenticated decision can consume the unchanged request.
    consumed = await redis.getdel(_mcp_state_key(request_id))
    if isinstance(consumed, bytes):
        consumed = consumed.decode()
    if consumed != raw:
        raise HTTPException(409, "This connection has already been approved or denied. Start again from your app.")
    if not approved:
        return AppConnectionResult(redirect_url=callback_response(
            record["redirect_uri"], error="access_denied", state=record["state"],
        ))
    code = secrets.token_urlsafe(32)
    code_record = {key: record[key] for key in ("client_id", "redirect_uri", "code_challenge", "scope")}
    code_record["user_id"] = str(user.user_id)
    await redis.setex(_mcp_auth_code_key(code), TTL_MCP_AUTH_CODE, _seal_auth_code_record(code, code_record))
    return AppConnectionResult(redirect_url=callback_response(record["redirect_uri"], code=code, state=record["state"]))
