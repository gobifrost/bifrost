"""Automatic CodeMode policy at the shared runtime and chat-history boundary."""

import json
from types import SimpleNamespace

import httpx2
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from src.models.contracts.agents import ToolResult
from src.models.enums import MessageRole
from src.services.agent_executor import AgentExecutor
from src.services.agent_runtime.tool_policy import (
    CODE_MODE_AUDIT_METADATA_KEY,
    CODE_MODE_NESTED_TOOL_CALL_IDS_KEY,
    build_automatic_code_mode_capabilities,
)
from src.services.agent_runtime.model_factory import create_agent_model
from src.services.agent_runtime.toolset import BifrostToolset, MAX_MODEL_TOOL_RESULT_CHARS
from src.services.llm import LLMMessage, ToolDefinition
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient
from tests.unit.services.test_agent_executor_runtime import (
    _saved_message,
    caller as caller,
    configured_chat_model as configured_chat_model,
    conversation as conversation,
    executor as executor,
)


def _tool_returns(messages: list[ModelMessage], name: str) -> list[object]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]


@pytest.mark.asyncio
async def test_automatic_policy_uses_code_mode_for_a_non_delegated_toolset() -> None:
    """A direct Automatic runtime exposes business reads only through run_code."""
    payload = "x" * (MAX_MODEL_TOOL_RESULT_CHARS + 1)
    calls: list[int] = []
    requests: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        assert name == "read_chunk"
        assert tool_call_id
        index = arguments["index"]
        assert isinstance(index, int)
        calls.append(index)
        return '{"payload": "' + payload + '"}'

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        requests.append(messages)
        if len(requests) == 1:
            names = {tool.name for tool in info.function_tools}
            assert names == {"run_code"}
            assert info.instructions is not None
            assert "def read_chunk(*, index: int)" in info.instructions
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "chunks = [read_chunk(index=index) for index in range(3)]\n"
                                "combined = ''.join(chunk['payload'] for chunk in chunks)\n"
                                "{'combined_length': len(combined)}"
                            )
                        },
                        "outer",
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
                        name="read_chunk",
                        description="Read one large chunk.",
                        parameters={
                            "type": "object",
                            "properties": {"index": {"type": "integer"}},
                            "required": ["index"],
                        },
                    )
                ],
                execute,
            )
        ],
        capabilities=build_automatic_code_mode_capabilities(),
    )

    result = await runtime.run("Aggregate the chunks.")

    assert result.output == "complete"
    assert calls == [0, 1, 2]
    [outer_result] = _tool_returns(requests[1], "run_code")
    assert outer_result == {"combined_length": len(payload) * 3}


@pytest.mark.asyncio
async def test_automatic_deepseek_wire_exposes_native_discovery_and_code_controls() -> None:
    """The provider sees native discovery plus CodeMode, never a raw business schema."""
    bodies: list[dict[str, Any]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        assert name == "read_chunk"
        assert arguments == {"index": 0}
        assert tool_call_id
        return '{"payload":"evidence"}'

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        index = len(bodies) - 1
        message: dict[str, Any] = {"role": "assistant", "content": "complete"}
        if index == 0:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "outer",
                        "type": "function",
                        "function": {
                            "name": "run_code",
                            "arguments": json.dumps(
                                {
                                    "code": (
                                        "receipt = read_chunk(index=0)\n"
                                        "{'payload': receipt['payload']}"
                                    )
                                }
                            ),
                        },
                    }
                ],
            }
        return httpx2.Response(
            200,
            json={
                "id": f"completion-{index}",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek/deepseek-v4.1-flash",
                "provider": "Synthetic",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if index == 0 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with patch(
            "src.services.agent_runtime.model_factory.get_ai_retry_http_client",
            return_value=client,
        ):
            model = create_agent_model(
                LLMConfig(
                    provider="openai",
                    model="deepseek/deepseek-v4.1-flash",
                    endpoint="https://openrouter.ai/api/v1",
                    api_key="synthetic-never-sent-to-network",
                )
            )
            result = await Agent(
                model,
                toolsets=[
                    BifrostToolset(
                        [
                            ToolDefinition(
                                name="read_chunk",
                                description="Read one large chunk.",
                                parameters={
                                    "type": "object",
                                    "properties": {"index": {"type": "integer"}},
                                    "required": ["index"],
                                },
                            )
                        ],
                        execute,
                    )
                ],
                capabilities=build_automatic_code_mode_capabilities(),
            ).run("Read the compact evidence.")

    assert result.output == "complete"
    assert len(bodies) == 2
    visible_tools = [
        {tool["function"]["name"] for tool in body.get("tools", [])}
        for body in bodies
    ]
    assert visible_tools == [{"run_code"}, {"run_code"}]
    assert all("read_chunk" not in names for names in visible_tools)


@pytest.mark.asyncio
async def test_legacy_runtime_keeps_direct_tool_dispatch() -> None:
    """Without Automatic capabilities, the legacy loop never inserts run_code."""
    calls: list[str] = []
    requests: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        del arguments, tool_call_id
        calls.append(name)
        return '{"status": "open"}'

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        requests.append(messages)
        if len(requests) == 1:
            return ModelResponse(parts=[ToolCallPart("read_ticket", {}, "direct")])
        return ModelResponse(parts=[TextPart("complete")])

    runtime = Agent(
        FunctionModel(respond),
        toolsets=[
            BifrostToolset(
                [
                    ToolDefinition(
                        name="read_ticket",
                        description="Read one ticket.",
                        parameters={"type": "object", "properties": {}},
                    )
                ],
                execute,
            )
        ],
    )

    result = await runtime.run("Read the ticket.")

    assert result.output == "complete"
    assert calls == ["read_ticket"]
    assert not _tool_returns(requests[-1], "run_code")


@pytest.mark.asyncio
async def test_chat_automatic_code_mode_audits_three_nested_reads_and_projects_history(
    executor: AgentExecutor,
    conversation,
    caller,
) -> None:
    """Chat persists nested audit/progress while the model receives one compact receipt."""
    payload = "x" * (MAX_MODEL_TOOL_RESULT_CHARS + 1)
    requests: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        requests.append(messages)
        if len(requests) == 1:
            names = {tool.name for tool in info.function_tools}
            assert names == {"run_code"}
            assert info.instructions is not None
            assert "def read_chunk(*, index: int)" in info.instructions
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "chunks = [read_chunk(index=index) for index in range(3)]\n"
                                "combined = ''.join(chunk['payload'] for chunk in chunks)\n"
                                "{'combined_length': len(combined)}"
                            )
                        },
                        "outer",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    async def stream_respond(messages: list[ModelMessage], info: AgentInfo):
        response = respond(messages, info)
        [part] = response.parts
        if isinstance(part, TextPart):
            yield part.content
        else:
            assert isinstance(part, ToolCallPart)
            yield {
                0: DeltaToolCall(
                    name=part.tool_name,
                    json_args=json.dumps(part.args_as_dict()),
                    tool_call_id=part.tool_call_id,
                )
            }

    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Automatic Reader"
    agent.organization_id = uuid4()
    agent.delegated_agents = []
    agent.max_iterations = 8
    agent.max_token_budget = 50_000
    agent.llm_model = None
    agent.llm_max_tokens = None
    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._update_tool_call_message = AsyncMock()
    executor._record_ai_usage = AsyncMock()
    executor._get_agent_tools = AsyncMock(
        return_value=[
            ToolDefinition(
                name="read_chunk",
                description="Read one large chunk.",
                parameters={
                    "type": "object",
                    "properties": {"index": {"type": "integer"}},
                    "required": ["index"],
                },
            )
        ]
    )
    executor._build_message_history = AsyncMock(
        return_value=[
            LLMMessage(role="system", content="Use tools."),
            LLMMessage(role="user", content="Read all chunks."),
        ]
    )
    executor._execute_tool = AsyncMock(
        side_effect=lambda request, *_args, **_kwargs: ToolResult(
            tool_call_id=request.id,
            tool_name=request.name,
            result='{"payload":"' + payload + '"}',
        )
    )
    client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-model", api_key="test-key")
    )

    with (
        patch(
            "src.services.agent_executor.get_llm_client",
            new=AsyncMock(return_value=client),
        ),
        patch(
            "src.services.agent_runtime.model_factory.create_agent_model",
            return_value=FunctionModel(respond, stream_function=stream_respond),
        ),
        patch(
            "src.services.agent_executor.authorize_agent_execution",
            new=AsyncMock(return_value=(caller, agent)),
        ),
    ):
        chunks = [
            chunk
            async for chunk in executor.chat(
                agent,
                conversation,
                "Read all chunks.",
                stream=True,
                enable_routing=False,
                caller=caller,
                run_user_id=None,
                agent_execution_mode="automatic",
            )
        ]

    errors = [chunk.error for chunk in chunks if chunk.type == "error"]
    assert not errors, errors
    nested_calls = [
        chunk.tool_call
        for chunk in chunks
        if (
            chunk.type == "tool_call"
            and chunk.tool_call
            and chunk.tool_call.name == "read_chunk"
        )
    ]
    assert len(nested_calls) == 3
    assert [
        chunk.tool_progress.tool_call_id
        for chunk in chunks
        if (
            chunk.type == "tool_progress"
            and chunk.tool_progress
            and chunk.tool_progress.tool_call_id in {call.id for call in nested_calls}
        )
    ] == [call.id for call in nested_calls]
    assert executor._execute_tool.await_count == 3
    [outer_result] = _tool_returns(requests[1], "run_code")
    assert outer_result == {"combined_length": len(payload) * 3}
    outer_audits = [
        call.kwargs["tool_result"]
        for call in executor._update_tool_call_message.await_args_list
        if CODE_MODE_AUDIT_METADATA_KEY in call.kwargs["tool_result"]
    ]
    assert [
        len(audit[CODE_MODE_AUDIT_METADATA_KEY][CODE_MODE_NESTED_TOOL_CALL_IDS_KEY])
        for audit in outer_audits
    ] == [1, 2, 3, 3]
    outer_audit = outer_audits[-1]
    assert outer_audit[CODE_MODE_AUDIT_METADATA_KEY][
        CODE_MODE_NESTED_TOOL_CALL_IDS_KEY
    ] == [call.id for call in nested_calls]


@pytest.mark.asyncio
async def test_chat_history_replays_outer_code_result_and_keeps_ordinary_double_underscore_id(
) -> None:
    """Nested audit rows are excluded by explicit outer metadata, never ID shape."""
    raw_nested_result = "raw nested evidence " + "x" * (MAX_MODEL_TOOL_RESULT_CHARS + 1)
    outer_id = "outer"
    nested_id = "outer__1"
    ordinary_id = "ordinary__1"
    rows = [
        SimpleNamespace(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=None,
            sequence=1,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL_CALL,
            tool_call_id=outer_id,
            tool_name="run_code",
            tool_input={"code": "..."},
            tool_result={
                CODE_MODE_AUDIT_METADATA_KEY: {
                    CODE_MODE_NESTED_TOOL_CALL_IDS_KEY: [nested_id],
                },
                "result": {"combined_length": 96_003},
            },
            sequence=2,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL_CALL,
            tool_call_id=nested_id,
            tool_name="read_chunk",
            tool_input={"index": 0},
            tool_result={"payload": raw_nested_result},
            sequence=3,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL_CALL,
            tool_call_id=ordinary_id,
            tool_name="read_ordinary",
            tool_input={},
            tool_result={"status": "open"},
            sequence=4,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL,
            tool_call_id=outer_id,
            tool_name="run_code",
            content='{"combined_length":96003}',
            sequence=5,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL,
            tool_call_id=nested_id,
            tool_name="read_chunk",
            content=raw_nested_result,
            sequence=6,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL,
            tool_call_id=ordinary_id,
            tool_name="read_ordinary",
            content='{"status":"open"}',
            sequence=7,
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute.return_value = result
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    executor = AgentExecutor(MagicMock(return_value=context))
    executor._get_default_system_prompt = AsyncMock(return_value="Use tools.")

    history = await executor._build_message_history(
        None,
        SimpleNamespace(id=object()),
    )

    assistant = next(message for message in history if message.role == "assistant")
    assert [call.id for call in assistant.tool_calls or []] == [outer_id, ordinary_id]
    assert [message.tool_call_id for message in history if message.role == "tool"] == [
        outer_id,
        ordinary_id,
    ]
    assert raw_nested_result not in "".join(message.content or "" for message in history)


@pytest.mark.asyncio
async def test_chat_history_excludes_nested_receipt_when_outer_code_call_was_interrupted() -> None:
    """The pre-dispatch outer audit relation survives without an outer TOOL row."""
    raw_nested_result = "raw interrupted evidence " + "x" * (
        MAX_MODEL_TOOL_RESULT_CHARS + 1
    )
    rows = [
        SimpleNamespace(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=None,
            sequence=1,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL_CALL,
            tool_call_id="outer",
            tool_name="run_code",
            tool_input={"code": "..."},
            tool_result={
                CODE_MODE_AUDIT_METADATA_KEY: {
                    CODE_MODE_NESTED_TOOL_CALL_IDS_KEY: ["opaque-child"],
                }
            },
            sequence=2,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL_CALL,
            tool_call_id="opaque-child",
            tool_name="read_chunk",
            tool_input={"index": 0},
            tool_result={"payload": raw_nested_result},
            sequence=3,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL,
            tool_call_id="opaque-child",
            tool_name="read_chunk",
            content=raw_nested_result,
            sequence=4,
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute.return_value = result
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    executor = AgentExecutor(MagicMock(return_value=context))
    executor._get_default_system_prompt = AsyncMock(return_value="Use tools.")

    history = await executor._build_message_history(
        None,
        SimpleNamespace(id=object()),
    )

    assert [message.role for message in history] == ["system", "assistant"]
    assert raw_nested_result not in "".join(message.content or "" for message in history)
