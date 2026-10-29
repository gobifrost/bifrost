"""Native CodeMode discovery, dispatch, and transport contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai_harness.code_mode import CodeMode

from src.services.agent_runtime.code_mode import build_code_mode_capabilities
from src.services.agent_runtime.tool_policy import (
    build_automatic_code_mode_capabilities,
    build_bifrost_tool_search,
)
from src.services.agent_runtime.toolset import BifrostToolset, ToolEvent, code_mode_safe_name_map
from src.services.llm import ToolDefinition
from src.services.llm.base import bifrost_adapter_return_schema


def _tool_returns(messages: list[object], name: str) -> list[Any]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]


@pytest.mark.asyncio
async def test_native_tool_search_reveals_ranked_tools_to_code_mode() -> None:
    """Search uses the shared policy, then upstream CodeMode owns the catalog and dispatch."""
    calls: list[str] = []
    events: list[ToolEvent] = []
    seen: list[list[object]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        assert tool_call_id
        calls.append(name)
        return json.dumps({"record_id": arguments["record_id"], "status": "active"})

    async def record(event: ToolEvent) -> None:
        events.append(event)

    def model(messages: list[object], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        names = {tool.name for tool in info.function_tools}
        if len(seen) == 1:
            assert names == {"run_code", "search_tools"}
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["read record"]}, "search")])
        if len(seen) == 2:
            assert names == {"run_code", "search_tools"}
            assert info.instructions is not None
            assert "def read_record(*, record_id: str) -> Any:" in info.instructions
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": "record = read_record(record_id='r-7')\nrecord['status']"}, "code")])
        return ModelResponse(parts=[TextPart("complete")])

    toolset = BifrostToolset(
        [
            ToolDefinition(
                "read_record",
                "Read one active record.",
                {"type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"]},
                return_schema=bifrost_adapter_return_schema(),
            )
        ],
        execute,
        event_handler=record,
    ).defer_loading()

    result = await Agent(
        FunctionModel(model),
        toolsets=[toolset],
        capabilities=(build_bifrost_tool_search(), *build_code_mode_capabilities()),
    ).run("Read the record.")

    assert result.output == "complete"
    assert calls == ["read_record"]
    assert _tool_returns(seen[2], "run_code") == ["active"]
    assert [event.result for event in events if event.type == "tool_result"] == [
        '{"record_id": "r-7", "status": "active"}'
    ]


@pytest.mark.asyncio
async def test_code_mode_dispatches_every_colliding_and_reserved_tool_name() -> None:
    """Native renamed toolsets preserve every Bifrost source callable."""
    mcp_prefix = "mcp__00000000-0000-0000-0000-000000000007__" + "remote_" * 12
    source_names = ("a-b", "a_b", "a_b_d44362d67d", "class", "run_code", mcp_prefix + "first", mcp_prefix + "second")
    name_map = code_mode_safe_name_map(source_names)
    presented = {
        name: next((alias for alias, original in name_map.items() if original == name), name)
        for name in source_names
    }
    assert all(alias.isidentifier() and len(alias) <= 64 for alias in presented.values())
    assert len(set(presented.values())) == len(source_names)
    calls: list[str] = []

    async def execute(name: str, _arguments: dict[str, Any], _tool_call_id: str) -> str:
        calls.append(name)
        return json.dumps(name)

    requests = 0

    def model(messages: list[object], info: AgentInfo) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            assert {tool.name for tool in info.function_tools} == {"run_code"}
            assert all(alias in (info.instructions or "") for alias in presented.values())
            code = "[" + ", ".join(f"{alias}()" for alias in presented.values()) + "]"
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, "code")])
        assert _tool_returns(messages, "run_code") == [list(source_names)]
        return ModelResponse(parts=[TextPart("complete")])

    result = await Agent(
        FunctionModel(model),
        toolsets=[
            BifrostToolset(
                [ToolDefinition(name, "Return its source name.", {"type": "object", "properties": {}}) for name in source_names],
                execute,
            ).renamed(name_map)
        ],
        capabilities=build_code_mode_capabilities(),
    ).run("Call every tool.")

    assert result.output == "complete"
    assert calls == list(source_names)


@pytest.mark.asyncio
async def test_native_provider_search_serializes_bounded_mcp_names() -> None:
    source = "mcp__00000000-0000-0000-0000-000000000007__" + "read_remote_record_" * 5
    name_map = code_mode_safe_name_map((source,))
    [alias] = name_map
    requests: list[dict[str, Any]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        message = {
            "id": "msg_native_search",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "complete"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 4, "output_tokens": 1},
        }
        if body.get("stream"):
            events = [
                ("message_start", {"message": {**message, "content": [], "stop_reason": None}}),
                ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "complete"}}),
                ("content_block_stop", {"index": 0}),
                ("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}),
                ("message_stop", {}),
            ]
            stream = "".join(
                f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n"
                for kind, payload in events
            )
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=stream)
        return httpx2.Response(200, json=message)

    async def execute(*_args: Any) -> None:
        raise AssertionError("A deferred business tool must not run.")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = AnthropicModel(
            "claude-sonnet-4-6",
            provider=AnthropicProvider(api_key="test-key", http_client=client),
        )
        result = await Agent(
            model,
            toolsets=[BifrostToolset(
                [ToolDefinition(source, "Read remote records.", {"type": "object"})],
                execute,
            ).defer_loading().renamed(name_map)],
            capabilities=build_automatic_code_mode_capabilities(),
        ).run("Say complete without using tools.")

    assert result.output == "complete"
    [body] = requests
    tools = {tool["name"]: tool for tool in body["tools"]}
    assert alias in tools
    assert tools[alias]["defer_loading"] is True
    assert source not in tools
    assert all(len(name) <= 64 for name in tools)


@pytest.mark.asyncio
async def test_code_mode_documents_only_structured_payload_not_a_universal_envelope() -> None:
    """A JSON-or-text adapter has an honest Any return and keeps payload field docs."""
    payload = {
        "type": "object",
        "properties": {"record_id": {"type": "string"}},
        "required": ["record_id"],
    }
    toolset = BifrostToolset(
        [
            ToolDefinition(
                "read_record",
                "Read one remote record.",
                {"type": "object", "properties": {}},
                return_schema=bifrost_adapter_return_schema(),
                structured_output_schema=payload,
            )
        ],
        executor=lambda *_: None,
    )

    [tool] = (await toolset.get_tools(SimpleNamespace(capabilities={"code": CodeMode()}))).values()

    assert tool.tool_def.return_schema is None
    assert "structured_content payload schema" in (tool.tool_def.description or "")
    assert '"record_id"' in (tool.tool_def.description or "")


@pytest.mark.asyncio
async def test_code_mode_preserves_only_explicit_non_string_return_schemas() -> None:
    """Decoded JSON keeps a concrete object schema, never a string or union claim."""
    object_schema = {"type": "object", "properties": {"record_id": {"type": "string"}}}
    union_schema = {"anyOf": [{"type": "object"}, {"type": "string"}]}
    toolset = BifrostToolset(
        [
            ToolDefinition("object_result", "Return a decoded object.", {"type": "object"}, object_schema),
            ToolDefinition("union_result", "Return decoded JSON or text.", {"type": "object"}, union_schema),
        ],
        executor=lambda *_: None,
    )

    tools = await toolset.get_tools(SimpleNamespace(capabilities={"code": CodeMode()}))

    assert tools["object_result"].tool_def.return_schema == object_schema
    assert tools["object_result"].tool_def.include_return_schema is True
    assert tools["union_result"].tool_def.return_schema is None
    assert tools["union_result"].tool_def.include_return_schema is False
