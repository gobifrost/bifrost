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

Auth strategy — mutually exclusive paths:

1. **MCP SDK HTTP context** (a tool call reached the MCP server via HTTP):
   :func:`mcp.server.auth.middleware.auth_context.get_access_token` yields the caller's
   bearer token. We reuse it directly.
2. **Runtime context** (an agent run, workflow or service attempt, with no
   FastMCP request on the stack): we mint a claims access token for the run
   with ``tokens.issue_access_token``. The client's transport re-mints and
   replays a request when the API answers with the ``refresh_required``
   challenge (a fact the token carries changed, possibly through this same
   bridge context's earlier request), at most three times per request.
3. **People context** (executor or test, no FastMCP request): we mint a
   claims access token for the database human subject, with the same
   ``issue_access_token`` minter and challenge-driven transport as runs.

Every request goes through the API's normal authentication: there is no
"trusted in-process" side door that skips auth validation.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

import httpx

if TYPE_CHECKING:
    from src.services.authorization.tokens import HumanSubject, RuntimeSubject
    from src.services.mcp_server.server import MCPContext

logger = logging.getLogger(__name__)

# When set (e.g. in E2E tests), route parity-tool REST calls over the network
# to an already-running API instance instead of the in-process FastAPI app.
# Format: full base URL without trailing slash (e.g. ``http://api:8000``).
_BRIDGE_URL_ENV = "BIFROST_MCP_HTTP_BRIDGE_URL"


# Challenge-driven re-mints allowed per bridge request, as for the SDK.
BRIDGE_REFRESH_BUDGET = 3


def _live_access_token() -> str | None:
    """The live MCP SDK access token (same bytes the external MCP client
    sent), when this call reached the MCP server over HTTP."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if token is not None and getattr(token, "token", None):
            return token.token
    except Exception:
        # Not in a FastMCP HTTP request: the caller mints instead.
        pass
    return None


def _runtime_subject(context: "MCPContext") -> "RuntimeSubject | None":
    """The run a runtime context acts for; None for a people context."""
    if context.runtime_credential_kind is None:
        return None
    from typing import cast
    from uuid import UUID

    from src.services.authorization.runtime import RuntimeKind
    from src.services.authorization.tokens import RuntimeSubject

    if context.runtime_credential_id is None:
        raise ValueError("Runtime MCP context requires a credential ID")
    service_id = None
    if context.runtime_credential_kind == "service_attempt":
        if context.runtime_service_id is None:
            raise ValueError("Service runtime MCP context requires a service ID")
        service_id = UUID(str(context.runtime_service_id))
    return RuntimeSubject(
        cast(RuntimeKind, context.runtime_credential_kind), UUID(str(context.runtime_credential_id)), service_id
    )


def _human_subject(context: "MCPContext") -> "HumanSubject":
    """Resolve a human subject; copied flags or lineage never become claims."""
    if context.runtime_credential_kind is not None:
        raise ValueError("A runtime MCP context requires a runtime subject")
    from uuid import UUID
    from src.services.authorization.tokens import HumanSubject

    return HumanSubject(UUID(str(context.user_id)))


def _build_in_process_transport() -> httpx.ASGITransport:
    """Bind httpx to the in-process FastAPI app."""
    from src.main import app

    return httpx.ASGITransport(app=app)


class BridgeRefreshingTransport(httpx.AsyncBaseTransport):
    """Carry a run's claims access token, re-minting it on the challenge.

    Every request is sent with the transport's current token. When the API
    answers with the ``refresh_required`` challenge (raised by its auth
    dependency before any handler effect), the transport mints a new token
    in-process with ``issue_access_token`` and replays the request, up to
    ``BRIDGE_REFRESH_BUDGET`` times per request; later requests on the same
    client use the newest token. If the run can no longer be authorized the
    challenge is returned as it is.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, subject: "RuntimeSubject | HumanSubject", token: str) -> None:
        self._inner = inner
        self._subject = subject
        self._token = token

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        from src.services.authorization.tokens import TokenSubjectUnavailable, issue_access_token
        from bifrost._refreshing import is_refresh_challenge

        request.headers["Authorization"] = f"Bearer {self._token}"
        response = await self._inner.handle_async_request(request)
        remaining = BRIDGE_REFRESH_BUDGET
        while remaining > 0 and is_refresh_challenge(response):
            remaining -= 1
            try:
                self._token = (await issue_access_token(self._subject)).token
            except TokenSubjectUnavailable:
                return response
            await response.aclose()
            request.headers["Authorization"] = f"Bearer {self._token}"
            response = await self._inner.handle_async_request(request)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


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
    headers = {
        "Content-Type": "application/json",
        # Self-reported audit attribution only (see
        # src.core.request_actor.resolve_self_reported_surface) — labels
        # this bridge call's audit rows with surface="mcp".
        "X-Bifrost-Surface": "mcp",
    }
    live = _live_access_token()
    subject = None
    if live is None:
        subject = _runtime_subject(context) or _human_subject(context)

    override_base = os.environ.get(_BRIDGE_URL_ENV)
    base_url = override_base.rstrip("/") if override_base else "http://mcp-bridge"
    transport: httpx.AsyncBaseTransport | None = (
        None if override_base else _build_in_process_transport()
    )
    if subject is not None:
        from src.services.authorization.tokens import issue_access_token

        token = (await issue_access_token(subject)).token
        transport = BridgeRefreshingTransport(transport or httpx.AsyncHTTPTransport(), subject, token)
    else:
        headers["Authorization"] = f"Bearer {live}"

    async with httpx.AsyncClient(
        transport=transport,
        base_url=base_url,
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
