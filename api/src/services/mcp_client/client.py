"""Streamable HTTP MCP client wrapper, per connection.

Exactly one transport: ``mcp.client.streamable_http.streamablehttp_client``.
No SSE, no stdio — both are deliberately omitted. If a future requirement
lands for stdio transport (e.g. a local development server), it goes in a
separate module rather than as a parallel branch here, so the public surface
stays small and the failure modes stay predictable.

The caller — ``dispatch.invoke`` and ``catalog_sync.sync_catalog`` — owns
auth resolution. By the time we get the ``access_token`` here, the caller
has already decided whether the vendor will see the user identity or the
service identity. This module only carries bytes over a wire.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from src.models.orm.external_mcp import MCPConnection

logger = logging.getLogger(__name__)


def _resolve_server_url(connection: MCPConnection) -> str:
    """Pick the URL to dial for a connection.

    Per-org URL overrides (``server_url_override``) take precedence over
    the template's default URL. Most orgs leave the override blank.
    """
    return connection.server_url_override or connection.server.server_url


@asynccontextmanager
async def open_session(
    connection: MCPConnection,
    access_token: str,
) -> AsyncIterator[ClientSession]:
    """Open an initialized Streamable HTTP MCP session for a connection.

    Yields a fully-initialized ``ClientSession`` ready for ``list_tools()``,
    ``call_tool(...)``, etc. The session is torn down when the ``async with``
    block exits.

    Args:
        connection: The ``MCPConnection`` row whose server URL (or override)
            we dial. The connection's ``server`` relationship must already
            be loaded — callers fetch it with ``joinedload`` or rely on the
            ORM's eager-load default.
        access_token: The Bearer token to send. Resolution of which token
            to use (per-user vs. shared service) happens in ``auth_resolution``;
            this layer is auth-agnostic.
    """
    server_url = _resolve_server_url(connection)
    headers = {"Authorization": f"Bearer {access_token}"}

    logger.debug(
        "Opening MCP session: connection=%s url=%s",
        connection.id,
        server_url,
    )

    async with streamablehttp_client(server_url, headers=headers) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session
