"""Durable chat history coverage for Automatic native CodeMode."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.principal import UserPrincipal
from src.models.contracts.agents import ToolResult
from src.models.contracts.artifacts import ModelCapabilities
from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation, Message
from src.services.agent_executor import AgentExecutor
from src.services.agent_runtime.tool_policy import (
    CODE_MODE_AUDIT_METADATA_KEY,
    CODE_MODE_NESTED_TOOL_CALL_IDS_KEY,
)
from src.services.authorization.enforce import load_caller
from src.services.llm import ToolDefinition
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient


pytestmark = pytest.mark.asyncio


def _create_agent(e2e_client, platform_admin, *, name: str) -> dict[str, Any]:
    response = e2e_client.post(
        "/api/agents",
        headers=platform_admin.headers,
        json={
            "name": name,
            "description": "Durable Automatic CodeMode chat coverage",
            "system_prompt": "Use approved tools to answer the request.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _load_chat_state(
    async_session_factory,
    conversation_id: UUID,
    user,
) -> tuple[Agent, Conversation, Any]:
    async with async_session_factory() as session:
        conversation = (
            await session.execute(
                select(Conversation)
                .options(
                    selectinload(Conversation.agent).selectinload(Agent.tools),
                    selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
                )
                .where(Conversation.id == conversation_id)
            )
        ).scalar_one()
        assert conversation.agent is not None
        assert user.user_id is not None
        caller = await load_caller(
            session,
            UserPrincipal(
                user_id=user.user_id,
                email=user.email,
                name=user.name,
                organization_id=user.organization_id,
                is_superuser=user.is_superuser,
            ),
        )
        return conversation.agent, conversation, caller


def _tool_parts(messages: list[ModelMessage]) -> list[ToolCallPart | ToolReturnPart]:
    return [
        part
        for message in messages
        for part in message.parts
        if isinstance(part, (ToolCallPart, ToolReturnPart))
    ]


async def test_automatic_chat_persists_nested_code_mode_audit_and_replays_only_outer_receipt(
    e2e_client,
    platform_admin,
    async_session_factory,
):
    """Reloaded chat history retains full child audit but never replays child data."""
    payload = "x" * 32_001
    child_receipt = json.dumps({"payload": payload})
    total_length = len(payload) * 3
    model_requests: list[list[ModelMessage]] = []
    continuation_messages: list[ModelMessage] | None = None
    agent_data = _create_agent(
        e2e_client,
        platform_admin,
        name=f"Automatic CodeMode Chat {uuid4().hex[:8]}",
    )
    conversation_response = e2e_client.post(
        "/api/chat/conversations",
        headers=platform_admin.headers,
        json={
            "agent_id": agent_data["id"],
            "channel": "chat",
            "title": "Automatic CodeMode durable history",
        },
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = UUID(conversation_response.json()["id"])

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal continuation_messages
        model_requests.append(messages)
        request_number = len(model_requests)
        if request_number == 1:
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
        if request_number == 2:
            return ModelResponse(parts=[TextPart("First turn complete.")])
        assert request_number == 3
        continuation_messages = messages
        return ModelResponse(parts=[TextPart("Continuation complete.")])

    async def stream_respond(messages: list[ModelMessage], info: AgentInfo):
        response = respond(messages, info)
        [part] = response.parts
        if isinstance(part, TextPart):
            yield part.content
            return
        assert isinstance(part, ToolCallPart)
        yield {
            0: DeltaToolCall(
                name=part.tool_name,
                json_args=json.dumps(part.args_as_dict()),
                tool_call_id=part.tool_call_id,
            )
        }

    async def business_execute(
        _executor: AgentExecutor,
        tool_call,
        *_args,
        **_kwargs,
    ) -> ToolResult:
        assert tool_call.name == "read_chunk"
        return ToolResult(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            result=child_receipt,
        )

    llm_client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-code-mode-chat", api_key="test-key")
    )
    profile = MagicMock(id=uuid4(), name="Automatic CodeMode chat profile")
    capabilities = ModelCapabilities(tool_calling=True, source="manual")
    tool_definition = ToolDefinition(
        name="read_chunk",
        description="Read one large evidence chunk.",
        parameters={
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
    )

    try:
        with (
            patch(
                "src.services.agent_executor.get_llm_client",
                new=AsyncMock(return_value=llm_client),
            ),
            patch(
                "src.services.agent_executor.AIModelService.resolve_chat_profile",
                new=AsyncMock(
                    return_value=(
                        profile,
                        LLMConfig(
                            provider="openai",
                            model="test-code-mode-chat",
                            api_key="test-key",
                        ),
                        capabilities,
                    )
                ),
            ),
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                side_effect=lambda *_args, **_kwargs: FunctionModel(
                    respond,
                    stream_function=stream_respond,
                ),
            ),
            patch.object(
                AgentExecutor,
                "_get_agent_tools",
                new=AsyncMock(return_value=[tool_definition]),
            ),
            patch.object(AgentExecutor, "_execute_tool", new=business_execute),
        ):
            first_agent, first_conversation, first_caller = await _load_chat_state(
                async_session_factory,
                conversation_id,
                platform_admin,
            )
            first_chunks = [
                chunk
                async for chunk in AgentExecutor(async_session_factory).chat(
                    agent=first_agent,
                    conversation=first_conversation,
                    user_message="Read all evidence chunks.",
                    stream=True,
                    enable_routing=False,
                    caller=first_caller,
                    run_user_id=platform_admin.user_id,
                    agent_execution_mode="automatic",
                )
            ]
            first_errors = [chunk.error for chunk in first_chunks if chunk.type == "error"]
            assert not first_errors, first_errors

            # A separate session and executor exercise persisted reload rather
            # than a retained in-memory history from the first chat turn.
            second_agent, second_conversation, second_caller = await _load_chat_state(
                async_session_factory,
                conversation_id,
                platform_admin,
            )
            second_chunks = [
                chunk
                async for chunk in AgentExecutor(async_session_factory).chat(
                    agent=second_agent,
                    conversation=second_conversation,
                    user_message="Continue with the compact result.",
                    stream=True,
                    enable_routing=False,
                    caller=second_caller,
                    run_user_id=platform_admin.user_id,
                    agent_execution_mode="automatic",
                )
            ]
            second_errors = [chunk.error for chunk in second_chunks if chunk.type == "error"]
            assert not second_errors, second_errors

        assert len(model_requests) == 3
        assert continuation_messages is not None
        continuation_parts = _tool_parts(continuation_messages)
        continuation_ids = {
            part.tool_call_id
            for part in continuation_parts
            if part.tool_call_id is not None
        }
        continuation_names = {part.tool_name for part in continuation_parts}
        continuation_receipts = [
            part.content
            for part in continuation_parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"
        ]
        assert "outer" in continuation_ids
        assert "read_chunk" not in continuation_names
        assert any(
            "combined_length" in str(receipt) and str(total_length) in str(receipt)
            for receipt in continuation_receipts
        )
        assert payload.encode() not in ModelMessagesTypeAdapter.dump_json(continuation_messages)

        async with async_session_factory() as session:
            stored_messages = list(
                (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id == conversation_id)
                        .order_by(Message.sequence)
                    )
                ).scalars()
            )
        outer_call = next(
            message
            for message in stored_messages
            if message.role == MessageRole.TOOL_CALL and message.tool_call_id == "outer"
        )
        nested_calls = [
            message
            for message in stored_messages
            if message.role == MessageRole.TOOL_CALL and message.tool_name == "read_chunk"
        ]
        assert len(nested_calls) == 3
        assert all(message.tool_result == child_receipt for message in nested_calls)
        assert outer_call.tool_result is not None
        audit = outer_call.tool_result[CODE_MODE_AUDIT_METADATA_KEY]
        assert audit[CODE_MODE_NESTED_TOOL_CALL_IDS_KEY] == [
            message.tool_call_id for message in nested_calls
        ]
        assert not ({message.tool_call_id for message in nested_calls} & continuation_ids)
    finally:
        e2e_client.delete(
            f"/api/chat/conversations/{conversation_id}",
            headers=platform_admin.headers,
        )
        e2e_client.delete(f"/api/agents/{agent_data['id']}", headers=platform_admin.headers)
