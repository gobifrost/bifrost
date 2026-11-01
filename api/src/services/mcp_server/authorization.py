"""Resolve MCP authority from the current user or its durable runtime record."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.authorization.enforce import Caller, load_caller
from src.services.authorization.callers import CallerUnavailable, load_human_caller
from src.services.authorization.runtime_auth import load_runtime_principal

if TYPE_CHECKING:
    from src.services.mcp_server.server import MCPContext


class MCPAuthorizationError(ValueError):
    """The authenticated MCP subject no longer has current authority."""


async def load_mcp_caller(db: AsyncSession, context: MCPContext) -> Caller:
    """Never turn copied context flags or role names into authority.

    Runtime contexts resolve their signed durable credential again. Human
    contexts reload the current account and role assignments. Acting identity
    remains separate from the original user's permission context.
    """
    if context.runtime_credential_kind is not None:
        payload = {
            "credential_kind": context.runtime_credential_kind,
            "sub": str(context.runtime_credential_id),
            "service_id": context.runtime_service_id,
        }
        principal = await load_runtime_principal(db, payload)
        if principal is None:
            raise MCPAuthorizationError("The runtime credential is no longer valid")
    else:
        from mcp.server.auth.middleware.auth_context import get_access_token
        from src.core.security import decode_token
        from src.services.authorization.tokens import authenticate_human_token, parse_human_claims

        access = get_access_token()
        if access is not None:
            payload = decode_token(access.token, expected_type="access")
            claims = parse_human_claims(payload) if payload is not None else None
            if claims is None or str(claims.sub) != str(context.user_id):
                raise MCPAuthorizationError("The MCP human credential is no longer valid")
            result = await authenticate_human_token(db, claims)
            if result.principal is None:
                raise MCPAuthorizationError("The MCP human credential requires reauthentication")
            return await load_caller(db, result.principal)
        try:
            user_id = UUID(str(context.user_id))
        except (TypeError, ValueError) as exc:
            raise MCPAuthorizationError("The MCP account is no longer available") from exc
        try:
            return await load_human_caller(db, user_id)
        except CallerUnavailable as exc:
            raise MCPAuthorizationError(str(exc)) from exc
    caller = await load_caller(db, principal)
    if caller.ctx is None:
        raise MCPAuthorizationError("The MCP account is no longer available")
    return caller
