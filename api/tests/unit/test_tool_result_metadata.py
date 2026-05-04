"""Unit tests for the ``ToolResult.error_type`` and ``metadata`` fields.

These fields were added in Phase 3 of the external-MCP-client feature so
the chat surface can render structured recovery UIs (e.g. an inline
reconnect button when an MCP tool returns ``needs_reauth``). They must
round-trip through Pydantic serialization and remain backward-compatible
(default to ``None``).
"""

from __future__ import annotations

from src.models.contracts.agents import ToolResult


def test_tool_result_defaults_have_no_error_type_or_metadata():
    """Backward compatibility: an existing caller that doesn't pass the
    new fields gets ``None`` for both."""
    result = ToolResult(
        tool_call_id="tc-1",
        tool_name="search_knowledge",
        result={"documents": []},
    )
    assert result.error_type is None
    assert result.metadata is None
    assert result.error is None


def test_tool_result_with_error_type_and_metadata_serializes():
    """The chat surface receives a JSON envelope containing the error_type
    and metadata so it can render an inline reconnect button without a
    second API roundtrip."""
    connection_id = "11111111-1111-1111-1111-111111111111"
    result = ToolResult(
        tool_call_id="tc-2",
        tool_name=f"mcp__{connection_id}__graph_search",
        result=None,
        error="User reauthentication required",
        duration_ms=42,
        error_type="needs_reauth",
        metadata={
            "reauth_url": f"/me/connections/{connection_id}/connect",
            "connection_id": connection_id,
            "tool_name": "graph_search",
        },
    )

    payload = result.model_dump()
    assert payload["error_type"] == "needs_reauth"
    assert payload["metadata"] == {
        "reauth_url": f"/me/connections/{connection_id}/connect",
        "connection_id": connection_id,
        "tool_name": "graph_search",
    }
    assert payload["error"] == "User reauthentication required"
    assert payload["result"] is None


def test_tool_result_round_trips_through_json():
    """JSON dump/load preserves the new fields verbatim."""
    original = ToolResult(
        tool_call_id="tc-3",
        tool_name="some_tool",
        result=None,
        error="boom",
        error_type="misconfig",
        metadata={"connection_id": "abc"},
    )

    raw = original.model_dump_json()
    restored = ToolResult.model_validate_json(raw)

    assert restored.error_type == "misconfig"
    assert restored.metadata == {"connection_id": "abc"}
    assert restored.error == "boom"


def test_tool_result_metadata_accepts_nested_structures():
    """``metadata`` is a free-form ``dict[str, Any]`` so we can carry
    arbitrarily nested payloads (the spec doesn't lock its shape)."""
    nested = {
        "reauth_url": "/connect",
        "details": {"scopes": ["read", "write"], "expires_in": 3600},
    }
    result = ToolResult(
        tool_call_id="tc-4",
        tool_name="x",
        result=None,
        error="…",
        error_type="needs_reauth",
        metadata=nested,
    )

    raw = result.model_dump_json()
    restored = ToolResult.model_validate_json(raw)
    assert restored.metadata == nested
