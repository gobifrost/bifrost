"""Tool invocation entry point — called by agent executors in Phase 3.

This is the single dispatch path for an agent calling an external MCP
tool. The chat executor and the autonomous executor both route through
``invoke``. ``caller_user_id`` is threaded all the way down so the
``auth_resolution`` layer makes the user-vs-service decision exactly
once per call.

Result envelope: the MCP SDK returns ``CallToolResult`` (a Pydantic model
with ``content``, ``structuredContent``, and ``isError``). We normalize
this to a plain ``dict`` matching the shape Bifrost workflow tools
return — the executor wraps this dict in a ``ToolResult`` envelope. We
keep the structured content first-class because LLMs prefer JSON output
over text when both are available, and we keep the text content as a
fallback for tools that haven't migrated to structured output.

401/403 retry: if the vendor returns an auth error AFTER token resolution
(meaning the token was fresh-by-our-clock but the vendor disagrees), we
attempt a single forced refresh and retry. If still 401/403, we raise
``NeedsReauthError`` for the chat case and ``ToolDispatchError`` for the
autonomous case (an autonomous run can't prompt a user to reconnect).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from mcp.types import CallToolResult

from src.models.orm.external_mcp import MCPConnection, MCPConnectionTool
from src.services.mcp_client import client as mcp_client_session
from src.services.mcp_client.auth_resolution import (
    ResolutionPath,
    resolve_token,
)
from src.services.mcp_client.errors import (
    NeedsReauthError,
    ToolDispatchError,
)

logger = logging.getLogger(__name__)


# Substrings that indicate the vendor rejected the token. Different
# servers report 401/403 in different shapes — we match conservatively
# rather than parse a specific error format.
_AUTH_ERROR_MARKERS = ("401", "403", "unauthorized", "forbidden", "invalid_token")


def _looks_like_auth_error(exc: BaseException) -> bool:
    """Heuristic: did the remote MCP server reject our token?

    The MCP SDK doesn't expose HTTP status codes directly — they bubble up
    inside protocol-level exceptions. We match on substring across the
    exception chain so transport, protocol, and tool-level rejections all
    funnel through the same retry path.
    """
    msg_chain: list[str] = []
    cur: BaseException | None = exc
    while cur is not None:
        msg_chain.append(str(cur).lower())
        cur = cur.__cause__ or cur.__context__
    blob = " ".join(msg_chain)
    return any(marker in blob for marker in _AUTH_ERROR_MARKERS)


def _normalize_call_tool_result(result: CallToolResult) -> dict[str, Any]:
    """Translate an MCP CallToolResult to Bifrost's tool-result envelope.

    The envelope shape matches what workflow tools return:
    ``{"content": <text-or-blocks>, "structured_content": <dict|None>,
       "is_error": bool}``.

    Structured content (``CallToolResult.structuredContent``) is preserved
    verbatim — the planner prefers structured JSON over text when both
    are present.
    """
    content_blocks: list[dict[str, Any]] = []
    for block in result.content:
        try:
            content_blocks.append(block.model_dump())
        except AttributeError:
            # Defensive: a future SDK may yield a non-Pydantic content type
            content_blocks.append({"type": "unknown", "value": str(block)})

    return {
        "content": content_blocks,
        "structured_content": result.structuredContent,
        "is_error": bool(result.isError),
    }


async def _load_tool(
    connection_id: UUID, tool_name: str, db: AsyncSession
) -> MCPConnectionTool | None:
    """Fetch the catalog row for a (connection, tool_name) pair."""
    result = await db.execute(
        select(MCPConnectionTool).where(
            MCPConnectionTool.connection_id == connection_id,
            MCPConnectionTool.tool_name == tool_name,
        )
    )
    return result.scalar_one_or_none()


async def _call_remote(
    connection: MCPConnection,
    access_token: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> CallToolResult:
    """Open a session, call the tool, return the raw result."""
    async with mcp_client_session.open_session(connection, access_token) as session:
        return await session.call_tool(tool_name, arguments)


async def invoke(
    connection: MCPConnection,
    tool_name: str,
    arguments: dict[str, Any],
    caller_user_id: UUID | None,
    db: AsyncSession,
) -> dict[str, Any]:
    """Invoke an external MCP tool and return its normalized result.

    Args:
        connection: Target ``MCPConnection`` (with ``server`` and
            ``service_oauth_token`` eager-loaded).
        tool_name: Tool to invoke. Must exist in ``mcp_connection_tools``
            and be ``enabled``.
        arguments: Argument dict to forward to the tool.
        caller_user_id: User invoking the tool (chat / signed-claim
            webhook), or ``None`` for autonomous runs. Threaded into
            ``resolve_token`` for the user-vs-service decision.
        db: Active async session.

    Returns:
        Normalized result envelope. Always includes ``content``,
        ``structured_content``, and ``is_error``. Adds an internal
        ``_resolution_path`` key the executor can record in its audit log.

    Raises:
        ToolDispatchError: Tool unknown/disabled, or remote server
            errored beyond the single retry budget.
        NeedsReauthError: Per-user token rejected by vendor and no
            fallback available (chat path), or auth_resolution determined
            the same up front.
        MisconfigError: From auth_resolution, when an autonomous caller
            slips through to a connection that should have been filtered.
    """
    catalog_row = await _load_tool(connection.id, tool_name, db)
    if catalog_row is None:
        raise ToolDispatchError(
            f"Tool {tool_name!r} not found in catalog for connection {connection.id}",
            connection_id=connection.id,
            tool_name=tool_name,
        )
    if not catalog_row.enabled:
        reason = catalog_row.disabled_reason or "disabled"
        raise ToolDispatchError(
            f"Tool {tool_name!r} is disabled on connection {connection.id}: {reason}",
            connection_id=connection.id,
            tool_name=tool_name,
        )

    access_token, resolution_path = await resolve_token(
        connection, caller_user_id, db
    )

    try:
        result = await _call_remote(connection, access_token, tool_name, arguments)
    except Exception as exc:
        if not _looks_like_auth_error(exc):
            raise ToolDispatchError(
                f"MCP call_tool {tool_name!r} on connection {connection.id} failed: {exc}",
                connection_id=connection.id,
                tool_name=tool_name,
            ) from exc

        # 401/403 retry: invalidate cached state and resolve again.
        # ``resolve_token`` will refresh the underlying token row if it's
        # expired and otherwise return the same one — but on the second
        # pass, we treat USER_TOKEN that still 401s as needs-reauth.
        logger.info(
            "MCP auth rejection on connection %s tool %s; refreshing and retrying once",
            connection.id,
            tool_name,
        )
        await db.refresh(connection)
        access_token, resolution_path = await resolve_token(
            connection, caller_user_id, db
        )
        try:
            result = await _call_remote(
                connection, access_token, tool_name, arguments
            )
        except Exception as retry_exc:
            if (
                _looks_like_auth_error(retry_exc)
                and resolution_path == ResolutionPath.USER_TOKEN
                and caller_user_id is not None
            ):
                raise NeedsReauthError(
                    reauth_url=f"/me/connections/{connection.id}/connect",
                    connection_id=connection.id,
                    tool_name=tool_name,
                ) from retry_exc
            raise ToolDispatchError(
                f"MCP call_tool {tool_name!r} on connection {connection.id} failed after retry: {retry_exc}",
                connection_id=connection.id,
                tool_name=tool_name,
            ) from retry_exc

    envelope = _normalize_call_tool_result(result)
    envelope["_resolution_path"] = resolution_path.value

    # Result-size guardrail: an MCP tool returning hundreds of KB of JSON can
    # blow the LLM's context window on the next turn. Cap the payload and
    # replace it with a structured truncation message that nudges the LLM to
    # use filters / pagination on retry. The cap is generous (default ~250KB
    # ≈ ~60K tokens) so well-behaved tools pass through untouched, but
    # protects us from "list_tickets() with no args" returning 1000+ rows.
    envelope = _enforce_result_size_cap(envelope, tool_name, connection.id)

    return envelope


# Conservative default ~250KB serialized JSON. Anthropic's input-token limit
# is 1M; at ~4 chars/token this is ~60K tokens, leaving plenty of headroom
# for system prompt, conversation history, and other tools' results.
_MAX_TOOL_RESULT_BYTES = 250_000


def _enforce_result_size_cap(
    envelope: dict[str, Any], tool_name: str, connection_id: UUID
) -> dict[str, Any]:
    """Truncate oversized MCP tool results so they don't blow the LLM context.

    Replaces ``content``+``structured_content`` with a single text block
    explaining the truncation and a hint on how to retry. The original
    payload size is preserved as a number so the LLM can reason about
    whether to narrow the call.
    """
    import json

    try:
        serialized = json.dumps(
            {
                "content": envelope.get("content"),
                "structured_content": envelope.get("structured_content"),
            },
            default=str,
        )
    except (TypeError, ValueError):
        # Non-JSON-serializable; fall back to str length on the parts.
        serialized = str(envelope.get("content")) + str(
            envelope.get("structured_content")
        )

    size_bytes = len(serialized.encode("utf-8"))
    if size_bytes <= _MAX_TOOL_RESULT_BYTES:
        return envelope

    truncation_note = (
        f"⚠️ MCP tool '{tool_name}' returned {size_bytes:,} bytes "
        f"(~{size_bytes // 4:,} tokens), exceeding Bifrost's per-call "
        f"result cap of {_MAX_TOOL_RESULT_BYTES:,} bytes. "
        f"The full payload was discarded to protect the model's context "
        f"window.\n\n"
        f"To retry successfully, narrow the call. Common parameters that "
        f"reduce response size on list-style tools:\n"
        f"  - Date range filters (e.g. 'since', 'until', 'created_after')\n"
        f"  - Entity scope (e.g. 'client_id', 'agent_id', 'status_id')\n"
        f"  - Pagination caps (e.g. 'page_size', 'max_pages', 'limit')\n"
        f"  - Field projection (e.g. 'fields=[\"id\", \"summary\"]')\n\n"
        f"Inspect the tool's input schema (visible in your tool catalog) "
        f"for exact parameter names. If you need broad results, fetch in "
        f"smaller batches and summarize between calls."
    )
    logger.warning(
        "MCP tool result truncated: tool=%s connection=%s size=%d cap=%d",
        tool_name,
        connection_id,
        size_bytes,
        _MAX_TOOL_RESULT_BYTES,
    )
    return {
        "content": [{"type": "text", "text": truncation_note}],
        "structured_content": {
            "_bifrost_truncated": True,
            "original_size_bytes": size_bytes,
            "cap_bytes": _MAX_TOOL_RESULT_BYTES,
            "tool_name": tool_name,
        },
        "is_error": True,
        "_resolution_path": envelope.get("_resolution_path"),
    }
