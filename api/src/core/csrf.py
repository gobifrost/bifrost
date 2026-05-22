"""
CSRF Protection Middleware

Provides Cross-Site Request Forgery protection for cookie-based authentication.
Uses the double-submit cookie pattern where:
1. A CSRF token is stored in a non-httpOnly cookie (JS readable)
2. The same token must be sent in the X-CSRF-Token header
3. Both values must match for the request to proceed

Note: Bearer token authentication (Authorization header) does NOT require
CSRF protection since the token is not automatically included by browsers.
"""

import logging
from typing import Awaitable, Callable

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.security import validate_csrf_token

logger = logging.getLogger(__name__)

# Methods that require CSRF protection
UNSAFE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Endpoints exempt from CSRF (auth endpoints that set cookies)
# Note: Auth routes use /auth prefix (no /api prefix)
CSRF_EXEMPT_PATHS = {
    "/auth/login",
    "/auth/register",
    "/auth/refresh",
    "/auth/oauth/callback",
    "/auth/mfa/login",
    "/auth/mfa/login/setup",
    "/auth/mfa/login/verify",
    "/auth/passkeys/authenticate/options",
    "/auth/passkeys/authenticate/verify",
    "/auth/setup/passkey/options",  # First-time passkey setup (no auth)
    "/auth/setup/passkey/verify",  # First-time passkey setup (no auth)
    "/auth/register-from-invite",  # Invite-based registration (token-gated, no cookies yet)
    "/auth/device/code",  # Device flow: request code (no auth)
    "/auth/device/token",  # Device flow: exchange code for token (no auth)
    "/health",
    "/ready",
    "/",
    # MCP OAuth endpoints (called by external MCP clients)
    "/authorize",
    "/token",
    "/register",
    "/mcp/callback",
}

# Path prefixes that are exempt from CSRF (public webhook endpoints)
CSRF_EXEMPT_PREFIXES = (
    "/api/hooks/",  # Webhook receiver - called by external services, no auth
    "/api/endpoints/",  # API key auth - called with X-Bifrost-Key header
    "/embed/",  # Embed entry points - HMAC-verified, no cookie auth
)


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    CSRF protection middleware for cookie-based authentication.

    Only enforces CSRF for requests that:
    1. Use an unsafe HTTP method (POST, PUT, DELETE, PATCH)
    2. Are authenticated via cookies (access_token cookie present)
    3. Do NOT have an Authorization header (Bearer token auth)
    4. Are NOT exempt paths (login, register, etc.)

    This allows API clients using Bearer tokens to work without CSRF,
    while protecting browser sessions that use cookies.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request and enforce CSRF protection where needed."""

        # Only check unsafe methods
        if request.method not in UNSAFE_METHODS:
            return await call_next(request)

        # Check if path is exempt
        path = request.url.path
        if path in CSRF_EXEMPT_PATHS:
            return await call_next(request)

        # Check if path matches an exempt prefix
        if path.startswith(CSRF_EXEMPT_PREFIXES):
            return await call_next(request)

        # Only enforce CSRF for cookie-based auth
        has_cookie_auth = "access_token" in request.cookies
        has_embed_auth = "embed_token" in request.cookies
        header_keys_lower = {k.lower() for k in request.headers.keys()}
        has_bearer_auth = "authorization" in header_keys_lower
        has_api_key_auth = "x-bifrost-key" in header_keys_lower

        # If using Bearer token, API key, or embed token, CSRF is not needed.
        # Embed tokens are obtained via HMAC verification and are scoped —
        # they cannot be forged by a CSRF attack since the attacker doesn't
        # have the HMAC secret to obtain a valid embed token.
        if has_bearer_auth or has_api_key_auth or has_embed_auth:
            return await call_next(request)

        # If using cookie auth, CSRF is required
        if has_cookie_auth:
            csrf_cookie = request.cookies.get("csrf_token")
            csrf_header = request.headers.get("X-CSRF-Token")

            if not csrf_cookie or not csrf_header:
                logger.warning(
                    f"CSRF token missing for {request.method} {path}",
                    extra={
                        "has_cookie": bool(csrf_cookie),
                        "has_header": bool(csrf_header),
                        "client_ip": request.client.host
                        if request.client
                        else "unknown",
                    },
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "CSRF token missing"},
                )

            if not validate_csrf_token(csrf_cookie, csrf_header):
                logger.warning(
                    f"CSRF token mismatch for {request.method} {path}",
                    extra={
                        "client_ip": request.client.host
                        if request.client
                        else "unknown",
                    },
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "CSRF token mismatch"},
                )

        return await call_next(request)
