"""OAuth metadata discovery for external MCP servers.

When an admin pastes a server URL in the "New MCP Server" form (mockup §3)
and clicks "Discover OAuth metadata", the router calls
``discover_oauth_metadata`` which fetches the two RFC-defined ``/.well-known``
endpoints from the server's host and merges them into a single dict. The
result populates the form fields (authorization URL, token URL, audience,
scopes) and the raw payload is stored verbatim on
``MCPServer.discovery_metadata`` for diff-on-rediscovery later.

Per the design: 5-second timeout, no retries, no global client. These are
infrequent admin operations — the cost of a fresh ``httpx.AsyncClient`` per
call is negligible compared to the simplicity of not managing a long-lived
client. ``None`` is returned on 404 / connect timeout / invalid JSON; the
caller falls back to manual entry rather than retrying automatically (see
spec: "operators should know whether they're working from discovery or from
manual config").
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)


_DISCOVERY_TIMEOUT_SECONDS = 5.0
_AUTHZ_SERVER_PATH = "/.well-known/oauth-authorization-server"
_PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"


def _well_known_base(server_url: str) -> str:
    """Strip path/query/fragment to get the scheme://host[:port] base.

    The two ``/.well-known`` endpoints live at the *server's host*, not at
    sub-paths beneath the MCP endpoint. A vendor whose MCP endpoint is at
    ``https://graph.microsoft.com/v1.0/copilot/mcp`` exposes discovery at
    ``https://graph.microsoft.com/.well-known/...``.
    """
    parsed = urlparse(server_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid server_url: {server_url!r}")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


async def _fetch_well_known(
    client: httpx.AsyncClient, base: str, path: str
) -> dict[str, Any] | None:
    """Fetch a single ``/.well-known`` endpoint, returning its JSON body or None."""
    url = base + path
    try:
        response = await client.get(url)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as exc:
        logger.debug(
            "MCP discovery: %s unreachable (%s)", url, exc.__class__.__name__
        )
        return None

    if response.status_code == 404:
        logger.debug("MCP discovery: %s returned 404", url)
        return None
    if response.status_code >= 400:
        logger.warning(
            "MCP discovery: %s returned %s", url, response.status_code
        )
        return None

    try:
        body = response.json()
    except ValueError:
        logger.warning("MCP discovery: %s returned non-JSON body", url)
        return None

    if not isinstance(body, dict):
        logger.warning(
            "MCP discovery: %s returned non-object JSON (%s)", url, type(body).__name__
        )
        return None

    return body


async def discover_oauth_metadata(server_url: str) -> dict[str, Any] | None:
    """Discover OAuth metadata for an MCP server via ``/.well-known``.

    Fetches both ``/.well-known/oauth-authorization-server`` and
    ``/.well-known/oauth-protected-resource`` from the server's host and
    merges them into a single dict. The protected-resource document, when
    present, is layered on top of the authorization-server document so its
    fields (e.g. ``audience``, ``resource``, ``scopes_supported``) take
    precedence on conflict — RFC 9728 treats the protected-resource
    document as authoritative for resource-scoped fields.

    Args:
        server_url: The MCP server URL. Path/query are stripped before
            building the well-known URLs (the spec mandates the well-known
            documents live at the *host root*, not under the MCP path).

    Returns:
        Merged metadata dict on success, or ``None`` when neither endpoint
        is reachable / returns valid JSON. Callers fall back to manual
        entry on ``None``.
    """
    try:
        base = _well_known_base(server_url)
    except ValueError as exc:
        logger.warning("MCP discovery: %s", exc)
        return None

    timeout = httpx.Timeout(_DISCOVERY_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        authz_doc = await _fetch_well_known(client, base, _AUTHZ_SERVER_PATH)
        resource_doc = await _fetch_well_known(client, base, _PROTECTED_RESOURCE_PATH)

    if authz_doc is None and resource_doc is None:
        return None

    merged: dict[str, Any] = {}
    if authz_doc:
        merged.update(authz_doc)
    if resource_doc:
        # RFC 9728: protected-resource doc is authoritative for resource-scoped fields
        merged.update(resource_doc)

    return merged
