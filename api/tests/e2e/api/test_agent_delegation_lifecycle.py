"""Durable delegation lifecycle coverage across chat and autonomous surfaces."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
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
from src.models.orm.agents import Agent, AgentDelegation, Conversation
from src.models.orm.agent_runs import AgentRun
from src.services.agent_executor import AgentExecutor
from src.services.execution.agent_helpers import agent_delegation_slug
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, ToolCallRequest
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.llm_config_service import LLMProviderConfig


pytestmark = pytest.mark.asyncio


class DelegatingTestModel(TestModel):
    """Emit one explicit delegation followed by the parent's final answer."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(model_name="test-parent")
        self._tool_name = tool_name

    def _request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        del model_settings, model_request_parameters
        delegation_completed = any(
            isinstance(message, ModelRequest)
            and any(
                isinstance(part, ToolReturnPart)
                and part.tool_call_id == "parent-delegation-call"
                for part in message.parts
            )
            for message in messages
        )
        if not delegation_completed:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        self._tool_name,
                        {"task": "Return the durable callback"},
                        tool_call_id="parent-delegation-call",
                    )
                ],
                model_name="test-parent",
            )
        return ModelResponse(
            parts=[TextPart("Parent received the durable callback.")],
            model_name="test-parent",
        )


def _session_factory_for(session):
    @asynccontextmanager
    async def session_factory():
        yield session

    return session_factory


def _create_agent(e2e_client, platform_admin, name: str) -> dict:
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "Delegation lifecycle E2E agent",
            "system_prompt": "Complete delegated work.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": None,
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_chat_delegation_creates_terminal_run_with_conversation_and_caller(
    e2e_client,
    platform_admin,
    alice_user,
    bob_user,
    db_session,
):
    child = _create_agent(
        e2e_client,
        platform_admin,
        f"Chat Lifecycle Child {uuid4().hex[:8]}",
    )
    parent = _create_agent(
        e2e_client,
        platform_admin,
        f"Chat Lifecycle Parent {uuid4().hex[:8]}",
    )
    conversation_response = e2e_client.post(
        "/api/chat/conversations",
        json={
            "agent_id": parent["id"],
            "channel": "chat",
            "title": "Delegation lifecycle",
        },
        headers=alice_user.headers,
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = UUID(conversation_response.json()["id"])

    db_session.add(
        AgentDelegation(
            parent_agent_id=UUID(parent["id"]),
            child_agent_id=UUID(child["id"]),
        )
    )
    await db_session.commit()

    session_factory = _session_factory_for(db_session)
    try:
        async with session_factory() as session:
            parent_result = await session.execute(
                select(Agent)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                )
                .where(Agent.id == UUID(parent["id"]))
            )
            parent_agent = parent_result.scalar_one()

        executor = AutonomousAgentExecutor(session_factory)
        caller = {
            "user_id": str(alice_user.user_id),
            "email": alice_user.email,
            "name": alice_user.name,
            "organization_id": str(alice_user.organization_id),
        }
        with (
            patch.object(
                AutonomousAgentExecutor,
                "run",
                new_callable=AsyncMock,
                return_value={
                    "output": "Durable answer",
                    "status": "completed",
                    "iterations_used": 2,
                    "tokens_used": 42,
                    "llm_model": "cheap-model",
                },
            ),
            patch(
                "src.services.execution.run_summarizer.enqueue_summarize",
                new_callable=AsyncMock,
            ),
        ):
            outcome = await executor.run_delegation(
                parent_agent=parent_agent,
                tool_call=ToolCallRequest(
                    id="tc1",
                    name=f"delegate_to_{child['name'].lower().replace(' ', '_')}",
                    arguments={"task": "Return a durable answer"},
                ),
                conversation_id=conversation_id,
                caller=caller,
            )

        async with session_factory() as session:
            persisted = await session.get(AgentRun, outcome.child_run_id)
            assert persisted is not None
            assert persisted.status == "completed"
            assert persisted.completed_at is not None
            assert persisted.parent_run_id is None
            assert persisted.conversation_id == conversation_id
            assert persisted.org_id == alice_user.organization_id
            assert persisted.caller_user_id == caller["user_id"]
            assert persisted.caller_email == caller["email"]
            assert persisted.caller_name == caller["name"]
            assert persisted.output == {"text": "Durable answer"}
            assert persisted.iterations_used == 2
            assert persisted.tokens_used == 42

        scoped_response = e2e_client.get(
            "/api/agent-runs",
            params={"agent_id": child["id"]},
            headers=alice_user.headers,
        )
        assert scoped_response.status_code == 200, scoped_response.text
        assert str(outcome.child_run_id) in {
            item["id"] for item in scoped_response.json()["items"]
        }

        other_user_list = e2e_client.get(
            "/api/agent-runs",
            params={"agent_id": child["id"]},
            headers=bob_user.headers,
        )
        assert other_user_list.status_code == 200, other_user_list.text
        assert str(outcome.child_run_id) not in {
            item["id"] for item in other_user_list.json()["items"]
        }

        owner_detail = e2e_client.get(
            f"/api/agent-runs/{outcome.child_run_id}",
            headers=alice_user.headers,
        )
        assert owner_detail.status_code == 200, owner_detail.text

        other_user_detail = e2e_client.get(
            f"/api/agent-runs/{outcome.child_run_id}",
            headers=bob_user.headers,
        )
        assert other_user_detail.status_code == 404, other_user_detail.text

        admin_detail = e2e_client.get(
            f"/api/agent-runs/{outcome.child_run_id}",
            headers=platform_admin.headers,
        )
        assert admin_detail.status_code == 200, admin_detail.text

        global_response = e2e_client.get(
            "/api/agent-runs",
            headers=alice_user.headers,
        )
        assert global_response.status_code == 200, global_response.text
        assert str(outcome.child_run_id) not in {
            item["id"] for item in global_response.json()["items"]
        }
    finally:
        e2e_client.delete(
            f"/api/chat/conversations/{conversation_id}",
            headers=alice_user.headers,
        )
        for agent in (parent, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}",
                headers=platform_admin.headers,
            )


async def test_chat_executor_receives_durable_child_callback(
    e2e_client,
    platform_admin,
    db_session,
):
    child = _create_agent(
        e2e_client,
        platform_admin,
        f"Chat Callback Child {uuid4().hex[:8]}",
    )
    parent = _create_agent(
        e2e_client,
        platform_admin,
        f"Chat Callback Parent {uuid4().hex[:8]}",
    )
    conversation_response = e2e_client.post(
        "/api/chat/conversations",
        json={
            "agent_id": parent["id"],
            "channel": "chat",
            "title": "Delegation callback",
        },
        headers=platform_admin.headers,
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = UUID(conversation_response.json()["id"])

    db_session.add(
        AgentDelegation(
            parent_agent_id=UUID(parent["id"]),
            child_agent_id=UUID(child["id"]),
        )
    )
    await db_session.commit()

    parent_llm = PydanticAIClient(
        LLMConfig(provider="openai", model="test-parent", api_key="test-key")
    )

    try:
        with (
            patch(
                "src.services.agent_executor.get_llm_client",
                new_callable=AsyncMock,
                return_value=parent_llm,
            ),
            patch(
                "src.services.llm_config_service.LLMConfigService.get_config",
                new=AsyncMock(
                    return_value=LLMProviderConfig(
                        provider="openai",
                        model="test-parent",
                    )
                ),
            ),
            patch(
                "src.services.agent_executor.create_agent_model",
                return_value=DelegatingTestModel(
                    agent_delegation_slug(child["name"])
                ),
            ),
            patch(
                "src.services.execution.autonomous_agent_executor.create_agent_model",
                return_value=TestModel(custom_output_text="Durable child answer"),
            ),
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_config",
                new_callable=AsyncMock,
                return_value=LLMConfig(
                    provider="openai",
                    model="test-child",
                    api_key="test-key",
                ),
            ),
            patch(
                "src.services.execution.run_summarizer.enqueue_summarize",
                new_callable=AsyncMock,
            ),
        ):
            session_factory = _session_factory_for(db_session)
            conversation_result = await db_session.execute(
                select(Conversation)
                .options(
                    selectinload(Conversation.agent).selectinload(Agent.tools),
                    selectinload(Conversation.agent).selectinload(
                        Agent.delegated_agents
                    ),
                )
                .where(Conversation.id == conversation_id)
            )
            conversation = conversation_result.scalar_one()

            assert platform_admin.user_id is not None
            principal = UserPrincipal(
                user_id=platform_admin.user_id,
                email=platform_admin.email,
                name=platform_admin.name,
                organization_id=platform_admin.organization_id,
                is_superuser=platform_admin.is_superuser,
            )
            executor = AgentExecutor(session_factory)
            final_content = ""
            async for chunk in executor.chat(
                agent=conversation.agent,
                conversation=conversation,
                user_message="Delegate this request.",
                stream=False,
                enable_routing=False,
                user=principal,
            ):
                if chunk.type == "done":
                    final_content = chunk.content or ""

        assert final_content == "Parent received the durable callback."

        messages_response = e2e_client.get(
            f"/api/chat/conversations/{conversation_id}/messages",
            headers=platform_admin.headers,
        )
        assert messages_response.status_code == 200, messages_response.text
        tool_call_messages = [
            message
            for message in messages_response.json()
            if message["role"] == "tool_call"
        ]
        assert len(tool_call_messages) == 1, messages_response.json()
        assert tool_call_messages[0]["tool_state"] == "completed", (
            tool_call_messages[0]
        )

        result = await db_session.execute(
            select(AgentRun).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.trigger_type == "delegation",
            )
        )
        child_run = result.scalar_one()
        assert child_run.status == "completed"
        assert child_run.output == {"text": "Durable child answer"}
        assert child_run.caller_user_id == str(platform_admin.user_id)

        assert tool_call_messages[0]["tool_result"]["child_run_id"] == str(
            child_run.id
        )
        assert tool_call_messages[0]["tool_result"]["response"] == (
            "Durable child answer"
        )
    finally:
        e2e_client.delete(
            f"/api/chat/conversations/{conversation_id}",
            headers=platform_admin.headers,
        )
        for agent in (parent, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}",
                headers=platform_admin.headers,
            )
