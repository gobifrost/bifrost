"""CodeMode adapts transport without rewriting arbitrary business results."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.services.agent_runtime.toolset import (
    BifrostToolset,
    ToolEvent,
    unbounded_model_tool_results,
)
from src.services.llm import ToolDefinition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,expected",
    [
        (
            json.dumps({
                "content": [{"type": "text", "text": "record found"}],
                "structured_content": {"records": [{"id": "r-7"}]},
                "is_error": False,
                "metadata": {"cursor": "next-page"},
            }),
            {
                "content": [{"type": "text", "text": "record found"}],
                "structured_content": {"records": [{"id": "r-7"}]},
                "is_error": False,
                "metadata": {"cursor": "next-page"},
            },
        ),
        (
            '{"is_error":true,"structured_content":null,"error":{"code":"Denied"}}',
            {"is_error": True, "structured_content": None, "error": {"code": "Denied"}},
        ),
        ('[{"id":1},{"id":2}]', [{"id": 1}, {"id": 2}]),
        ("null", None),
        ("Window closes Friday.", "Window closes Friday."),
        ("Error: access denied", "Error: access denied"),
    ],
)
async def test_nested_transport_preserves_payload_and_raw_audit(source: str, expected: Any) -> None:
    events: list[ToolEvent] = []

    async def execute(_name: str, _args: dict[str, Any], _call_id: str) -> str:
        return source

    async def record(event: ToolEvent) -> None:
        events.append(event)

    toolset = BifrostToolset(
        [ToolDefinition("read_record", "Read a record.", {"type": "object"})],
        execute,
        event_handler=record,
    )
    ctx = SimpleNamespace(tool_call_id="read", capabilities={})
    tool = (await toolset.get_tools(ctx))["read_record"]

    with unbounded_model_tool_results():
        assert await toolset.call_tool("read_record", {}, ctx, tool) == expected
    assert await toolset.call_tool("read_record", {}, ctx, tool) == source
    assert [event.result for event in events if event.type == "tool_result"] == [source, source]
