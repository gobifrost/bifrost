"""
Bifrost MCP OAuth 2.1 Authentication Provider

Custom FastMCP auth provider that implements OAuth 2.1 using Bifrost's
existing authentication system. Provides:

1. OAuth discovery endpoints (RFC 8414, RFC 9728)
2. Authorization code flow with PKCE (RFC 7636)
3. Dynamic client registration (RFC 7591)
4. Token validation using Bifrost's existing JWT system

Users authenticate through Bifrost's normal login flow and use their
access token for MCP requests.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from pydantic import Field
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.authentication import AuthenticationError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from shared.human_sessions import HumanSessionUnavailable, issue_human_session, rotate_human_session
from shared.identities import is_identity

from shared.mcp_oauth import _mcp_auth_code_key, _open_auth_code_record

logger = logging.getLogger(__name__)


class MCPAccessToken(AccessToken):
    """SDK token with Bifrost identity claims for tool execution."""

    claims: dict[str, Any] = Field(default=...)


class _RefreshRequired(AuthenticationError):
    """An authentication refusal that can be repaired before any tool effect."""


def _authentication_error(_connection, error: AuthenticationError) -> Response:
    from src.core.auth import REFRESH_REQUIRED_CHALLENGE

    return JSONResponse(
        {"error": "invalid_token", "error_description": "refresh_required"},
        status_code=401,
        headers={"WWW-Authenticate": REFRESH_REQUIRED_CHALLENGE if isinstance(error, _RefreshRequired) else "Bearer"},
    )


class BifrostAuthProvider:
    """
    OAuth 2.1 auth provider for MCP using Bifrost's auth system.

    Implements:
    - /.well-known/oauth-authorization-server (RFC 8414 discovery)
    - /.well-known/oauth-protected-resource (RFC 9728 resource metadata)
    - /authorize (redirects to Bifrost login)
    - /token (exchanges code for Bifrost JWT)
    - /register (dynamic client registration)
    - Token validation using Bifrost's existing JWT system
    """

    def __init__(self, base_url: str | None = None):
        """
        Args:
            base_url: Public URL of the MCP server (e.g., ngrok URL).
                      Falls back to settings.mcp_base_url config.
        """
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            from src.config import get_settings
            self.base_url = get_settings().public_url.rstrip("/")
        self.issuer = self.base_url
        self.required_scopes: list[str] = ["mcp:access"]

    def _get_resource_url(self, path: str | None = None) -> str | None:
        """Get the actual resource URL being protected.

        Args:
            path: The path where the resource endpoint is mounted (e.g., "/mcp")

        Returns:
            The full URL of the protected resource
        """
        if self.base_url is None:
            return None

        if path:
            prefix = str(self.base_url).rstrip("/")
            suffix = path.lstrip("/")
            return f"{prefix}/{suffix}"
        return self.base_url

    def get_routes(
        self, mcp_path: str | None = None, mcp_endpoint: Any | None = None
    ) -> list[Route]:
        """
        Return OAuth routes to be mounted by FastMCP.

        Args:
            mcp_path: The path where the MCP endpoint is mounted (e.g., "/mcp").
                      Used for path-aware discovery routes.
            mcp_endpoint: The MCP endpoint handler to protect with auth.
                          Not used in our implementation since we use middleware.
        """
        # Determine the path suffix for discovery endpoints
        path_suffix = mcp_path.rstrip("/") if mcp_path else "/mcp"

        routes = [
            # Path-aware discovery (RFC 8414) - with path suffix
            Route(
                f"/.well-known/oauth-authorization-server{path_suffix}",
                self._authorization_server_metadata,
                methods=["GET"],
            ),
            Route(
                f"/.well-known/oauth-protected-resource{path_suffix}",
                self._protected_resource_metadata,
                methods=["GET"],
            ),
            # Root-level discovery (backwards compatibility)
            Route(
                "/.well-known/oauth-authorization-server",
                self._authorization_server_metadata,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-protected-resource",
                self._protected_resource_metadata,
                methods=["GET"],
            ),
            # Agent-scoped discovery (for /mcp/{agent_id} endpoints)
            Route(
                "/.well-known/oauth-authorization-server/mcp/{agent_id}",
                self._authorization_server_metadata,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp/{agent_id}",
                self._protected_resource_metadata,
                methods=["GET"],
            ),
            Route("/authorize", self._authorize, methods=["GET"]),
            Route("/token", self._token, methods=["POST"]),
            Route("/register", self._register, methods=["POST"]),
            Route("/mcp/callback", self._callback, methods=["GET"]),
        ]

        return routes

    async def _authorization_server_metadata(self, request: Request) -> JSONResponse:
        """RFC 8414: OAuth Authorization Server Metadata."""
        return JSONResponse({
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.base_url}/authorize",
            "token_endpoint": f"{self.base_url}/token",
            "registration_endpoint": f"{self.base_url}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp:access"],
        })

    async def _protected_resource_metadata(self, request: Request) -> JSONResponse:
        """RFC 9728: OAuth Protected Resource Metadata."""
        return JSONResponse({
            "resource": f"{self.base_url}/mcp",
            "authorization_servers": [self.issuer],
            "scopes_supported": ["mcp:access"],
            "bearer_methods_supported": ["header"],
        })

    async def _authorize(self, request: Request) -> Response:
        """
        OAuth authorize endpoint - redirects to Bifrost login.

        Expected query params:
        - response_type: "code" (required)
        - client_id: Registered client ID (required)
        - redirect_uri: Client callback URL (required)
        - state: Client state for CSRF protection (required)
        - code_challenge: PKCE challenge (required)
        - code_challenge_method: "S256" (required)
        - scope: Space-separated scopes (optional)
        """
        # Extract OAuth parameters
        response_type = request.query_params.get("response_type")
        client_id = request.query_params.get("client_id")
        redirect_uri = request.query_params.get("redirect_uri")
        state = request.query_params.get("state")
        code_challenge = request.query_params.get("code_challenge")
        code_challenge_method = request.query_params.get("code_challenge_method")
        scope = request.query_params.get("scope", "mcp:access")

        # Validate required parameters
        if response_type != "code":
            return JSONResponse(
                {"error": "unsupported_response_type", "error_description": "Only 'code' response type is supported"},
                status_code=400
            )

        if not client_id or not redirect_uri or not state or not code_challenge:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Missing required parameters"},
                status_code=400
            )

        if code_challenge_method != "S256":
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Only S256 code_challenge_method is supported"},
                status_code=400
            )

        from shared.app_connections import begin_mcp_connection, OAuthConnectionError

        try:
            internal_state = await begin_mcp_connection(
                client_id, redirect_uri, state, code_challenge, scope,
            )
        except OAuthConnectionError as error:
            return JSONResponse(error.payload, status_code=400)
        from urllib.parse import urlencode
        callback_url = f"{self.base_url}/connect-app?{urlencode({'request': internal_state})}"
        login_url = f"{self.base_url}/login?{urlencode({'return_to': callback_url})}"
        return RedirectResponse(url=login_url, status_code=302)

    async def _callback(self, request: Request) -> Response:
        """Opening a callback never grants access; the browser must show consent."""
        from urllib.parse import urlencode
        from shared.browser_impersonation import IMPERSONATION_COOKIE_NAME

        if request.cookies.get(IMPERSONATION_COOKIE_NAME):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        internal_state = request.query_params.get("internal_state")
        if not internal_state:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return RedirectResponse(
            url=f"{self.base_url}/connect-app?{urlencode({'request': internal_state})}",
            status_code=302,
        )

    async def _token(self, request: Request) -> JSONResponse:
        """
        OAuth token endpoint - exchanges code for Bifrost JWT.

        Supports:
        - grant_type=authorization_code: Exchange auth code for tokens
        - grant_type=refresh_token: Refresh access token
        """
        from src.core.cache import get_shared_redis
        from src.core.database import get_db_context
        from src.repositories.users import UserRepository

        # Parse form data
        form = await request.form()
        grant_type = form.get("grant_type")

        if grant_type == "authorization_code":
            code = form.get("code")
            redirect_uri = form.get("redirect_uri")
            code_verifier = form.get("code_verifier")
            client_id = form.get("client_id")

            if not all([code, redirect_uri, code_verifier]):
                return JSONResponse(
                    {"error": "invalid_request", "error_description": "Missing required parameters"},
                    status_code=400
                )

            # Get auth code data from Redis
            r = await get_shared_redis()
            code_key = _mcp_auth_code_key(code)
            auth_code_data_json = await r.getdel(code_key)

            if not auth_code_data_json:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"},
                    status_code=400
                )

            auth_code_data = _open_auth_code_record(code, auth_code_data_json)
            if auth_code_data is None:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"},
                    status_code=400
                )

            if client_id != auth_code_data.get("client_id"):
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "client_id mismatch"},
                    status_code=400,
                )

            # Validate redirect_uri matches
            if redirect_uri != auth_code_data["redirect_uri"]:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "redirect_uri mismatch"},
                    status_code=400
                )

            # Validate PKCE code_verifier
            code_challenge = auth_code_data["code_challenge"]
            expected_challenge = hashlib.sha256(code_verifier.encode()).digest()
            import base64
            expected_challenge_b64 = base64.urlsafe_b64encode(expected_challenge).rstrip(b"=").decode()

            if expected_challenge_b64 != code_challenge:
                logger.warning(f"MCP OAuth: PKCE verification failed for client {client_id}")
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid code_verifier"},
                    status_code=400
                )

            # Generate Bifrost JWT tokens
            user_id = auth_code_data["user_id"]

            async with get_db_context() as db:
                user_repo = UserRepository(db)
                user = await user_repo.get_by_id(user_id)

                if not user or not user.is_active or is_identity(user):
                    return JSONResponse(
                        {"error": "invalid_grant", "error_description": "User not found"},
                        status_code=400
                    )

            try:
                tokens = await issue_human_session(user.id)
            except HumanSessionUnavailable:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "User not found"},
                    status_code=400,
                )

            logger.info("MCP OAuth: Token issued for user %s", user_id)

            return JSONResponse({
                "access_token": tokens.access_token,
                "token_type": "Bearer",
                "expires_in": max(0, int((tokens.expires_at - datetime.now(timezone.utc)).total_seconds())),
                "refresh_token": tokens.refresh_token,
                "scope": auth_code_data.get("scope", "mcp:access"),
            })

        elif grant_type == "refresh_token":
            refresh_token = form.get("refresh_token")

            if not refresh_token:
                return JSONResponse(
                    {"error": "invalid_request", "error_description": "Missing refresh_token"},
                    status_code=400
                )

            try:
                tokens = await rotate_human_session(refresh_token)
            except HumanSessionUnavailable:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid refresh token"},
                    status_code=400,
                )

            return JSONResponse({
                "access_token": tokens.access_token,
                "token_type": "Bearer",
                "expires_in": max(0, int((tokens.expires_at - datetime.now(timezone.utc)).total_seconds())),
                "refresh_token": tokens.refresh_token,
                "scope": "mcp:access",
            })

        else:
            return JSONResponse(
                {"error": "unsupported_grant_type", "error_description": "Unsupported grant type"},
                status_code=400
            )

    async def _register(self, request: Request) -> JSONResponse:
        """
        Dynamic Client Registration (RFC 7591).

        MCP clients register to get a client_id. We accept any registration
        since authentication is handled by user tokens, not client credentials.
        """
        from src.core.rate_limit import auth_limiter, get_client_ip

        await auth_limiter.check("mcp_client_registration", get_client_ip(request))
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Invalid JSON body"},
                status_code=400
            )

        from shared.app_connections import register_mcp_client, OAuthConnectionError

        try:
            registration = await register_mcp_client(body)
        except OAuthConnectionError as error:
            return JSONResponse(error.payload, status_code=400)
        return JSONResponse(registration, status_code=201)

    async def _check_mcp_access(self, token_payload: dict) -> bool:
        """
        Check if user is authorized for MCP access based on config.

        Only gates on the global ``enabled`` switch. Authenticated users receive
        the stable gateway tools; agent discovery and every dispatch re-check
        agent and tool access downstream.

        Args:
            token_payload: Decoded JWT claims

        Returns:
            True if user can access MCP
        """
        from src.core.database import get_db_context
        from src.services.mcp_server.config_service import get_mcp_config_cached

        try:
            async with get_db_context() as db:
                config = await get_mcp_config_cached(db)
            logger.info(
                f"MCP access check: config loaded - enabled={config.enabled if config else 'N/A'}"
            )
        except Exception as e:
            logger.warning(f"MCP access check: Failed to get config: {e}")
            config = None

        if config is not None and not config.enabled:
            logger.info("MCP access check: External MCP access is disabled")
            return False

        return True

    async def verify_token(self, token: str) -> Any:
        """
        Verify a Bearer token (Bifrost JWT).

        Called by FastMCP to validate incoming MCP requests.

        Args:
            token: Bearer token from Authorization header

        Returns:
            AccessToken if valid, None otherwise
        """
        from src.core.database import get_db_context
        from src.core.security import decode_token
        from src.services.authorization.runtime_auth import (
            is_legacy_runtime_payload,
            is_runtime_credential_payload,
            load_runtime_principal,
            log_legacy_runtime_token_accepted,
        )
        from src.services.authorization.tokens import (
            authenticate_human_token,
            authenticate_runtime_token,
            parse_human_claims,
            parse_runtime_claims,
        )

        payload = decode_token(token, expected_type="access")
        if payload is None:
            from src.core.auth import _refresh_fixes

            if _refresh_fixes(token):
                raise _RefreshRequired()
            logger.info("MCP auth: decode_token returned None (invalid/expired JWT)")
            return None
        if payload.get("impersonated_by_user_id") or payload.get("impersonation_session_id"):
            # A browser support credential cannot establish an independent
            # MCP session with a different lifetime or revocation boundary.
            return None

        runtime_principal = None
        runtime_role_names: list[str] | None = None
        if is_legacy_runtime_payload(payload):
            # Legacy bridge (token PR1c removes it): validated as before.
            async with get_db_context() as db:
                runtime_principal = await load_runtime_principal(db, payload)
            if runtime_principal is None:
                return None
            log_legacy_runtime_token_accepted(payload)
        elif is_runtime_credential_payload(payload):
            claims = parse_runtime_claims(payload)
            if claims is None:
                return None
            async with get_db_context() as db:
                result = await authenticate_runtime_token(db, claims)
            if result.refresh_required:
                raise _RefreshRequired()
            if result.principal is None:
                return None
            runtime_principal = result.principal
            # Tool filtering follows the run user's roles, carried by the claims.
            runtime_role_names = [role.name for role in claims.usr.roles]
        else:
            claims = parse_human_claims(payload)
            if claims is None:
                return None
            async with get_db_context() as db:
                result = await authenticate_human_token(db, claims)
            if result.refresh_required:
                raise _RefreshRequired()
            if result.principal is None:
                return None
            runtime_principal = result.principal
            runtime_role_names = [role.name for role in claims.usr.roles]

        logger.info(f"MCP auth: Token decoded for user {payload.get('email')}, checking access...")

        # Check MCP access permissions
        if not await self._check_mcp_access(payload):
            logger.info(f"MCP auth: _check_mcp_access returned False for user {payload.get('email')}")
            return None

        principal = runtime_principal
        role_user_id = (
            str(principal.runtime_authority.run_user.user_id)
            if principal is not None and principal.runtime_authority is not None
            else payload.get("sub")
        )
        # Runtime tool filtering follows the original run user's current
        # roles, while effects keep the separately resolved acting identity.
        role_names = (
            runtime_role_names if runtime_role_names is not None else await self._get_user_roles(role_user_id)
        )

        logger.info(f"MCP auth: Token valid for user {payload.get('email')}, roles={role_names}")

        return MCPAccessToken(
            token=token,
            client_id=str(principal.user_id if principal is not None else payload.get("sub")),
            scopes=["mcp:access"],
            expires_at=payload.get("exp"),
            claims={
                "user_id": str(principal.user_id) if principal is not None else payload.get("sub"),
                "email": principal.email if principal is not None else payload.get("email"),
                "name": principal.name if principal is not None else payload.get("name"),
                "is_superuser": principal.is_superuser if principal is not None else payload.get("is_superuser", False),
                "is_provider_org": principal.is_provider_org if principal is not None else payload.get("is_provider_org", False),
                "is_external": principal.is_external if principal is not None else payload.get("is_external", False),
                "org_id": str(principal.organization_id) if principal is not None and principal.organization_id else payload.get("org_id"),
                "roles": role_names,  # Role names for MCP tool filtering
                "runtime_credential_kind": (
                    principal.runtime_authority.kind
                    if principal is not None and principal.runtime_authority is not None
                    else None
                ),
                "runtime_credential_id": (
                    str(principal.runtime_authority.credential_id)
                    if principal is not None and principal.runtime_authority is not None
                    else None
                ),
                "runtime_service_id": (
                    str(principal.runtime_authority.service_id)
                    if principal is not None and principal.runtime_authority is not None and principal.runtime_authority.service_id
                    else None
                ),
            },
        )

    async def _get_user_roles(self, user_id: str | None) -> list[str]:
        """
        Get role names for a user from the database.

        Args:
            user_id: User UUID as string

        Returns:
            List of role names the user has
        """
        if not user_id:
            return []

        try:
            from sqlalchemy import select

            from src.core.database import get_db_context
            from src.models.orm.users import Role, UserRole

            async with get_db_context() as db:
                # Single query joining UserRole to Role to get role names
                result = await db.execute(
                    select(Role.name)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == user_id)
                )
                return list(result.scalars().all())

        except Exception as e:
            logger.warning(f"Failed to fetch user roles: {e}")
            return []

    def get_middleware(self) -> list:
        """
        Return HTTP middleware for the auth provider.

        The MCP SDK requires AuthenticationMiddleware with BearerAuthBackend
        to validate tokens and set scope["user"]. Without this, all
        requests fail with 401 because the RequireAuthMiddleware can't
        find an authenticated user.
        """
        return [
            StarletteMiddleware(AuthenticationMiddleware, backend=BearerAuthBackend(self), on_error=_authentication_error),
            StarletteMiddleware(AuthContextMiddleware),
        ]


def create_bifrost_auth_provider(base_url: str | None = None) -> BifrostAuthProvider:
    """
    Create a Bifrost auth provider for MCP.

    Args:
        base_url: Public URL of the MCP server. Falls back to MCP_BASE_URL env var.

    Returns:
        BifrostAuthProvider instance
    """
    return BifrostAuthProvider(base_url)
