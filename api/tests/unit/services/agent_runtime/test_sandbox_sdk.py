"""Catalog-private direct binding coverage for native CodeMode."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
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

from src.services.agent_runtime.code_mode import (
    build_catalog_private_code_mode_capabilities,
)
from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.sandbox_sdk import (
    BifrostSandboxSDK,
    MAX_SANDBOX_SDK_BUSINESS_CALLS,
    RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS,
    SANDBOX_SDK_TOOL_NAMES,
    _aliases_for,
    _decode_business_result,
    _direct_return_schema,
    is_sandbox_sdk_tool,
)
from src.services.agent_runtime.toolset import BifrostToolset, ToolEvent
from src.services.llm import ToolDefinition
from src.services.llm.base import bifrost_adapter_return_schema


def _sdk_capabilities() -> tuple[object, ...]:
    return (
        *build_catalog_private_code_mode_capabilities(
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
        ("plain receipt", "plain receipt"),
        ('{"id":"record-a"}', {"id": "record-a"}),
        ("Error: denied by source", "Error: denied by source"),
        ('{"error":"denied","result":"receipt"}', {"error": "denied", "result": "receipt"}),
    ],
)
def test_direct_bindings_decode_only_json_adapter_text(value: Any, expected: Any) -> None:
    assert _decode_business_result(value) == expected


@pytest.mark.parametrize(
    ("source_return_schema", "expected"),
    [
        ({"type": "object", "properties": {}}, {"type": "object", "properties": {}}),
        ({"type": "string"}, None),
        ({"oneOf": [{"type": "object"}, {"type": "string"}]}, None),
        ({"type": ["object", "null"]}, None),
        ({}, None),
    ],
)
def test_direct_bindings_preserve_only_unchanged_return_schemas(
    source_return_schema: dict[str, Any], expected: dict[str, Any] | None
) -> None:
    assert _direct_return_schema(source_return_schema) == expected


@pytest.mark.asyncio
async def test_sandbox_sdk_discovers_describes_and_chains_direct_live_bindings() -> None:
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
            assert names == {"run_code"}
            assert not SANDBOX_SDK_TOOL_NAMES & names
            assert not {"search_records", "read_record", "search_tools"} & names
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "catalog = bifrost_search(query='search records')\n"
                                "assert catalog['ok']\n"
                                "contract = bifrost_describe(name=catalog['data'][0]['name'])\n"
                                "assert contract['ok']\n"
                                "assert \"def search_records\" in contract['data']['python_signature']\n"
                                "assert \"TypedDict\" in \"\\n\".join(contract['data']['python_type_definitions'])\n"
                                "read_contract = bifrost_describe(name='read_record')\n"
                                "assert read_contract['data']['source_return_schema']['type'] == 'string'\n"
                                "assert \"-> Any\" in read_contract['data']['python_signature']\n"
                                "page = search_records(kind='target')\n"
                                "ids = [row['id'] for row in page['rows'] if row['kind'] == 'target']\n"
                                "details = [read_record(record_id=record_id) for record_id in ids]\n"
                                "{'target_ids': ids, 'verified': [item['verified'] for item in details]}"
                            )
                        },
                        "outer-code",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    result = await Agent(
        FunctionModel(model),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="search_records",
                        description="Find entities using the approved source.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string"},
                                "criteria": {
                                    "type": "object",
                                    "properties": {"priority": {"type": "integer"}},
                                    "required": ["priority"],
                                },
                            },
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
                        return_schema=bifrost_adapter_return_schema(),
                    ),
                ],
                execute,
                event_handler=record,
            ).defer_loading()
        ],
        capabilities=_sdk_capabilities(),
    ).run("Find and verify target records.")

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
async def test_sandbox_sdk_rejects_invalid_direct_arguments_before_dispatch() -> None:
    calls: list[str] = []
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del arguments, tool_call_id
        calls.append(name)
        return "unexpected"

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {"code": "invalid = read_record(record_id='')\n{'invalid': invalid}"},
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
                        name="read_record",
                        description="Read a record.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "record_id": {"type": "string", "minLength": 1}
                            },
                            "required": ["record_id"],
                            "additionalProperties": False,
                        },
                    )
                ],
                execute,
            ).defer_loading()
        ],
        capabilities=_sdk_capabilities(),
    ).run("Check direct binding validation.")

    assert calls == []
    [projection] = _tool_returns(seen[1], "run_code")
    assert "Arguments do not match" in projection["invalid"]


@pytest.mark.asyncio
async def test_sandbox_sdk_exposes_a_deferred_specialist_only_after_native_load() -> None:
    seen: list[list[ModelMessage]] = []

    def restricted_read(record_id: str) -> dict[str, str]:
        """Read restricted evidence."""
        return {"record_id": record_id}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        names = {tool.name for tool in info.function_tools}
        assert "run_code" in names
        assert "load_capability" in names
        assert "restricted_read" not in names
        if len(seen) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "catalog = bifrost_search(query='restricted')\n"
                                "{'available_before_load': catalog['data']}"
                            )
                        },
                    )
                ]
            )
        if len(seen) == 2:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "specialist"}, "load")]
            )
        if len(seen) == 3:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "catalog = bifrost_search(query='restricted')\n"
                                "assert catalog['data'][0]['name'] == 'restricted_read'\n"
                                "contract = bifrost_describe(name='restricted_read')\n"
                                "assert contract['data']['source_return_schema']['type'] == 'object'\n"
                                "assert \"async def restricted_read\" in contract['data']['python_signature']\n"
                                "assert \"-> Any\" not in contract['data']['python_signature']\n"
                                "await restricted_read(record_id='r-7')"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    result = await Agent(
        FunctionModel(model),
        capabilities=(
            *_sdk_capabilities(),
            Capability(id="specialist", tools=[restricted_read], defer_loading=True),
        ),
    ).run("Load and read specialist evidence.")

    assert result.output == "complete"
    assert _tool_returns(seen[1], "run_code") == [{"available_before_load": []}]
    assert _tool_returns(seen[3], "run_code") == [
        {"available_before_load": []},
        {"record_id": "r-7"},
    ]


@pytest.mark.asyncio
async def test_sandbox_sdk_refreshes_live_authority_before_direct_dispatch() -> None:
    available = True
    callbacks: list[str] = []
    seen: list[list[ModelMessage]] = []

    def is_live(_: Any, tool_def: Any) -> bool:
        return tool_def.name != "revoked_read" or available

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        nonlocal available
        del arguments, tool_call_id
        callbacks.append(name)
        if name == "first_read":
            available = False
            return json.dumps({"first": True})
        raise AssertionError("revoked tool dispatched")

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
                                "contract = bifrost_describe(name='revoked_read')\n"
                                "first = first_read()\n"
                                "revoked = revoked_read()\n"
                                "{'described': contract['ok'], 'first': first['first'], 'revoked': revoked}"
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
                    ToolDefinition("first_read", "Read first.", {"type": "object", "properties": {}}),
                    ToolDefinition("revoked_read", "Read while live.", {"type": "object", "properties": {}}),
                ],
                execute,
            ).filtered(is_live)
        ],
        capabilities=_sdk_capabilities(),
    ).run("Observe revocation.")

    assert callbacks == ["first_read"]
    assert _tool_returns(seen[1], "run_code") == [
        {
            "described": True,
            "first": True,
            "revoked": "Error: Bifrost callable 'revoked_read' is no longer available.",
        }
    ]


@pytest.mark.asyncio
async def test_sandbox_sdk_propagates_cancellation_and_caps_business_bindings() -> None:
    calls: list[int] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del tool_call_id
        if name == "cancel":
            raise AgentRunCancelled("Cancelled by user")
        calls.append(arguments["index"])
        return json.dumps({"index": arguments["index"]})

    requests = 0

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
                                "bifrost_search(query='count')\n"
                                "bifrost_describe(name='count')\n"
                                "values = [count(index=index) for index in range(17)]\n"
                                "{'successful': sum(isinstance(value, dict) for value in values), "
                                "'last': values[-1]}"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    seen: list[list[ModelMessage]] = []

    def recording_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return model(messages, info)

    await Agent(
        FunctionModel(recording_model),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        "count",
                        "Count one item.",
                        {
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
    ).run("Count through direct bindings.")

    assert calls == list(range(MAX_SANDBOX_SDK_BUSINESS_CALLS))
    assert _tool_returns(seen[1], "run_code") == [
        {
            "successful": MAX_SANDBOX_SDK_BUSINESS_CALLS,
            "last": "Error: Bifrost sandbox business call limit of 16 reached.",
        }
    ]


@pytest.mark.asyncio
async def test_sandbox_sdk_propagates_direct_binding_cancellation() -> None:
    effects: list[str] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del arguments, tool_call_id
        if name == "cancel":
            raise AgentRunCancelled("Cancelled by user")
        effects.append(name)
        return "unexpected"

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(
            parts=[ToolCallPart("run_code", {"code": "cancel()\neffect()"})]
        )

    runtime = Agent(
        FunctionModel(model),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition("cancel", "Cancel.", {"type": "object", "properties": {}}),
                    ToolDefinition("effect", "Effect.", {"type": "object", "properties": {}}),
                ],
                execute,
            ).defer_loading()
        ],
        capabilities=_sdk_capabilities(),
    )

    with pytest.raises(AgentRunCancelled, match="Cancelled by user"):
        await runtime.run("Cancel the direct binding.")
    assert effects == []


@pytest.mark.asyncio
async def test_sandbox_sdk_honors_trusted_stable_python_alias_metadata() -> None:
    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del name, arguments, tool_call_id
        return "ok"

    toolset = BifrostToolset(
        [
            ToolDefinition("bfa_first", "First duplicate.", {"type": "object", "properties": {}}),
            ToolDefinition("bfa_second", "Second duplicate.", {"type": "object", "properties": {}}),
        ],
        execute,
        tool_metadata={
            "bfa_first": {"bifrost_python_name": "owner_aaa_read_record"},
            "bfa_second": {"bifrost_python_name": "owner_bbb_read_record"},
        },
    )
    raw_tools = await toolset.get_tools(SimpleNamespace())

    assert _aliases_for(raw_tools) == {
        "bfa_first": "owner_aaa_read_record",
        "bfa_second": "owner_bbb_read_record",
    }


@pytest.mark.asyncio
async def test_sandbox_sdk_resolves_generated_alias_collisions_deterministically() -> None:
    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del name, arguments, tool_call_id
        return "ok"

    first_digest = hashlib.sha256(b"a").hexdigest()[:12]
    second_digest = hashlib.sha256(b"b").hexdigest()[:12]
    third_digest = hashlib.sha256(b"c").hexdigest()[:12]
    toolset = BifrostToolset(
        [
            ToolDefinition("a", "First.", {"type": "object", "properties": {}}),
            ToolDefinition("b", "Second.", {"type": "object", "properties": {}}),
            ToolDefinition("c", "Third.", {"type": "object", "properties": {}}),
        ],
        execute,
        tool_metadata={
            "a": {"bifrost_python_name": "read"},
            "b": {"bifrost_python_name": "read"},
            "c": {"bifrost_python_name": f"read_{second_digest}"},
        },
    )

    aliases = _aliases_for(await toolset.get_tools(SimpleNamespace()))

    assert aliases == {
        "a": f"read_{first_digest}",
        "b": f"read_{second_digest}",
        "c": f"read_{second_digest}_{third_digest}",
    }
