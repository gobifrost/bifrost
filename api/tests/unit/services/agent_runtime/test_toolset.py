"""Deferred tool boundaries remain distinguishable from failures."""

from types import SimpleNamespace

import pytest
from pydantic_ai import CallDeferred

from src.services.agent_runtime.toolset import BifrostToolset
from src.services.llm.base import ToolDefinition


async def test_deferred_tool_does_not_record_a_failed_result():
    events = []

    async def park(name, arguments, call_id):
        raise CallDeferred({"timer_id": "timer-1"})

    async def record(event):
        events.append(event)

    toolset = BifrostToolset(
        [ToolDefinition(name="sleep", description="Wait", parameters={"type": "object"})],
        park,
        event_handler=record,
    )
    with pytest.raises(CallDeferred):
        await toolset.call_tool("sleep", {}, SimpleNamespace(tool_call_id="call-1"), None)
    assert [(event.type, event.tool_call_id) for event in events] == [("tool_call", "call-1")]
