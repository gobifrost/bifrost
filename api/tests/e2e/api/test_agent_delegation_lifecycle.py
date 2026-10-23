"""Durable delegation lifecycle coverage across chat and autonomous surfaces."""

import asyncio
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

from src.models.contracts.artifacts import ModelCapabilities
from src.core.principal import UserPrincipal
from src.models.orm.agents import Agent, AgentDelegation, Conversation
from src.models.orm.agent_runs import AgentRun
from src.services.agent_executor import AgentExecutor
from src.services.agent_runtime.composition import _delegate_name
from src.services.authorization.callers import load_human_caller
from src.services.authorization.enforce import load_caller
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, ToolCallRequest
from src.services.llm.pydantic_client import PydanticAIClient


pytestmark = pytest.mark.asyncio


class DelegatingTestModel(TestModel):
    """Emit one explicit delegation followed by the parent's final answer."""

    def __init__(self, agent_name: str) -> None:
        super().__init__(model_name="test-parent")
        self._agent_name = agent_name

    def _request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        del model_settings
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
            assert any(
                tool.name == "delegate_task"
                for tool in model_request_parameters.function_tools
            )
            assert any(
                self._agent_name in part.content
                for part in model_request_parameters.instruction_parts or ()
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": self._agent_name,
                            "task": "Return the durable callback",
                        },
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
        async with session_factory() as session:
            caller = await load_human_caller(session, alice_user.user_id)
        child_run_kwargs: dict = {}

        async def child_run(child_executor, **kwargs):
            child_run_kwargs.update(kwargs)
            # The real run() counts each model response on the executor.
            child_executor._own_tokens = 42
            return {
                "output": "Durable answer",
                "status": "completed",
                "iterations_used": 2,
                "tokens_used": child_executor.subtree_tokens,
                "llm_model": "cheap-model",
            }

        with (
            patch.object(
                AutonomousAgentExecutor,
                "run",
                autospec=True,
                side_effect=child_run,
            ),
            patch(
                "src.services.execution.summary_dispatch.stage_summary",
                new_callable=AsyncMock,
                return_value=None,
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
                acting_user_id=caller.principal.user_id,
                run_user_id=alice_user.user_id,
                run_as=None,
                agent_execution_mode="automatic",
            )

        # The delegated run keeps the delegating run's user.
        assert child_run_kwargs["run_user_id"] == alice_user.user_id
        assert child_run_kwargs["agent_execution_mode"] == "automatic"
        async with session_factory() as session:
            persisted = await session.get(AgentRun, outcome.child_run_id)
            assert persisted is not None
            assert persisted.run_user_id == alice_user.user_id
            assert persisted.acting_user_id == alice_user.user_id
            assert persisted.status == "completed"
            assert persisted.completed_at is not None
            assert persisted.parent_run_id is None
            assert persisted.conversation_id == conversation_id
            assert persisted.org_id == alice_user.organization_id
            assert persisted.caller_user_id == str(caller.principal.user_id)
            assert persisted.caller_email == caller.principal.email
            assert persisted.caller_name == caller.principal.name
            assert persisted.run_metadata["agent_execution_mode"] == "automatic"
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


async def test_delegation_timeout_persists_sanitized_partial_evidence(
    e2e_client,
    platform_admin,
    db_session,
):
    """A real delegation deadline persists only caller-safe tool evidence."""
    child = _create_agent(
        e2e_client,
        platform_admin,
        f"Timeout Evidence Child {uuid4().hex[:8]}",
    )
    parent = _create_agent(
        e2e_client,
        platform_admin,
        f"Timeout Evidence Parent {uuid4().hex[:8]}",
    )
    db_session.add(
        AgentDelegation(
            parent_agent_id=UUID(parent["id"]),
            child_agent_id=UUID(child["id"]),
        )
    )
    await db_session.commit()

    session_factory = _session_factory_for(db_session)
    try:
        parent_result = await db_session.execute(
            select(Agent)
            .options(selectinload(Agent.delegated_agents))
            .where(Agent.id == UUID(parent["id"]))
        )
        parent_agent = parent_result.scalar_one()
        caller = await load_human_caller(db_session, platform_admin.user_id)
        assert caller.ctx is not None

        async def block_after_completed_tool(child_executor, **_kwargs):
            child_executor._current_run_id = _kwargs["run_id"]
            child_executor._pending_steps = [
                {
                    "id": str(uuid4()),
                    "run_id": _kwargs["run_id"],
                    "step_number": 1,
                    "type": "tool_result",
                    "content": {
                        "tool_name": "read_status",
                        "result": "service is running",
                        "is_error": False,
                    },
                },
                {
                    "id": str(uuid4()),
                    "run_id": _kwargs["run_id"],
                    "step_number": 2,
                    "type": "tool_call",
                    "content": {
                        "tool_name": "restart_service",
                        "arguments": {"token": "must not leak"},
                    },
                },
            ]
            await asyncio.Event().wait()

        original_wait_for = asyncio.wait_for

        async def shortened_deadline(coro, timeout):
            if timeout == 600:
                return await original_wait_for(coro, timeout=0.01)
            return await original_wait_for(coro, timeout=timeout)

        with (
            patch.object(
                AutonomousAgentExecutor,
                "run",
                autospec=True,
                side_effect=block_after_completed_tool,
            ),
            patch(
                "src.services.execution.autonomous_agent_executor.asyncio.wait_for",
                new=shortened_deadline,
            ),
        ):
            outcome = await AutonomousAgentExecutor(session_factory).run_delegation(
                parent_agent=parent_agent,
                tool_call=ToolCallRequest(
                    id="timeout-call",
                    name=f"delegate_to_{child['name'].lower().replace(' ', '_')}",
                    arguments={"task": "Check the service"},
                ),
                caller=caller,
                acting_user_id=caller.principal.user_id,
                run_user_id=caller.ctx.user_id,
                run_as=None,
            )

        assert outcome.status == "timeout"
        assert "completed step" in (outcome.error or "")
        assert "service is running" in (outcome.error or "")
        assert "restart_service" in (outcome.error or "")
        assert "must not leak" not in (outcome.error or "")

        persisted = await db_session.get(AgentRun, outcome.child_run_id)
        assert persisted is not None
        assert persisted.status == "timeout"
        assert persisted.acting_user_id == caller.principal.user_id
        assert persisted.error == (
            f"Delegation to {child['name']} timed out after 600s"
        )
        assert persisted.output is not None
        assert "service is running" in persisted.output["text"]
        assert "must not leak" not in persisted.output["text"]
    finally:
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
    chat_profile = MagicMock(id=uuid4(), name="Test parent")
    chat_capabilities = ModelCapabilities(tool_calling=True, source="manual")

    parent_model: DelegatingTestModel | None = None
    child_model = TestModel(custom_output_text="Durable child answer")

    def _route_model(config, model=None, **kwargs):
        """Route model construction by config: parent chat vs delegated child.

        Both executors build through the shared factory now, so dispatch on
        the resolved model name instead of stacking two patches on one target
        (the second would shadow the first for both surfaces).
        """
        del kwargs
        name = model or config.model
        if name != "test-parent":
            return child_model
        assert parent_model is not None
        return parent_model

    try:
        with (
            patch(
                "src.services.agent_executor.get_llm_client",
                new_callable=AsyncMock,
                return_value=parent_llm,
            ),
            patch(
                "src.services.agent_executor.AIModelService.resolve_chat_profile",
                new=AsyncMock(
                    return_value=(
                        chat_profile,
                        LLMConfig(provider="openai", model="test-parent", api_key="test-key"),
                        chat_capabilities,
                    )
                ),
            ),
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                side_effect=_route_model,
            ),
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new_callable=AsyncMock,
                return_value=[
                    LLMConfig(
                        provider="openai",
                        model="test-child",
                        api_key="test-key",
                    )
                ],
            ),
            patch(
                "src.services.execution.summary_dispatch.stage_summary",
                new_callable=AsyncMock,
                return_value=None,
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
            delegated_child = next(
                item
                for item in conversation.agent.delegated_agents
                if item.id == UUID(child["id"])
            )
            parent_model = DelegatingTestModel(_delegate_name(delegated_child))

            assert platform_admin.user_id is not None
            principal = UserPrincipal(
                user_id=platform_admin.user_id,
                email=platform_admin.email,
                name=platform_admin.name,
                organization_id=platform_admin.organization_id,
                is_superuser=platform_admin.is_superuser,
            )
            executor = AgentExecutor(session_factory)
            caller = await load_caller(db_session, principal)
            final_content = ""
            async for chunk in executor.chat(
                agent=conversation.agent,
                conversation=conversation,
                user_message="Delegate this request.",
                stream=False,
                enable_routing=False,
                caller=caller,
                run_user_id=platform_admin.user_id,
                agent_execution_mode="automatic",
            ):
                if chunk.type == "done":
                    final_content = chunk.content or ""
                elif chunk.type == "error":
                    pytest.fail(f"Chat returned an execution error: {chunk!r}")

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
        assert all(message["tool_state"] == "completed" for message in tool_call_messages)
        delegation_message = next(
            message for message in tool_call_messages
            if message["tool_name"] == "delegate_task"
        )

        result = await db_session.execute(
            select(AgentRun).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.trigger_type == "delegation",
            )
        )
        child_run = result.scalar_one()
        assert child_run.status == "completed"
        assert child_run.agent_id == UUID(child["id"])
        assert child_run.output == {"text": "Durable child answer"}
        assert child_run.caller_user_id == str(platform_admin.user_id)
        assert child_run.run_user_id == platform_admin.user_id
        assert child_run.acting_user_id == platform_admin.user_id

        assert delegation_message["tool_result"]["child_run_id"] == str(
            child_run.id
        )
        assert delegation_message["tool_result"]["response"] == (
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
