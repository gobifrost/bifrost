"""Sandbox SDK coverage for catalog-private native CodeMode dispatch."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from src.services.agent_runtime.code_mode import build_code_mode_capabilities
from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.sandbox_sdk import (
    BifrostSandboxSDK,
    MAX_SANDBOX_SDK_BUSINESS_CALLS,
    RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS,
    SANDBOX_SDK_TOOL_NAMES,
    _normalize_result,
    is_sandbox_sdk_tool,
)
from src.services.agent_runtime.toolset import BifrostToolset, ToolEvent
from src.services.llm import ToolDefinition


def _sdk_capabilities() -> tuple[object, ...]:
    return (
        *build_code_mode_capabilities(
            tools=is_sandbox_sdk_tool,
            max_tool_calls=RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS,
        ),
        BifrostSandboxSDK(),
    )


def _tool_returns(messages: list[ModelMessage], name: str) -> list[Any]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain receipt", {"ok": True, "data": "plain receipt", "error": None}),
        (
            '{"id":"record-a"}',
            {"ok": True, "data": {"id": "record-a"}, "error": None},
        ),
        (
            "Error: denied by source",
            {"ok": False, "data": "Error: denied by source", "error": "Error: denied by source"},
        ),
        (
            '{"error":"denied","result":"receipt"}',
            {
                "ok": False,
                "data": {"error": "denied", "result": "receipt"},
                "error": "denied",
            },
        ),
        (
            {"is_error": True, "result": "receipt"},
            {
                "ok": False,
                "data": {"is_error": True, "result": "receipt"},
                "error": {"is_error": True, "result": "receipt"},
            },
        ),
    ],
)
def test_sandbox_sdk_normalizes_source_transport_without_inventing_payloads(
    value: Any, expected: dict[str, Any]
) -> None:
    assert _normalize_result(value) == expected


@pytest.mark.asyncio
async def test_sandbox_sdk_discovers_describes_and_chains_live_business_calls() -> None:
    """One sandbox program keeps catalog and raw receipts outside model context."""
    raw_sentinels = {"raw-search-a", "raw-search-b", "raw-detail"}
    callbacks: list[tuple[str, dict[str, Any], str]] = []
    events: list[ToolEvent] = []
    seen: list[list[ModelMessage]] = []
    instructions: list[str | None] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        callbacks.append((name, arguments, tool_call_id))
        if name == "search_records":
            return json.dumps(
                {
                    "rows": [
                        {"id": "record-a", "kind": "target", "raw": "raw-search-a"},
                        {"id": "record-ignore", "kind": "other", "raw": "raw-search-b"},
                    ]
                }
            )
        assert name == "read_record"
        assert arguments == {"record_id": "record-a"}
        return json.dumps({"id": "record-a", "verified": True, "raw": "raw-detail"})

    async def record(event: ToolEvent) -> None:
        events.append(event)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        instructions.append(info.instructions)
        if len(seen) == 1:
            names = {tool.name for tool in info.function_tools}
            assert "run_code" in names
            assert not SANDBOX_SDK_TOOL_NAMES & names
            assert "search_tools" not in names
            assert not {"search_records", "read_record"} & names
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "catalog = bifrost_search(query='search records')\n"
                                "search_name = catalog['data'][0]['name']\n"
                                "contract = bifrost_describe(name=search_name)\n"
                                "assert contract['data']['input_schema']['required'] == ['kind']\n"
                                "page = bifrost_call(name=search_name, arguments={'kind': 'target'})\n"
                                "ids = [row['id'] for row in page['data']['rows'] if row['kind'] == 'target']\n"
                                "details = [bifrost_call(name='read_record', arguments={'record_id': record_id}) for record_id in ids]\n"
                                "{'target_ids': ids, 'verified': [item['data']['verified'] for item in details]}"
                            )
                        },
                        "outer-code",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(model),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="search_records",
                        description="Find entities using the approved source.",
                        parameters={
                            "type": "object",
                            "properties": {"kind": {"type": "string"}},
                            "required": ["kind"],
                            "additionalProperties": False,
                        },
                    ),
                    ToolDefinition(
                        name="read_record",
                        description="Read one approved entity.",
                        parameters={
                            "type": "object",
                            "properties": {"record_id": {"type": "string"}},
                            "required": ["record_id"],
                            "additionalProperties": False,
                        },
                    ),
                ],
                execute,
                event_handler=record,
            ).defer_loading()
        ],
        capabilities=_sdk_capabilities(),
    )

    result = await runtime.run("Find and verify target records.")

    assert result.output == "complete"
    assert [(name, arguments) for name, arguments, _ in callbacks] == [
        ("search_records", {"kind": "target"}),
        ("read_record", {"record_id": "record-a"}),
    ]
    callback_ids = [tool_call_id for _, _, tool_call_id in callbacks]
    assert all(callback_ids)
    assert len(set(callback_ids)) == len(callback_ids)
    assert "outer-code" not in callback_ids
    assert _tool_returns(seen[1], "run_code") == [
        {"target_ids": ["record-a"], "verified": [True]}
    ]
    assert all("bifrost_search" in (value or "") for value in instructions)
    assert all("search_records" not in (value or "") for value in instructions)
    assert all("read_record" not in (value or "") for value in instructions)
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
async def test_sandbox_sdk_denies_unknown_names_and_invalid_arguments_before_dispatch() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del tool_call_id
        calls.append((name, arguments))
        return "unexpected"

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "unknown = bifrost_call(name='missing', arguments={})\n"
                                "native = bifrost_call(name='load_capability', arguments={})\n"
                                "invalid = bifrost_call(name='read_record', arguments={})\n"
                                "{'unknown_ok': unknown['ok'], 'native_ok': native['ok'], "
                                "'invalid_ok': invalid['ok'], "
                                "'errors': [unknown['error'], native['error'], invalid['error']]}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(model),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="read_record",
                        description="Read a record.",
                        parameters={
                            "type": "object",
                            "properties": {"record_id": {"type": "string"}},
                            "required": ["record_id"],
                            "additionalProperties": False,
                        },
                    )
                ],
                execute,
            ).defer_loading()
        ],
        capabilities=_sdk_capabilities(),
    )

    await runtime.run("Check denied sandbox calls.")

    assert calls == []
    [projection] = _tool_returns(seen[1], "run_code")
    assert projection["unknown_ok"] is False
    assert projection["native_ok"] is False
    assert projection["invalid_ok"] is False
    assert "Unknown Bifrost tool" in projection["errors"][0]
    assert "Unknown Bifrost tool" in projection["errors"][1]
    assert "Arguments do not match" in projection["errors"][2]


@pytest.mark.asyncio
async def test_sandbox_sdk_keeps_loaded_capability_schema_out_of_the_model_catalog() -> None:
    """Native capability loading remains available without publishing its tool schema."""
    seen: list[list[ModelMessage]] = []
    instructions: list[str | None] = []

    def restricted_read(record_id: str) -> str:
        """PRIVATE_SCHEMA_SENTINEL: read one restricted record."""
        return record_id

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        instructions.append(info.instructions)
        names = {tool.name for tool in info.function_tools}
        assert "load_capability" in names
        assert "run_code" in names
        assert "search_tools" not in names
        assert "restricted_read" not in names
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "specialist"}, "load")]
            )
        return ModelResponse(parts=[TextPart("loaded")])

    result = await Agent(
        FunctionModel(model),
        capabilities=(
            *_sdk_capabilities(),
            Capability(id="specialist", tools=[restricted_read], defer_loading=True),
        ),
    ).run("Load the approved specialist.")

    assert result.output == "loaded"
    assert all("PRIVATE_SCHEMA_SENTINEL" not in str(messages) for messages in seen)
    assert all("PRIVATE_SCHEMA_SENTINEL" not in (value or "") for value in instructions)


@pytest.mark.asyncio
async def test_sandbox_sdk_refreshes_the_live_catalog_before_each_business_call() -> None:
    """A result from describe never grants authority after a live revocation."""
    target_available = True
    callbacks: list[str] = []
    seen: list[list[ModelMessage]] = []

    def is_live(_: Any, tool_def: Any) -> bool:
        return tool_def.name != "revoked_read" or target_available

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        nonlocal target_available
        del arguments, tool_call_id
        callbacks.append(name)
        if name == "first_read":
            target_available = False
            return json.dumps({"first": True})
        raise AssertionError("The revoked tool must not dispatch")

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "described = bifrost_describe(name='revoked_read')\n"
                                "first = bifrost_call(name='first_read', arguments={})\n"
                                "revoked = bifrost_call(name='revoked_read', arguments={})\n"
                                "{'described': described['ok'], 'first': first['ok'], "
                                "'revoked': revoked['ok'], 'error': revoked['error']}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    await Agent(
        FunctionModel(model),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="first_read",
                        description="Read one current record.",
                        parameters={"type": "object", "properties": {}},
                    ),
                    ToolDefinition(
                        name="revoked_read",
                        description="Read a record while authorization remains live.",
                        parameters={"type": "object", "properties": {}},
                    ),
                ],
                execute,
            ).filtered(is_live)
        ],
        capabilities=_sdk_capabilities(),
    ).run("Observe a revoked tool.")

    assert callbacks == ["first_read"]
    assert _tool_returns(seen[1], "run_code") == [
        {
            "described": True,
            "first": True,
            "revoked": False,
            "error": "Unknown Bifrost tool: revoked_read",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("native_toolset", [False, True])
async def test_sandbox_sdk_propagates_cancellation_without_running_later_callbacks(
    native_toolset: bool,
) -> None:
    effects: list[str] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del arguments, tool_call_id
        if name == "cancel":
            raise AgentRunCancelled("Cancelled by user")
        effects.append(name)
        return "unexpected"

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        assert len(messages) == 1, str(messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "run_code",
                    {
                        "code": (
                            "bifrost_call(name='cancel', arguments={})\n"
                            "bifrost_call(name='effect', arguments={})"
                        )
                    },
                )
            ]
        )

    async def cancel() -> str:
        return await execute("cancel", {}, "")

    async def effect() -> str:
        return await execute("effect", {}, "")

    toolset = (
        FunctionToolset([cancel, effect])
        if native_toolset
        else BifrostToolset(
            [
                ToolDefinition(
                    name="cancel",
                    description="Cancel the current run.",
                    parameters={"type": "object", "properties": {}},
                ),
                ToolDefinition(
                    name="effect",
                    description="Perform one effect.",
                    parameters={"type": "object", "properties": {}},
                ),
            ],
            execute,
        )
    )
    runtime = Agent(
        FunctionModel(model),
        toolsets=[toolset.defer_loading()],
        capabilities=_sdk_capabilities(),
    )

    with pytest.raises(AgentRunCancelled, match="Cancelled by user"):
        await runtime.run("Cancel the sandbox run.")

    assert effects == []


@pytest.mark.asyncio
async def test_sandbox_sdk_caps_real_business_callbacks_without_counting_discovery() -> None:
    calls: list[int] = []
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del name, tool_call_id
        calls.append(arguments["index"])
        return json.dumps({"index": arguments["index"]})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "catalog = bifrost_search(query='count')\n"
                                "bifrost_describe(name=catalog['data'][0]['name'])\n"
                                "results = [bifrost_call(name='count', arguments={'index': index}) for index in range(17)]\n"
                                "{'successful': sum(item['ok'] for item in results), 'last_error': results[-1]['error']}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(model),
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
        capabilities=_sdk_capabilities(),
    )

    await runtime.run("Count through the sandbox SDK.")

    assert calls == list(range(MAX_SANDBOX_SDK_BUSINESS_CALLS))
    assert _tool_returns(seen[1], "run_code") == [
        {
            "successful": MAX_SANDBOX_SDK_BUSINESS_CALLS,
            "last_error": "Error: Bifrost sandbox business call limit of 16 reached.",
        }
    ]


@pytest.mark.asyncio
async def test_sandbox_sdk_resets_the_business_cap_for_the_next_code_program() -> None:
    calls: list[int] = []
    requests = 0

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del name, tool_call_id
        calls.append(arguments["index"])
        return json.dumps({"index": arguments["index"]})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "results = [bifrost_call(name='count', arguments={'index': index}) "
                                "for index in range(16)]\n"
                                "sum(item['ok'] for item in results)"
                            )
                        },
                        "first-program",
                    )
                ]
            )
        if requests == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": "bifrost_call(name='count', arguments={'index': 99})['ok']"
                        },
                        "second-program",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    await Agent(
        FunctionModel(model),
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
        capabilities=_sdk_capabilities(),
    ).run("Run two independent code programs.")

    assert calls == [*range(MAX_SANDBOX_SDK_BUSINESS_CALLS), 99]
