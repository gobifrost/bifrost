"""OAuth metadata discovery for external MCP servers.

When an admin pastes a server URL in the "New MCP Server" form (mockup §3)
and clicks "Discover OAuth metadata", the router calls
``discover_oauth_metadata`` which fetches the two RFC-defined ``/.well-known``
endpoints and merges them into a single dict. Authorization-server metadata
is host-scoped; RFC 9728 protected-resource metadata preserves the MCP resource
path. The
result preserves each discovery document under its own key while retaining
the flattened fields consumed by the existing form. The payload is stored on
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
_PROTECTED_RESOURCE_FLAT_FIELDS = frozenset(
    {
        "resource",
        "authorization_servers",
        "bearer_methods_supported",
        "authorization_details_types_supported",
        "dpop_bound_access_tokens_required",
        "dpop_signing_alg_values_supported",
        "resource_documentation",
        "resource_name",
        "resource_policy_uri",
        "resource_signing_alg_values_supported",
        "resource_encryption_alg_values_supported",
        "resource_encryption_enc_values_supported",
        "resource_tos_uri",
        "scopes_supported",
        "jwks_uri",
        "signed_metadata",
        "tls_client_certificate_bound_access_tokens",
        # Compatibility alias used by existing MCP server templates.
        "audience",
    }
)


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


def _protected_resource_metadata_url(server_url: str) -> str:
    """Build the RFC 9728 path-aware protected-resource metadata URL."""
    parsed = urlparse(server_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid server_url: {server_url!r}")
    resource_path = parsed.path.rstrip("/")
    well_known_path = f"{_PROTECTED_RESOURCE_PATH}{resource_path}"
    return urlunparse(
        (parsed.scheme, parsed.netloc, well_known_path, "", "", "")
    )


async def _fetch_well_known(
    client: httpx.AsyncClient, url: str
) -> dict[str, Any] | None:
    """Fetch a single ``/.well-known`` endpoint, returning its JSON body or None."""
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

    Fetches ``/.well-known/oauth-authorization-server`` from the server host
    and the RFC 9728 path-aware protected-resource metadata URL, then
    preserves them separately. Flattened compatibility fields are also
    returned for the form; the protected-resource document takes precedence
    for resource-scoped fields.

    Args:
        server_url: The MCP resource URL. Query and fragment are ignored;
            its path is retained for protected-resource metadata discovery.

    Returns:
        Metadata dict on success, including separate authorization-server and
        protected-resource documents, or ``None`` when neither endpoint is
        reachable / returns valid JSON.
    """
    try:
        base = _well_known_base(server_url)
    except ValueError as exc:
        logger.warning("MCP discovery: %s", exc)
        return None

    timeout = httpx.Timeout(_DISCOVERY_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        authz_doc = await _fetch_well_known(client, base + _AUTHZ_SERVER_PATH)
        resource_url = _protected_resource_metadata_url(server_url)
        resource_doc = await _fetch_well_known(client, resource_url)

    if authz_doc is None and resource_doc is None:
        return None

    merged: dict[str, Any] = {
        "authorization_server_metadata": authz_doc,
        "protected_resource_metadata": resource_doc,
    }
    if authz_doc:
        merged.update(authz_doc)
    if resource_doc:
        # RFC 9728 metadata may override only resource-scoped compatibility
        # fields. In particular, never allow it to replace the authorization
        # server's issuer or endpoints in the flattened snapshot.
        merged.update(
            {
                key: value
                for key, value in resource_doc.items()
                if key in _PROTECTED_RESOURCE_FLAT_FIELDS
            }
        )

    return merged
