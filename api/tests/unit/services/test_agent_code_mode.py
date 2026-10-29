"""Native Pydantic Harness CodeMode spike over Bifrost's ordinary toolset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage, UsageLimits

from src.services.agent_runtime.code_mode import (
    CODE_MODE_INSTRUCTIONS,
    build_code_mode_capabilities,
)
from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.toolset import (
    BifrostToolset,
    MAX_MODEL_TOOL_RESULT_CHARS,
    ToolEvent,
)
from src.services.llm import ToolDefinition


@pytest.mark.asyncio
async def test_code_mode_cannot_read_an_unexposed_host_file(tmp_path: Path) -> None:
    sentinel = tmp_path / "host-only.txt"
    sentinel.write_text("host-only synthetic evidence")
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": (
                "try:\n"
                f"    open({str(sentinel)!r}).read()\n"
                "    host_read = True\n"
                "except Exception:\n"
                "    host_read = False\n"
                "{'host_read': host_read}"
            )})])
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(FunctionModel(respond), capabilities=build_code_mode_capabilities(tools=[]))
    await runtime.run("Check that host files are unavailable.")

    assert _tool_returns(seen[1], "run_code") == [{"host_read": False}]


def _tool_returns(messages: list[ModelMessage], name: str) -> list[Any]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]


def _retry_feedback(messages: list[ModelMessage], tool_name: str) -> list[RetryPromptPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart) and part.tool_name == tool_name
    ]


@pytest.mark.asyncio
async def test_code_mode_default_allows_three_model_corrections_before_one_effect() -> None:
    """Syntax failures return feedback; only the model's corrected code executes."""
    effects: list[str] = []
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        del tool_call_id
        assert name == "record"
        effects.append(arguments["value"])
        return {"recorded": arguments["value"]}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["record"]})])
        if 2 <= len(seen) <= 4:
            if len(seen) > 2:
                assert _retry_feedback(messages, "run_code")
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": "record(value=)"})])
        if len(seen) == 5:
            assert _retry_feedback(messages, "run_code")
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": "record(value='once')"})])
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="record",
                        description="Record one effect.",
                        parameters={
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    )
                ],
                execute,
            ).defer_loading()
        ],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["record"]),
        ),
    )

    result = await runtime.run(
        "Correct your code after syntax feedback.",
        usage_limits=UsageLimits(request_limit=6),
    )

    assert result.output == "complete"
    assert effects == ["once"]
    assert result.usage.requests == 6


@pytest.mark.asyncio
async def test_code_mode_model_correction_does_not_replay_completed_effects() -> None:
    """A retry asks the model for fresh code instead of re-executing a partial snippet."""
    effects: list[str] = []
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        del tool_call_id
        assert name == "record"
        effects.append(arguments["value"])
        return {"recorded": arguments["value"]}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["record"]})])
        if len(seen) == 2:
            return ModelResponse(
                parts=[ToolCallPart("run_code", {"code": "record(value='once')\n1 / 0"})]
            )
        if len(seen) == 3:
            assert _retry_feedback(messages, "run_code")
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": "{'complete': True}"})])
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="record",
                        description="Record one effect.",
                        parameters={
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    )
                ],
                execute,
            ).defer_loading()
        ],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["record"]),
        ),
    )

    result = await runtime.run(
        "Correct the code without repeating completed work.",
        usage_limits=UsageLimits(request_limit=4),
    )

    assert result.output == "complete"
    assert effects == ["once"]
    assert result.usage.requests == 4


@pytest.mark.asyncio
async def test_code_mode_default_stops_after_exhausted_corrections_with_no_effects() -> None:
    """The native retry cap and run-wide usage limit prevent an endless loop."""
    seen: list[list[ModelMessage]] = []
    usage = RunUsage()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        return ModelResponse(parts=[ToolCallPart("run_code", {"code": "("})])

    runtime = Agent(
        FunctionModel(respond),
        capabilities=build_code_mode_capabilities(tools=[]),
    )

    with pytest.raises(UnexpectedModelBehavior, match="max retries count of 3"):
        await runtime.run(
            "Keep submitting invalid syntax.",
            usage_limits=UsageLimits(request_limit=5),
            usage=usage,
        )

    assert len(seen) == 4
    assert usage.requests == 4 <= 5


@pytest.mark.asyncio
async def test_code_mode_keeps_large_nested_results_until_its_bounded_outer_return() -> None:
    """The sandbox can aggregate complete nested data without expanding model history."""
    chunk = "x" * (MAX_MODEL_TOOL_RESULT_CHARS + 1)
    calls: list[int] = []
    events: list[ToolEvent] = []
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        assert tool_call_id
        if name == "status":
            return {"payload": chunk}
        assert name == "read_chunk"
        calls.append(arguments["index"])
        return {"index": arguments["index"], "payload": chunk}

    async def record(event: ToolEvent) -> None:
        events.append(event)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart("search_tools", {"queries": ["chunk", "status"]})]
            )
        if len(seen) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "chunks = []\n"
                                "for index in range(3):\n"
                                "    chunks.append(read_chunk(index=index))\n"
                                "combined = ''.join(item['payload'] for item in chunks)\n"
                                "{'combined_length': len(combined), 'combined': combined}"
                            )
                        },
                    )
                ]
            )
        if len(seen) == 3:
            return ModelResponse(parts=[ToolCallPart("status", {})])
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="read_chunk",
                        description="Return one payload chunk.",
                        parameters={
                            "type": "object",
                            "properties": {"index": {"type": "integer"}},
                            "required": ["index"],
                        },
                    ),
                    ToolDefinition(
                        name="status",
                        description="Return a direct status payload.",
                        parameters={"type": "object", "properties": {}},
                    ),
                ],
                execute,
                event_handler=record,
            ).defer_loading()
        ],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["read_chunk"]),
        ),
    )

    result = await runtime.run("Aggregate the payloads with run_code.")

    assert result.output == "complete"
    assert calls == [0, 1, 2]
    assert [event.type for event in events] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
    ]
    [outer_result] = _tool_returns(seen[2], "run_code")
    assert isinstance(outer_result, str)
    assert f'"combined_length":{len(chunk) * 3}' in outer_result
    assert "[tool result truncated:" in outer_result
    [direct_result] = _tool_returns(seen[3], "status")
    assert isinstance(direct_result, str)
    assert "[tool result truncated:" in direct_result


@pytest.mark.asyncio
async def test_code_mode_guides_one_call_to_filter_and_chain_private_results() -> None:
    """One code call can retain raw reads privately and return a compact projection."""
    raw_sentinels = {"raw-search-a", "raw-search-b", "raw-detail-a", "raw-detail-b"}
    callbacks: list[tuple[str, dict[str, Any]]] = []
    events: list[ToolEvent] = []
    seen: list[list[ModelMessage]] = []
    instructions: list[str | None] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        assert tool_call_id
        callbacks.append((name, arguments))
        if name == "search_records":
            return json.dumps({
                "rows": [
                    {"id": "record-a", "kind": "target", "raw": "raw-search-a"},
                    {"id": "record-ignore", "kind": "other", "raw": "raw-search-b"},
                    {"id": "record-b", "kind": "target", "raw": "raw-detail-a"},
                ],
                "next_page": None,
            })
        assert name == "read_record"
        return json.dumps({
            "id": arguments["record_id"],
            "verified": arguments["record_id"] == "record-a",
            "raw": "raw-detail-b",
        })

    async def record(event: ToolEvent) -> None:
        events.append(event)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        instructions.append(info.instructions)
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart("search_tools", {"queries": ["search record", "read record"]})]
            )
        if len(seen) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "page = search_records(page_size=100)\n"
                                "targets = [row for row in page['rows'] if row['kind'] == 'target']\n"
                                "details = [read_record(record_id=row['id']) for row in targets]\n"
                                "{'count': len(details), 'target_ids': [detail['id'] for detail in details], "
                                "'verification': [detail['verified'] for detail in details]}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="search_records",
                        description="Search structured records.",
                        parameters={
                            "type": "object",
                            "properties": {"page_size": {"type": "integer"}},
                            "required": ["page_size"],
                        },
                    ),
                    ToolDefinition(
                        name="read_record",
                        description="Read one structured record.",
                        parameters={
                            "type": "object",
                            "properties": {"record_id": {"type": "string"}},
                            "required": ["record_id"],
                        },
                    ),
                ],
                execute,
                event_handler=record,
            ).defer_loading()
        ],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["search_records", "read_record"]),
        ),
    )

    result = await runtime.run("Find and verify target records.")

    assert result.output == "complete"
    assert callbacks == [
        ("search_records", {"page_size": 100}),
        ("read_record", {"record_id": "record-a"}),
        ("read_record", {"record_id": "record-b"}),
    ]
    assert all(CODE_MODE_INSTRUCTIONS.strip() in (value or "") for value in instructions), instructions
    assert _tool_returns(seen[2], "run_code") == [
        {"count": 2, "target_ids": ["record-a", "record-b"], "verification": [True, False]}
    ]
    model_visible = [
        part.content
        for messages in seen
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert all(sentinel not in str(content) for sentinel in raw_sentinels for content in model_visible)
    audit_results = [event.result for event in events if event.type == "tool_result"]
    assert all(any(sentinel in str(result) for result in audit_results) for sentinel in raw_sentinels)


@pytest.mark.asyncio
async def test_code_mode_schema_and_dispatch_denials_do_not_run_effects() -> None:
    """Live schema validation and denied dispatch stay inside the existing lifecycle."""
    effects: list[str] = []
    events: list[ToolEvent] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        del arguments, tool_call_id
        if name == "denied_action":
            raise PermissionError("not authorized")
        effects.append(name)
        return {"effect": name}

    async def record(event: ToolEvent) -> None:
        events.append(event)

    definitions = [
        ToolDefinition(
            name="guarded_action",
            description="A schema-guarded action.",
            parameters={
                "type": "object",
                "properties": {
                    "allowed": {"type": "string", "pattern": "^approved$"}
                },
                "required": ["allowed"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="denied_action",
            description="An action which may be denied.",
            parameters={"type": "object", "properties": {}},
        ),
    ]

    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["action"]})])
        if len(seen) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "invalid = guarded_action(allowed='denied')\n"
                                "denied = denied_action()\n"
                                "{'invalid': invalid, 'denied': denied}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[BifrostToolset(definitions, execute, event_handler=record).defer_loading()],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["guarded_action", "denied_action"]),
        ),
    )

    result = await runtime.run("Check both guarded actions with run_code.")

    assert result.output == "complete"
    assert effects == []
    assert [(event.type, event.tool_name) for event in events] == [
        ("tool_call", "guarded_action"),
        ("tool_error", "guarded_action"),
        ("tool_call", "denied_action"),
        ("tool_error", "denied_action"),
    ]


@pytest.mark.asyncio
async def test_code_mode_enforces_nested_call_limit_sequentially() -> None:
    """The native nested-call budget refuses a third effect before dispatching it."""
    calls: list[int] = []
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        del name, tool_call_id
        calls.append(arguments["index"])
        return arguments["index"]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["count"]})])
        if len(seen) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "values = []\n"
                                "for index in range(3):\n"
                                "    try:\n"
                                "        values.append(count(index=index))\n"
                                "    except Exception:\n"
                                "        pass\n"
                                "{'values': values}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="count",
                        description="Count one item.",
                        parameters={
                            "type": "object",
                            "properties": {"index": {"type": "integer"}},
                            "required": ["index"],
                        },
                    )
                ],
                execute,
            ).defer_loading()
        ],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["count"], max_tool_calls=2),
        ),
    )

    result = await runtime.run("Use run_code to count three items.")

    assert result.output == "complete"
    assert calls == [0, 1]


@pytest.mark.asyncio
async def test_code_mode_cancellation_blocks_later_nested_effects() -> None:
    """Cancellation remains terminal even when generated code handles its local error."""
    effects: list[str] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        del arguments, tool_call_id
        if name == "cancel":
            raise AgentRunCancelled("Cancelled by user during tool execution")
        effects.append(name)
        return {"effect": name}

    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart("search_tools", {"queries": ["cancel", "effect"]})]
            )
        if len(seen) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "try:\n"
                                "    cancel()\n"
                                "except Exception:\n"
                                "    pass\n"
                                "try:\n"
                                "    effect()\n"
                                "except Exception:\n"
                                "    pass\n"
                                "{'complete': True}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("unexpected completion")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="cancel",
                        description="Request cancellation.",
                        parameters={"type": "object", "properties": {}},
                    ),
                    ToolDefinition(
                        name="effect",
                        description="Perform an effect.",
                        parameters={"type": "object", "properties": {}},
                    ),
                ],
                execute,
            ).defer_loading()
        ],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["cancel", "effect"]),
        ),
    )

    with pytest.raises(AgentRunCancelled, match="Cancelled by user"):
        await runtime.run("Cancel the run.")

    assert effects == []
