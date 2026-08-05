"""Deny-by-default HTTP capability policy for typed embed sessions."""

import logging
import re
from collections.abc import Callable
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.security import decode_token

logger = logging.getLogger(__name__)

_COMMON_RULES = {
    ("GET", "/auth/status"),
    ("GET", "/api/branding"),
    ("GET", "/health"),
}

_APP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"^/api/applications/[^/]+$")),
    ("GET", re.compile(r"^/api/applications/[^/]+/render$")),
    ("GET", re.compile(r"^/api/applications/[^/]+/files(?:/.*)?$")),
    ("GET", re.compile(r"^/api/applications/[^/]+/dependencies$")),
    ("POST", re.compile(r"^/api/workflows/execute$")),
    ("GET", re.compile(r"^/api/executions/[0-9a-f-]{36}(?:/.*)?$")),
    ("GET", re.compile(r"^/api/forms/[0-9a-f-]{36}/runtime$")),
    ("POST", re.compile(r"^/api/forms/[0-9a-f-]{36}/startup$")),
    ("POST", re.compile(r"^/api/forms/[0-9a-f-]{36}/upload$")),
    ("POST", re.compile(r"^/api/forms/[0-9a-f-]{36}/submissions$")),
    (
        "POST",
        re.compile(r"^/api/forms/[0-9a-f-]{36}/fields/[^/]+/options$"),
    ),
)

_FORM_RUNTIME_RE = re.compile(
    r"^/api/forms/(?P<form_id>[0-9a-f-]{36})/"
    r"(?P<action>runtime|startup|upload|submissions|captcha/challenge|fields/[^/]+/options)$"
)
_FORM_METHODS = {
    "runtime": "GET",
    "startup": "POST",
    "upload": "POST",
    "submissions": "POST",
    "captcha/challenge": "POST",
}
_EXECUTION_PATH_RE = re.compile(r"^/api/executions/([0-9a-f-]{36})")
_MAX_FORM_SESSION_BODY_BYTES = 512 * 1024


def _get_embed_payload(request: Request) -> dict[str, Any] | None:
    """Decode an embed JWT from every credential location accepted by auth."""

    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]
    elif "embed_token" in request.cookies:
        token = request.cookies["embed_token"]

    if not token:
        return None

    payload = decode_token(token, expected_type="access")
    if payload is None or payload.get("embed") is not True:
        return None
    return payload


def _matches_rules(
    method: str,
    path: str,
    rules: tuple[tuple[str, re.Pattern[str]], ...],
) -> bool:
    return any(rule_method == method and pattern.fullmatch(path) for rule_method, pattern in rules)


def embed_request_allowed(method: str, path: str, payload: dict[str, Any]) -> bool:
    """Return whether a typed embed claim authorizes this HTTP request."""

    method = method.upper()
    if (method, path) in _COMMON_RULES:
        return True

    embed_kind = payload.get("embed_kind")
    if embed_kind == "app":
        return _matches_rules(method, path, _APP_RULES)
    if embed_kind != "form":
        return False

    if payload.get("grant") == "hmac" and _EXECUTION_PATH_RE.fullmatch(path):
        return method == "GET"

    match = _FORM_RUNTIME_RE.fullmatch(path)
    if match is None or match.group("form_id") != payload.get("form_id"):
        return False

    action = match.group("action")
    if action == "captcha/challenge" and payload.get("grant") != "public":
        return False
    expected_method = (
        "POST" if action.startswith("fields/") else _FORM_METHODS.get(action)
    )
    return method == expected_method


class EmbedScopeMiddleware(BaseHTTPMiddleware):
    """Apply the typed embed capability policy before router authorization."""

    async def dispatch(self, request: Request, call_next: Callable):
        payload = _get_embed_payload(request)
        if payload is None:
            return await call_next(request)

        if not embed_request_allowed(request.method, request.url.path, payload):
            logger.warning(
                "Embed token denied access to %s %s",
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Embed tokens cannot access this endpoint"},
            )

        if payload.get("embed_kind") == "form" and request.method == "POST":
            content_length = request.headers.get("content-length")
            if (
                content_length is not None
                and content_length.isdigit()
                and int(content_length) > _MAX_FORM_SESSION_BODY_BYTES
            ):
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Form request is too large"},
                )

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

            async with get_redis() as redis:
                exists = await redis.exists(embed_execution_key(jti, execution_id))
            if not exists:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access denied to this execution"},
                )

        return await call_next(request)
