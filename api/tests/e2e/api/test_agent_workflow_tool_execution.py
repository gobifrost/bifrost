"""Chat handler coverage for workflow-backed agent tools."""

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
from sqlalchemy import delete, update

from src.core.principal import UserPrincipal
from src.models.contracts.agents import ChatRequest
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.executions import Execution
from src.models.orm.workflows import Workflow
from src.routers.chat import send_message
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


async def test_chat_handler_executes_global_agent_workflow_in_caller_org(
    e2e_client,
    org1_user,
    db_session,
    test_agent_with_tools,
):
    agent_data = test_agent_with_tools["agent"]
    workflow = test_agent_with_tools["workflow"]
    await db_session.execute(
        update(Agent)
        .where(Agent.id == UUID(agent_data["id"]))
        .values(
            access_level=AgentAccessLevel.EVERYONE,
            organization_id=None,
        )
    )
    await db_session.execute(
        update(Workflow)
        .where(Workflow.id == UUID(workflow["id"]))
        .values(organization_id=None)
    )
    await db_session.commit()
    conversation_response = e2e_client.post(
        "/api/chat/conversations",
        json={
            "agent_id": agent_data["id"],
            "channel": "chat",
            "title": "Workflow tool session boundary",
        },
        headers=org1_user.headers,
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

    execution_id: str | None = None
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
            assert org1_user.user_id is not None
            principal = UserPrincipal(
                user_id=org1_user.user_id,
                email=org1_user.email,
                name=org1_user.name,
                organization_id=org1_user.organization_id,
                is_superuser=org1_user.is_superuser,
            )
            response = await send_message(
                conversation_id=conversation_id,
                request=ChatRequest(message="Run the workflow tool."),
                db=db_session,
                user=principal,
            )

        assert response.content == "Workflow tool completed."
        messages_response = e2e_client.get(
            f"/api/chat/conversations/{conversation_id}/messages",
            headers=org1_user.headers,
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
        execution_id = tool_calls[0]["execution_id"]
        assert execution_id is not None
        execution = await db_session.get(Execution, UUID(execution_id))
        assert execution is not None
        assert execution.organization_id == org1_user.organization_id
    finally:
        if execution_id is not None:
            await db_session.execute(
                delete(Execution).where(Execution.id == UUID(execution_id))
            )
            await db_session.commit()
        e2e_client.delete(
            f"/api/chat/conversations/{conversation_id}",
            headers=org1_user.headers,
        )
