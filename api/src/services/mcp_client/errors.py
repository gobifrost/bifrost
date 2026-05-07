"""Structured error types for the external MCP client.

These errors are caught by the executor layer (Phase 3) and translated into
``ToolResult.error_type`` envelopes that the chat surface can render as
inline reconnect prompts (``needs_reauth``) or surface to logs as misconfig
warnings.
"""

from __future__ import annotations

from uuid import UUID


class NeedsReauthError(Exception):
    """The caller's per-user credential is missing or unrecoverable.

    Raised by ``auth_resolution.resolve_token`` when:

    - The caller has no per-user credential row for this connection AND no
      service-token fallback is available for the chat surface, or
    - A previously-stored per-user token has expired and refresh failed,
      AND no service-token fallback is available.

    Also raised by ``dispatch.invoke`` when the vendor returns 401/403 on a
    user token even after a forced refresh — at that point the token is
    invalidated and the user must re-grant consent.

    Carries a ``reauth_url`` (server-built; the chat surface opens it
    verbatim) and a ``connection_id`` so the surface can render the inline
    Connect button without needing to look up the connection.
    """

    def __init__(
        self,
        reauth_url: str,
        connection_id: UUID,
        tool_name: str | None = None,
        message: str | None = None,
    ) -> None:
        self.reauth_url = reauth_url
        self.connection_id = connection_id
        self.tool_name = tool_name
        super().__init__(
            message
            or (
                f"User reauthentication required for connection {connection_id}"
                + (f" (tool: {tool_name})" if tool_name else "")
            )
        )


class MisconfigError(Exception):
    """Resolution reached a state that should have been filtered upstream.

    Raised by ``auth_resolution.resolve_token`` on Path 5: an autonomous
    caller (``caller_user_id is None``) hit a connection whose
    ``available_to_autonomous`` flag is false. In normal operation this is
    impossible because ``resolve_agent_tools()`` filters the tool out at
    planning. Reaching this branch means the planner missed a case — the
    error is surfaced so the bug is visible and fixable rather than masked
    by a silent fallback.

    Also raised by ``catalog_sync.sync_catalog`` when the connection has no
    service token configured (catalog sync always uses the service token).
    """

    def __init__(self, connection_id: UUID, reason: str) -> None:
        self.connection_id = connection_id
        self.reason = reason
        super().__init__(f"MCP connection {connection_id} misconfigured: {reason}")


class ToolDispatchError(Exception):
    """Generic error surface for downstream MCP tool failures.

    Wraps non-auth failures from the remote MCP server (network errors,
    protocol violations, server-side tool errors) so the executor can
    distinguish them from ``NeedsReauthError`` (which has its own envelope)
    and from ``MisconfigError`` (which is always a Bifrost-side bug).
    """

    def __init__(
        self,
        message: str,
        connection_id: UUID | None = None,
        tool_name: str | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.tool_name = tool_name
        super().__init__(message)
