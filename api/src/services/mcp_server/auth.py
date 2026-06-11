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
import json
import logging
import secrets
from typing import Any

from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from shared.external_access import resolve_external_claim

logger = logging.getLogger(__name__)

# TTLs for MCP OAuth flow
TTL_MCP_AUTH_CODE = 300  # 5 minutes for authorization code
TTL_MCP_CLIENT = 86400 * 30  # 30 days for registered clients


def _mcp_auth_code_key(code: str) -> str:
    """Key for MCP authorization code storage."""
    return f"bifrost:mcp:auth_code:{code}"


def _mcp_client_key(client_id: str) -> str:
    """Key for registered MCP client."""
    return f"bifrost:mcp:client:{client_id}"


def _mcp_state_key(state: str) -> str:
    """Key for OAuth state during authorization flow."""
    return f"bifrost:mcp:state:{state}"


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
        from src.core.cache import get_shared_redis

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

        if not all([client_id, redirect_uri, state, code_challenge]):
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Missing required parameters"},
                status_code=400
            )

        if code_challenge_method != "S256":
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Only S256 code_challenge_method is supported"},
                status_code=400
            )

        # Store OAuth state in Redis for callback
        r = await get_shared_redis()
        oauth_data = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "scope": scope,
        }

        # Generate internal state token to link login to OAuth flow
        internal_state = secrets.token_urlsafe(32)
        await r.setex(
            _mcp_state_key(internal_state),
            TTL_MCP_AUTH_CODE,
            json.dumps(oauth_data)
        )

        # Redirect to Bifrost login page with return URL to callback
        callback_url = f"{self.base_url}/mcp/callback?internal_state={internal_state}"
        login_url = f"{self.base_url}/login?return_to={callback_url}"

        logger.info(f"MCP OAuth: Redirecting to login for client_id={client_id}")
        return RedirectResponse(url=login_url, status_code=302)

    async def _callback(self, request: Request) -> Response:
        """
        OAuth callback after Bifrost login.

        This is called after the user logs in. We:
        1. Verify the user is authenticated (via cookie)
        2. Retrieve OAuth params from Redis
        3. Generate authorization code
        4. Redirect to client's redirect_uri with code
        """
        from src.core.cache import get_shared_redis
        from src.core.security import decode_token

        internal_state = request.query_params.get("internal_state")
        if not internal_state:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Missing internal_state"},
                status_code=400
            )

        # Get OAuth data from Redis
        r = await get_shared_redis()
        oauth_data_json = await r.get(_mcp_state_key(internal_state))

        if not oauth_data_json:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "OAuth session expired"},
                status_code=400
            )

        oauth_data = json.loads(oauth_data_json)

        # Get access token from cookie (set by Bifrost login)
        access_token = request.cookies.get("access_token")
        if not access_token:
            # User not logged in - redirect back to login
            # DON'T delete state yet - we need it when user comes back after login
            callback_url = f"{self.base_url}/mcp/callback?internal_state={internal_state}"
            login_url = f"{self.base_url}/login?return_to={callback_url}"
            return RedirectResponse(url=login_url, status_code=302)

        # Clean up state only after we've confirmed user is authenticated
        await r.delete(_mcp_state_key(internal_state))

        # Validate the access token
        payload = decode_token(access_token, expected_type="access")
        if payload is None:
            return JSONResponse(
                {"error": "access_denied", "error_description": "Invalid or expired session"},
                status_code=401
            )

        # Check MCP access permissions (only gates on the master enabled switch)
        if not await self._check_mcp_access(payload):
            return JSONResponse(
                {"error": "access_denied", "error_description": "External MCP access is disabled"},
                status_code=403
            )

        # Generate authorization code
        auth_code = secrets.token_urlsafe(32)

        # Store auth code with user info and PKCE challenge
        auth_code_data = {
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name"),
            "is_superuser": payload.get("is_superuser", False),
            "org_id": payload.get("org_id"),
            "code_challenge": oauth_data["code_challenge"],
            "redirect_uri": oauth_data["redirect_uri"],
            "client_id": oauth_data["client_id"],
            "scope": oauth_data["scope"],
        }

        await r.setex(
            _mcp_auth_code_key(auth_code),
            TTL_MCP_AUTH_CODE,
            json.dumps(auth_code_data)
        )

        # Redirect to client with authorization code
        redirect_uri = oauth_data["redirect_uri"]
        state = oauth_data["state"]
        redirect_url = f"{redirect_uri}?code={auth_code}&state={state}"

        logger.info(f"MCP OAuth: Issuing auth code for user {payload.get('email')}")

        # If client requests JSON (from fetch in React component), return URL instead of redirect
        # This is needed because Vite's proxy only works for XHR, not browser navigations
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"redirect_url": redirect_url})

        return RedirectResponse(url=redirect_url, status_code=302)

    async def _token(self, request: Request) -> JSONResponse:
        """
        OAuth token endpoint - exchanges code for Bifrost JWT.

        Supports:
        - grant_type=authorization_code: Exchange auth code for tokens
        - grant_type=refresh_token: Refresh access token
        """
        from src.core.cache import get_shared_redis
        from src.core.database import get_db_context
        from src.core.security import create_access_token, create_refresh_token
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
            auth_code_data_json = await r.get(_mcp_auth_code_key(code))

            if not auth_code_data_json:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"},
                    status_code=400
                )

            auth_code_data = json.loads(auth_code_data_json)

            # Delete auth code (one-time use)
            await r.delete(_mcp_auth_code_key(code))

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

                if not user:
                    return JSONResponse(
                        {"error": "invalid_grant", "error_description": "User not found"},
                        status_code=400
                    )

                # Create tokens with full user context. The is_external claim
                # (EXT-1) is neutralized for bypass principals at this mint —
                # without it every MCP-OAuth token defaulted to is_external=False
                # (LEAK #1), re-opening the global tier to external users.
                token_data = {
                    "sub": str(user.id),
                    "email": user.email,
                    "name": user.name,
                    "is_superuser": user.is_superuser,
                    "is_external": await resolve_external_claim(db, user),
                    "org_id": str(user.organization_id) if user.organization_id else None,
                    "type": "access",
                }
                access_token = create_access_token(data=token_data)
                refresh_token, _jti = create_refresh_token(data={"sub": str(user.id)})

            logger.info(f"MCP OAuth: Token issued for user {auth_code_data['email']}")

            return JSONResponse({
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 1800,  # 30 minutes
                "refresh_token": refresh_token,
                "scope": auth_code_data.get("scope", "mcp:access"),
            })

        elif grant_type == "refresh_token":
            refresh_token = form.get("refresh_token")

            if not refresh_token:
                return JSONResponse(
                    {"error": "invalid_request", "error_description": "Missing refresh_token"},
                    status_code=400
                )

            # Validate and rotate refresh token
            from src.core.security import decode_token

            payload = decode_token(refresh_token, expected_type="refresh")
            if payload is None:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "Invalid refresh token"},
                    status_code=400
                )

            user_id = payload.get("sub")

            async with get_db_context() as db:
                user_repo = UserRepository(db)
                user = await user_repo.get_by_id(user_id)

                if not user:
                    return JSONResponse(
                        {"error": "invalid_grant", "error_description": "User not found"},
                        status_code=400
                    )

                # Create new tokens (carry the is_external claim — LEAK #1).
                token_data = {
                    "sub": str(user.id),
                    "email": user.email,
                    "name": user.name,
                    "is_superuser": user.is_superuser,
                    "is_external": await resolve_external_claim(db, user),
                    "org_id": str(user.organization_id) if user.organization_id else None,
                    "type": "access",
                }
                access_token = create_access_token(data=token_data)
                new_refresh_token, _jti = create_refresh_token(data={"sub": str(user.id)})

            return JSONResponse({
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 1800,
                "refresh_token": new_refresh_token,
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
        from src.core.cache import get_shared_redis

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Invalid JSON body"},
                status_code=400
            )

        # Generate client_id
        client_id = secrets.token_urlsafe(16)

        # Store client registration
        r = await get_shared_redis()
        client_data = {
            "client_name": body.get("client_name", "MCP Client"),
            "redirect_uris": body.get("redirect_uris", []),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

        await r.setex(
            _mcp_client_key(client_id),
            TTL_MCP_CLIENT,
            json.dumps(client_data)
        )

        logger.info(f"MCP OAuth: Registered new client: {client_data['client_name']}")

        return JSONResponse({
            "client_id": client_id,
            "client_name": client_data["client_name"],
            "redirect_uris": client_data["redirect_uris"],
            "grant_types": client_data["grant_types"],
            "response_types": client_data["response_types"],
            "token_endpoint_auth_method": "none",
        }, status_code=201)

    async def _check_mcp_access(self, token_payload: dict) -> bool:
        """
        Check if user is authorized for MCP access based on config.

        Only gates on the global ``enabled`` switch. Per-user tool access is
        resolved downstream by ``MCPToolAccessService`` using the user's roles
        and agent membership — non-admins without matching roles connect
        successfully and see an empty tool set rather than a 403.

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
        from src.core.security import decode_token

        logger.info(f"MCP auth: verify_token called with token prefix: {token[:20]}...")

        payload = decode_token(token, expected_type="access")
        if payload is None:
            logger.info("MCP auth: decode_token returned None (invalid/expired JWT)")
            return None

        logger.info(f"MCP auth: Token decoded for user {payload.get('email')}, checking access...")

        # Check MCP access permissions
        if not await self._check_mcp_access(payload):
            logger.info(f"MCP auth: _check_mcp_access returned False for user {payload.get('email')}")
            return None

        # Fetch user's role names from database for tool filtering
        role_names = await self._get_user_roles(payload.get("sub"))

        logger.info(f"MCP auth: Token valid for user {payload.get('email')}, roles={role_names}")

        return AccessToken(
            token=token,  # FastMCP requires this field
            client_id=str(payload.get("sub")),
            scopes=["mcp:access"],
            expires_at=payload.get("exp"),
            claims={
                "user_id": payload.get("sub"),
                "email": payload.get("email"),
                "name": payload.get("name"),
                "is_superuser": payload.get("is_superuser", False),
                "is_external": payload.get("is_external", False),
                "org_id": payload.get("org_id"),
                "roles": role_names,  # Role names for MCP tool filtering
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

        FastMCP requires AuthenticationMiddleware with BearerAuthBackend
        to validate tokens and set scope["user"]. Without this, all
        requests fail with 401 because the RequireAuthMiddleware can't
        find an authenticated user.
        """
        return [
            StarletteMiddleware(AuthenticationMiddleware, backend=BearerAuthBackend(self)),
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
