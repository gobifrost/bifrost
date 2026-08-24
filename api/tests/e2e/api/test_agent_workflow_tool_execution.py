"""Live chat boundary coverage for workflow-backed agent tools."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.principal import UserPrincipal
from src.models.orm.agents import Agent, Conversation
from src.services.agent_executor import AgentExecutor
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.model_capabilities import manual_capabilities


pytestmark = pytest.mark.asyncio


class WorkflowToolTestModel(TestModel):
    """Call one workflow tool, then acknowledge its completed result."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(model_name="test-chat-workflow-tool")
        self._tool_name = tool_name

    def _request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        del model_settings, model_request_parameters
        tool_completed = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        )
        if not tool_completed:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        self._tool_name,
                        {"query": "session boundary"},
                        tool_call_id="workflow-tool-call",
                    )
                ],
                model_name=self.model_name,
            )
        return ModelResponse(
            parts=[TextPart("Workflow tool completed.")],
            model_name=self.model_name,
        )


def _session_factory_for(session):
    @asynccontextmanager
    async def session_factory():
        yield session

    return session_factory


async def test_chat_executes_workflow_tool_through_live_worker(
    e2e_client,
    platform_admin,
    db_session,
    test_agent_with_tools,
):
    agent_data = test_agent_with_tools["agent"]
    workflow = test_agent_with_tools["workflow"]
    conversation_response = e2e_client.post(
        "/api/chat/conversations",
        json={
            "agent_id": agent_data["id"],
            "channel": "chat",
            "title": "Workflow tool session boundary",
        },
        headers=platform_admin.headers,
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = UUID(conversation_response.json()["id"])

    llm_client = PydanticAIClient(
        LLMConfig(provider="openai", model="test-chat", api_key="test-key")
    )
    profile = MagicMock(id=uuid4(), name="Test chat workflow tool")
    capabilities = manual_capabilities(
        provider="openai",
        model="test-chat",
        endpoint=None,
        image_input=False,
        pdf_input=False,
        tool_calling=True,
    )

    try:
        with (
            patch(
                "src.services.agent_executor.get_llm_client",
                new_callable=AsyncMock,
                return_value=llm_client,
            ),
            patch(
                "src.services.agent_executor.AIModelService.resolve_chat_profile",
                new=AsyncMock(
                    return_value=(
                        profile,
                        LLMConfig(
                            provider="openai",
                            model="test-chat",
                            api_key="test-key",
                        ),
                        capabilities,
                    )
                ),
            ),
            patch(
                "src.services.agent_executor.create_agent_model",
                return_value=WorkflowToolTestModel(f"wf_{workflow['name']}"),
            ),
        ):
            result = await db_session.execute(
                select(Conversation)
                .options(
                    selectinload(Conversation.agent).selectinload(Agent.tools),
                    selectinload(Conversation.agent).selectinload(
                        Agent.delegated_agents
                    ),
                    selectinload(Conversation.user),
                )
                .where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one()
            assert platform_admin.user_id is not None
            principal = UserPrincipal(
                user_id=platform_admin.user_id,
                email=platform_admin.email,
                name=platform_admin.name,
                organization_id=platform_admin.organization_id,
                is_superuser=platform_admin.is_superuser,
            )
            executor = AgentExecutor(_session_factory_for(db_session))
            final_content = ""
            async for chunk in executor.chat(
                agent=conversation.agent,
                conversation=conversation,
                user_message="Run the workflow tool.",
                stream=False,
                enable_routing=False,
                user=principal,
            ):
                if chunk.type == "done":
                    final_content = chunk.content or ""

        assert final_content == "Workflow tool completed."
        messages_response = e2e_client.get(
            f"/api/chat/conversations/{conversation_id}/messages",
            headers=platform_admin.headers,
        )
        assert messages_response.status_code == 200, messages_response.text
        tool_calls = [
            message
            for message in messages_response.json()
            if message["role"] == "tool_call"
        ]
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool_state"] == "completed"
        assert tool_calls[0]["tool_result"] == "result: session boundary"
    finally:
        e2e_client.delete(
            f"/api/chat/conversations/{conversation_id}",
            headers=platform_admin.headers,
        )
