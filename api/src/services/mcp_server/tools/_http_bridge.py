"""In-process REST bridge for MCP parity tools.

Task 6 of the CLI mutation surface + MCP parity plan.

The parity tools added in Task 6 are **thin wrappers** over the REST API —
they must not touch the ORM, repositories, or a long-lived ``AsyncSession``.
This module gives every parity-tool handler a single helper,
:func:`rest_client`, that yields an :class:`httpx.AsyncClient` bound to the
in-process FastAPI app with the caller's auth already attached.

Why in-process HTTP instead of calling router functions directly:

* The REST handler is the canonical place where side effects (cache
  invalidation, audit logs, ``RepoSyncWriter``, role syncs) happen.
* Importing a router function still requires assembling the dependency
  graph (``ctx``, ``db``, ``user``) by hand, which re-invents the drift the
  plan is trying to prevent.
* Going through Starlette/FastAPI's ASGI transport reuses routing, auth,
  Pydantic validation, and error handling exactly as an external caller
  would, so parity with the CLI is structural, not best-effort.

Auth strategy — two mutually exclusive paths:

1. **FastMCP HTTP context** (a tool call reached the MCP server via HTTP):
   :func:`fastmcp.server.dependencies.get_access_token` yields the caller's
   bearer token. We reuse it directly.
2. **Executor / test context** (no FastMCP request on the stack, the MCP
   context was constructed by the agent executor or a unit test): we mint
   a short-lived JWT matching the ``MCPContext``'s claims using the same
   ``create_access_token`` helper Bifrost uses for login. This keeps every
   request going through :mod:`src.core.security` — there is no "trusted
   in-process" side door that skips auth validation.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

import httpx

if TYPE_CHECKING:
    from src.services.mcp_server.server import MCPContext

logger = logging.getLogger(__name__)

# When set (e.g. in E2E tests), route parity-tool REST calls over the network
# to an already-running API instance instead of the in-process FastAPI app.
# Format: full base URL without trailing slash (e.g. ``http://api:8000``).
_BRIDGE_URL_ENV = "BIFROST_MCP_HTTP_BRIDGE_URL"


def _token_from_context(context: "MCPContext") -> str:
    """Return a bearer token usable for an in-process REST call.

    Prefers the live FastMCP access token (same bytes the external MCP
    client sent); falls back to minting a short-lived JWT from the
    context's claims so executor / test contexts still go through the
    standard auth path.
    """
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
        if token is not None and getattr(token, "token", None):
            return token.token
    except Exception:
        # Not in a FastMCP HTTP request — fall through to minting.
        pass

    from src.core.security import create_access_token

    claims: dict[str, Any] = {
        "sub": str(context.user_id) if context.user_id else "",
        "email": context.user_email or "",
        "name": context.user_name or "",
        "is_superuser": bool(context.is_platform_admin),
        # OPEN-F: carry is_external from the MCP context. The real user-token
        # mints (mcp_server/auth.py) stamp this via resolve_external_claim;
        # this fallback mint (executor / test contexts) is the one site that
        # dropped it, re-opening the global tier to external MCP principals.
        "is_external": bool(getattr(context, "is_external", False)),
        "org_id": str(context.org_id) if context.org_id else None,
    }
    return create_access_token(data=claims)


def _build_in_process_transport() -> httpx.ASGITransport:
    """Bind httpx to the in-process FastAPI app."""
    from src.main import app

    return httpx.ASGITransport(app=app)


@asynccontextmanager
async def rest_client(context: "MCPContext") -> AsyncIterator[httpx.AsyncClient]:
    """Yield an ``httpx.AsyncClient`` bound to the API with auth attached.

    By default the client uses an in-process :class:`httpx.ASGITransport`
    bound to :data:`src.main.app`; no network socket is touched. Set
    ``BIFROST_MCP_HTTP_BRIDGE_URL`` to route through an external base URL
    instead (the E2E test suite uses this to aim at the running API
    container so real writes land where ``e2e_client`` can see them).

    Usage::

        async with rest_client(context) as client:
            resp = await client.post("/api/roles", json=body)
            resp.raise_for_status()
            return resp.json()
    """
    token = _token_from_context(context)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    override_base = os.environ.get(_BRIDGE_URL_ENV)
    if override_base:
        async with httpx.AsyncClient(
            base_url=override_base.rstrip("/"),
            headers=headers,
            timeout=30.0,
        ) as client:
            yield client
        return

    transport = _build_in_process_transport()
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://mcp-bridge",
        headers=headers,
        timeout=30.0,
    ) as client:
        yield client


async def call_rest(
    context: "MCPContext",
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Issue a REST request through the in-process bridge.

    Returns ``(status_code, parsed_body)``. The parsed body is either
    ``dict`` / ``list`` (on JSON responses), ``None`` (on 204), or a raw
    string (on non-JSON bodies).
    """
    async with rest_client(context) as client:
        response = await client.request(
            method.upper(),
            path,
            json=json_body,
            params=params,
        )

    if response.status_code == 204:
        return response.status_code, None

    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


__all__ = ["call_rest", "rest_client"]
