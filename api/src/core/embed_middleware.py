"""
Embed Token Scope Middleware

Restricts embed tokens to only the API endpoints needed for rendering
an embedded app. Embed tokens are NOT superusers and should only access
app rendering, workflow execution, and related endpoints.

Also enforces execution scoping: embed tokens can only access executions
they created (tracked via Redis using the token's jti).
"""

import logging
import re
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.security import decode_token

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class EmbedRouteRule:
    methods: frozenset[str]
    pattern: re.Pattern[str]
    scope: str | None = None


# Paths that embed tokens are allowed to access. Rules are method-aware so
# read/render allowlist entries cannot accidentally authorize mutations.
EMBED_ALLOWED_RULES = [
    # App loading and rendering
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/api/applications/([^/]+)$"), "app_ref"),
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/api/applications/([^/]+)/render$"), "app_id"),
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/api/applications/([^/]+)/files(?:/.*)?$"), "app_id"),
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/api/applications/([^/]+)/dependencies$"), "app_id"),

    # Workflow execution
    EmbedRouteRule(frozenset({"POST"}), re.compile(r"^/api/workflows/execute$"), "app_token"),
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/api/executions/"), None),

    # Auth status (SPA checks this on load)
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/auth/status$"), None),

    # Branding (SPA loads this for theming)
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/api/branding$"), None),

    # WebSocket for execution streaming
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/ws"), None),

    # Health check
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/health$"), None),

    # Form loading and execution
    EmbedRouteRule(frozenset({"GET"}), re.compile(r"^/api/forms/([^/]+)$"), "form_id"),
    EmbedRouteRule(frozenset({"POST"}), re.compile(r"^/api/forms/([^/]+)/execute$"), "form_id"),
    EmbedRouteRule(frozenset({"POST"}), re.compile(r"^/api/forms/([^/]+)/startup$"), "form_id"),
    EmbedRouteRule(frozenset({"POST"}), re.compile(r"^/api/forms/([^/]+)/upload$"), "form_id"),
]

# Pattern to extract execution_id from /api/executions/{id}... paths
_EXECUTION_PATH_RE = re.compile(r"^/api/executions/([0-9a-f-]{36})")


def _get_embed_payload(request: Request) -> dict | None:
    """Return the decoded JWT payload if this is an embed token, else None."""
    auth_header = request.headers.get("authorization", "")
    token = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
    elif "embed_token" in request.cookies:
        token = request.cookies["embed_token"]

    if not token:
        return None

    payload = decode_token(token)
    if payload is None:
        return None

    if payload.get("embed", False) is not True:
        return None

    return payload


def _scope_allowed(scope: str | None, match: re.Match[str], payload: dict) -> bool:
    if scope is None:
        return True

    if scope == "app_token":
        return bool(payload.get("app_id")) and not payload.get("form_id")

    if scope == "app_id":
        return match.group(1) == payload.get("app_id")

    if scope == "app_ref":
        allowed_refs = {payload.get("app_id"), payload.get("app_slug")}
        allowed_refs.discard(None)
        return match.group(1) in allowed_refs

    if scope == "form_id":
        return match.group(1) == payload.get("form_id")

    return False


def _path_allowed(method: str, path: str, payload: dict) -> bool:
    """Check if the path is in the embed allowlist."""
    for rule in EMBED_ALLOWED_RULES:
        if method.upper() not in rule.methods:
            continue
        match = rule.pattern.match(path)
        if match and _scope_allowed(rule.scope, match, payload):
            return True
    return False


class EmbedScopeMiddleware(BaseHTTPMiddleware):
    """Restrict embed tokens to app-rendering endpoints only."""

    async def dispatch(self, request: Request, call_next):
        # Only check requests with Bearer tokens that have embed=true
        payload = _get_embed_payload(request)
        if payload is None:
            return await call_next(request)

        if not _path_allowed(request.method, request.url.path, payload):
            logger.warning(
                f"Embed token denied access to {request.method} {request.url.path}"
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Embed tokens cannot access this endpoint"},
            )

        # Enforce execution scoping: embed tokens can only access their own executions
        match = _EXECUTION_PATH_RE.match(request.url.path)
        if match:
            execution_id = match.group(1)
            jti = payload.get("jti")
            if not jti:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Embed token missing session identifier"},
                )

            from src.core.cache.keys import embed_execution_key
            from src.core.cache.redis_client import get_redis

            async with get_redis() as r:
                exists = await r.exists(embed_execution_key(jti, execution_id))

            if not exists:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access denied to this execution"},
                )

        return await call_next(request)
