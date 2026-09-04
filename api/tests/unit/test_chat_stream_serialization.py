"""Regression coverage for contract-safe chat stream serialization."""

import pytest

from src.core.pubsub import serialize_chat_stream_chunk
from src.models.contracts.agents import (
    AgentSwitch,
    ChatStreamChunk,
    ContextWarning,
    ToolCall,
    ToolProgress,
    ToolProgressLog,
    ToolResult,
)
from src.models.contracts.artifacts import ArtifactRef


CHUNKS = [
    ChatStreamChunk(type="message_start", message_id="message-1"),
    ChatStreamChunk(type="delta", content="Hello"),
    ChatStreamChunk(
        type="assistant_message_end",
        message_id="message-1",
        stop_reason="end_turn",
    ),
    ChatStreamChunk(
        type="tool_call",
        tool_call=ToolCall(id="call-1", name="lookup", arguments={"id": 1}),
    ),
    ChatStreamChunk(
        type="tool_progress",
        tool_progress=ToolProgress(
            tool_call_id="call-1",
            status="running",
            log=ToolProgressLog(level="info", message="Working"),
        ),
    ),
    ChatStreamChunk(
        type="tool_result",
        tool_result=ToolResult(
            tool_call_id="call-1",
            tool_name="lookup",
            result={"found": True},
        ),
    ),
    ChatStreamChunk(
        type="tool_result",
        tool_result=ToolResult(
            tool_call_id="call-2",
            tool_name="update",
            result=None,
            duration_ms=10,
        ),
    ),
    ChatStreamChunk(
        type="artifact_started",
        artifact=ArtifactRef(
            id="attachment-1",
            filename="Welcome Page.html",
            content_type="text/html",
            size_bytes=1024,
        ),
    ),
    ChatStreamChunk(
        type="artifact_ready",
        artifact=ArtifactRef(
            id="attachment-1",
            filename="Welcome Page.html",
            content_type="text/html",
            size_bytes=1024,
        ),
    ),
    ChatStreamChunk(type="artifact_failed", error="Artifact generation failed"),
    ChatStreamChunk(
        type="agent_switch",
        agent_switch=AgentSwitch(
            agent_id="agent-2",
            agent_name="Specialist",
            reason="routed",
        ),
    ),
    ChatStreamChunk(
        type="context_warning",
        context_warning=ContextWarning(
            current_tokens=900,
            max_tokens=1000,
            action="warning",
            message="Context is nearly full",
        ),
    ),
    ChatStreamChunk(type="run_status", run_status="running"),
    ChatStreamChunk(type="title_update", title="Updated title"),
    ChatStreamChunk(type="done", run_status="completed", duration_ms=100),
    ChatStreamChunk(type="cancelled", run_status="cancelled"),
    ChatStreamChunk(type="error", run_status="failed", error="Internal failure"),
]


@pytest.mark.parametrize("chunk", CHUNKS, ids=lambda chunk: chunk.type)
def test_chat_stream_chunk_round_trips_through_wire_payload(
    chunk: ChatStreamChunk,
) -> None:
    payload = serialize_chat_stream_chunk(chunk)

    assert ChatStreamChunk.model_validate(payload) == chunk


def test_successful_null_tool_result_remains_present_on_wire() -> None:
    chunk = ChatStreamChunk(
        type="tool_result",
        tool_result=ToolResult(
            tool_call_id="call-1",
            tool_name="enable",
            result=None,
            duration_ms=10,
        ),
    )

    payload = serialize_chat_stream_chunk(chunk)

    assert payload["tool_result"]["result"] is None


def test_explicit_top_level_null_remains_present_on_wire() -> None:
    payload = serialize_chat_stream_chunk(
        ChatStreamChunk(type="done", content=None, run_status="completed")
    )

    assert "content" in payload
    assert payload["content"] is None
    assert "tool_result" not in payload


def test_artifact_chunk_dumps_portable_reference() -> None:
    chunk = CHUNKS[8]

    payload = serialize_chat_stream_chunk(chunk)

    assert payload["artifact"] == {
        "type": "bifrost_artifact",
        "id": "attachment-1",
        "filename": "Welcome Page.html",
        "content_type": "text/html",
        "size_bytes": 1024,
    }
