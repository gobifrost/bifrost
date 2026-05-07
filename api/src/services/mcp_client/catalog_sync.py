"""Tool catalog refresh — ``tools/list`` against the vendor.

Catalog sync always uses the connection's shared service token (autonomous
mode resolution), never a per-user token. Two reasons:

1. Catalog sync is operator-initiated (admin clicks "Refresh tools" on the
   connection edit page) or scheduler-initiated; there's no caller user.
2. The catalog is per-connection, not per-user. Asking which tools the
   user-of-the-moment has access to would mix authorization decisions into
   a discovery operation.

If a connection has no service token configured, sync raises
``MisconfigError`` — there is no catalog to fetch. The frontend surfaces
this as a "Connect the service account first" prompt.

Tools that disappear from the vendor's ``tools/list`` are NOT deleted.
They are flagged ``enabled = False`` with a timestamped
``disabled_reason``. Admin preferences (a manually-disabled tool an admin
wants to keep disabled even if the vendor restores it) are preserved
across sync runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.models.orm.external_mcp import MCPConnection, MCPConnectionTool
from src.models.orm.oauth import OAuthProvider
from src.services.mcp_client import client as mcp_client_session
from src.services.mcp_client.auth_resolution import (
    _decode_access_token,
    _is_token_fresh,
    _refresh_token_in_place,
)
from src.services.mcp_client.errors import MisconfigError, ToolDispatchError

logger = logging.getLogger(__name__)


async def _resolve_service_token_for_sync(
    connection: MCPConnection, db: AsyncSession
) -> str:
    """Service-token-only resolver for catalog sync.

    Mirrors ``auth_resolution._service_token_healthy`` but raises
    ``MisconfigError`` directly on failure rather than returning ``None``;
    sync is fail-loud because the only caller is an admin asking to
    populate the catalog.
    """
    token = connection.service_oauth_token
    if token is None:
        raise MisconfigError(
            connection_id=connection.id,
            reason="catalog sync requires a service OAuth token; none configured",
        )

    result = await db.execute(
        select(OAuthProvider).where(OAuthProvider.id == token.provider_id)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise MisconfigError(
            connection_id=connection.id,
            reason=f"service OAuth token {token.id} has no provider row",
        )

    if not _is_token_fresh(token):
        if not await _refresh_token_in_place(token, provider, db):
            raise MisconfigError(
                connection_id=connection.id,
                reason=(
                    f"service OAuth token {token.id} expired and refresh failed"
                ),
            )

    return _decode_access_token(token)


async def sync_catalog(
    connection: MCPConnection,
    db: AsyncSession,
) -> list[MCPConnectionTool]:
    """Fetch ``tools/list`` from the vendor and upsert ``mcp_connection_tools``.

    Args:
        connection: The connection to sync. Must have its ``server`` and
            ``service_oauth_token`` relationships loaded.
        db: Active async session. Upserts and disable-flags are committed
            here.

    Returns:
        The full list of ``MCPConnectionTool`` rows for this connection
        after the upsert (including ones that were disabled by this run).

    Raises:
        MisconfigError: No service token, no provider, or refresh failed.
        ToolDispatchError: The vendor's ``tools/list`` call raised.
    """
    access_token = await _resolve_service_token_for_sync(connection, db)

    try:
        async with mcp_client_session.open_session(connection, access_token) as session:
            tools_result = await session.list_tools()
    except Exception as exc:
        raise ToolDispatchError(
            f"tools/list failed for connection {connection.id}: {exc}",
            connection_id=connection.id,
        ) from exc

    now = datetime.now(timezone.utc)
    seen_names: set[str] = set()

    # Pre-fetch existing rows for this connection so we can do an in-process
    # diff rather than per-row SELECTs.
    existing_result = await db.execute(
        select(MCPConnectionTool).where(
            MCPConnectionTool.connection_id == connection.id
        )
    )
    existing_by_name: dict[str, MCPConnectionTool] = {
        row.tool_name: row for row in existing_result.scalars().all()
    }

    for tool in tools_result.tools:
        seen_names.add(tool.name)
        # The MCP SDK exposes the JSON Schema as ``inputSchema`` on the
        # Tool model. Persist it verbatim so the planner can re-emit it
        # to the LLM without re-shaping.
        schema = (
            tool.inputSchema
            if isinstance(tool.inputSchema, dict)
            else (tool.inputSchema.model_dump() if tool.inputSchema is not None else {})
        )

        existing = existing_by_name.get(tool.name)
        if existing is not None:
            existing.tool_schema = schema
            existing.last_seen_at = now
            # Re-enabling a previously-removed tool: clear the auto-disable
            # reason but ONLY if the previous reason was an auto-removal.
            # An admin's manual disable should survive a vendor restoration.
            if (
                not existing.enabled
                and existing.disabled_reason
                and existing.disabled_reason.startswith("Removed from server catalog")
            ):
                existing.enabled = True
                existing.disabled_reason = None
        else:
            new_row = MCPConnectionTool(
                connection_id=connection.id,
                tool_name=tool.name,
                tool_schema=schema,
                enabled=True,
                disabled_reason=None,
                last_seen_at=now,
            )
            db.add(new_row)
            existing_by_name[tool.name] = new_row

    # Mark any rows that didn't appear in the response as removed-by-vendor.
    # Preserve admin manual disables — only flip rows that are currently
    # enabled (admin-disabled rows are already disabled with their own reason).
    for name, row in existing_by_name.items():
        if name in seen_names:
            continue
        if not row.enabled:
            continue
        row.enabled = False
        row.disabled_reason = f"Removed from server catalog at {now.isoformat()}"

    await db.commit()

    # Return the post-upsert state — re-fetch with a fresh load so the
    # caller sees the durable rows including any newly-inserted IDs.
    final_result = await db.execute(
        select(MCPConnectionTool)
        .where(MCPConnectionTool.connection_id == connection.id)
        .options(joinedload(MCPConnectionTool.connection))
    )
    return list(final_result.scalars().all())
