"""Durable delegation lifecycle coverage across chat and autonomous surfaces."""

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
from src.models.orm.agents import Agent, AgentDelegation, Conversation
from src.models.orm.agent_runs import AgentRun
from src.services.agent_executor import AgentExecutor
from src.services.execution.agent_helpers import agent_delegation_slug
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, ToolCallRequest
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.model_capabilities import manual_capabilities


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_rabbitmq_publish():
    """Keep durable queue nudges in-process.

    Delegation admits and child completions publish a RabbitMQ nudge via
    ``src.jobs.rabbitmq.publish_message``. Every run in this file executes
    in-process, so a real publish would disturb the shared stack worker and
    pin the process-wide publisher pools to one test's function-scoped loop
    (see ``RabbitMQConnection.reset_pools``). Swallow the nudges.
    """
    with patch("src.jobs.rabbitmq.publish_message", new_callable=AsyncMock) as mock:
        yield mock


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
    chat_profile = MagicMock(id=uuid4(), name="Test parent")
    chat_capabilities = manual_capabilities(
        provider="openai",
        model="test-parent",
        endpoint=None,
        image_input=False,
        pdf_input=False,
        tool_calling=True,
    )

    parent_model = DelegatingTestModel(agent_delegation_slug(child["name"]))
    child_model = TestModel(custom_output_text="Durable child answer")

    def _route_model(config, model=None, **kwargs):
        """Route model construction by config: parent chat vs delegated child.

        Both executors build through the shared factory now, so dispatch on
        the resolved model name instead of stacking two patches on one target
        (the second would shadow the first for both surfaces).
        """
        del kwargs
        name = model or config.model
        return parent_model if name == "test-parent" else child_model

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


async def test_child_terminal_before_parent_wait_commit_wakes_same_parent(
    e2e_client,
    platform_admin,
    db_session,
    async_session_factory,
):
    """Regression: a child may finish before the parent wait commit lands."""
    from src.services.agent_runtime import run_store
    from src.services.agent_runtime.delegation import (
        suspend_for_deferred_calls,
        wake_parent_for_child,
    )

    child = _create_agent(
        e2e_client,
        platform_admin,
        f"Early Terminal Child {uuid4().hex[:8]}",
    )
    parent = _create_agent(
        e2e_client,
        platform_admin,
        f"Early Terminal Parent {uuid4().hex[:8]}",
    )
    parent_id = None
    early_child_id = None
    try:
        db_session.add(
            AgentDelegation(
                parent_agent_id=UUID(parent["id"]),
                child_agent_id=UUID(child["id"]),
            )
        )
        parent_run = AgentRun(
            agent_id=UUID(parent["id"]),
            trigger_type="manual",
            status="queued",
            input={"task": "Delegate while child finishes early."},
        )
        db_session.add(parent_run)
        await db_session.commit()
        parent_id = parent_run.id

        async with async_session_factory() as session:
            claimed = await run_store.claim_run(session, parent_id, "worker-parent")
            lease_token = claimed.lease_token
            assert lease_token is not None

        async with async_session_factory() as session:
            agent_result = await session.execute(
                select(Agent)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                    selectinload(Agent.roles),
                )
                .where(Agent.id == UUID(parent["id"]))
            )
            parent_agent = agent_result.scalar_one()

        real_transition_waiting = run_store.transition_waiting

        async def terminalize_child_before_parent_wait(*args, **kwargs):
            nonlocal early_child_id
            async with async_session_factory() as child_session:
                parent_before_wait = await child_session.get(AgentRun, parent_id)
                assert parent_before_wait is not None
                assert parent_before_wait.status == "running"
                assert parent_before_wait.lease_token == lease_token
                child_row = (
                    await child_session.execute(
                        select(AgentRun).where(AgentRun.parent_run_id == parent_id)
                    )
                ).scalar_one()
                early_child_id = child_row.id
                child_claim = await run_store.claim_run(
                    child_session, child_row.id, "worker-child"
                )
                assert child_claim.lease_token is not None
                await run_store.finish_run(
                    child_session,
                    child_row.id,
                    child_claim.lease_token,
                    "completed",
                    output={"text": "finished before parent waited"},
                )
            assert await wake_parent_for_child(async_session_factory, early_child_id) is False
            return await real_transition_waiting(*args, **kwargs)

        slug = agent_delegation_slug(child["name"])
        tool_call = ToolCallPart(
            slug,
            {"task": "Finish before parent wait commits"},
            tool_call_id="early-child-call",
        )

        with (
            patch(
                "src.services.agent_runtime.execution_snapshot.get_llm_config",
                new_callable=AsyncMock,
                return_value=LLMConfig(
                    provider="openai", model="test-child", api_key="test-key"
                ),
            ),
            patch(
                "src.services.execution.agent_run_service.publish_message",
                new=AsyncMock(),
            ),
            patch("src.jobs.rabbitmq.publish_message", new=AsyncMock()) as parent_nudge,
            patch(
                "src.services.agent_runtime.delegation.run_store.transition_waiting",
                new=AsyncMock(side_effect=terminalize_child_before_parent_wait),
            ),
        ):
            suspended = await suspend_for_deferred_calls(
                session_factory=async_session_factory,
                parent_run_id=parent_id,
                lease_token=lease_token,
                agent=parent_agent,
                execution_snapshot=None,
                tool_calls=[tool_call],
                caller={
                    "user_id": str(platform_admin.user_id),
                    "email": platform_admin.email,
                    "name": platform_admin.name,
                    "organization_id": None,
                    "is_superuser": True,
                    "is_platform_admin": True,
                },
                caller_context={"ticket_id": 99},
                correlation={"kind": "early-child"},
            )

        assert early_child_id is not None
        assert suspended == {
            "status": "suspended",
            "child_run_ids": [str(early_child_id)],
            "woken": True,
        }
        parent_nudge.assert_awaited_once_with(
            "agent-runs", {"run_id": str(parent_id)}
        )

        async with async_session_factory() as session:
            parent_after = await session.get(AgentRun, parent_id)
            assert parent_after is not None
            assert parent_after.status == "running"
            assert parent_after.lease_token is None
            child_after = await session.get(AgentRun, early_child_id)
            assert child_after is not None
            assert child_after.status == "completed"
            assert child_after.parent_run_id == parent_id

        assert (
            await wake_parent_for_child(async_session_factory, early_child_id) is False
        )

        async with async_session_factory() as session:
            resumed = await run_store.claim_run(session, parent_id, "worker-resume")
            assert resumed.id == parent_id
            assert resumed.lease_token is not None
            assert resumed.attempt == 2
    finally:
        if parent_id is not None:
            async with async_session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(AgentRun).where(
                                (AgentRun.id == parent_id)
                                | (AgentRun.parent_run_id == parent_id)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    await session.delete(row)
                await session.commit()
        for agent in (parent, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}",
                headers=platform_admin.headers,
            )


async def test_durable_delegation_suspends_wakes_and_resumes_same_parent(
    e2e_client,
    platform_admin,
    db_session,
    async_session_factory,
):
    """Durable delegation: suspend, independent child, wake once, resume."""
    from src.services.agent_runtime import run_store
    from src.services.agent_runtime.delegation import wake_parent_for_child
    from src.services.agent_runtime.resume import prepare_resume

    child = _create_agent(
        e2e_client,
        platform_admin,
        f"Durable Child {uuid4().hex[:8]}",
    )
    parent = _create_agent(
        e2e_client,
        platform_admin,
        f"Durable Parent {uuid4().hex[:8]}",
    )
    try:
        db_session.add(
            AgentDelegation(
                parent_agent_id=UUID(parent["id"]),
                child_agent_id=UUID(child["id"]),
            )
        )
        parent_run = AgentRun(
            agent_id=UUID(parent["id"]),
            trigger_type="manual",
            status="queued",
            input={"task": "Delegate this request."},
        )
        db_session.add(parent_run)
        await db_session.commit()
        parent_id = parent_run.id

        async with async_session_factory() as session:
            claimed = await run_store.claim_run(session, parent_id, "worker-a")
            lease_token = claimed.lease_token
            assert lease_token is not None

        async with async_session_factory() as session:
            agent_result = await session.execute(
                select(Agent)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                    selectinload(Agent.roles),
                )
                .where(Agent.id == UUID(parent["id"]))
            )
            agent = agent_result.scalar_one()

        slug = agent_delegation_slug(child["name"])
        executor = AutonomousAgentExecutor(async_session_factory)
        with (
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                return_value=DelegatingTestModel(slug),
            ),
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new_callable=AsyncMock,
                return_value=[LLMConfig(
                    provider="openai", model="test-parent", api_key="test-key"
                )],
            ),
            patch(
                "src.services.agent_runtime.execution_snapshot.get_llm_config",
                new_callable=AsyncMock,
                return_value=LLMConfig(
                    provider="openai", model="test-parent", api_key="test-key"
                ),
            ),
            patch(
                "src.services.execution.run_summarizer.enqueue_summarize",
                new_callable=AsyncMock,
            ),
            patch(
                "src.jobs.rabbitmq.publish_message", new=AsyncMock()
            ),
            patch(
                "src.services.execution.agent_run_service.publish_message",
                new=AsyncMock(),
            ) as enqueue_nudges,
        ):
            suspended = await executor.run(
                agent,
                input_data={"task": "Delegate this request."},
                run_id=str(parent_id),
                lease_token=lease_token,
                caller_context={"ticket_id": 7},
                correlation={"kind": "ticket", "ticket_id": "7"},
            )
        assert suspended["status"] == "suspended"
        child_ids = suspended["suspended"]["child_run_ids"]
        assert len(child_ids) == 1
        child_id = UUID(child_ids[0])
        assert enqueue_nudges.await_count >= 1
        assert enqueue_nudges.await_args.args[1] == {"run_id": str(child_id)}

        async with async_session_factory() as session:
            parent_row = await session.get(AgentRun, parent_id)
            assert parent_row is not None
            assert parent_row.status == "waiting_child"
            assert parent_row.lease_token is None
            child_row = await session.get(AgentRun, child_id)
            assert child_row is not None
            assert child_row.status == "queued"
            assert child_row.parent_run_id == parent_id
            assert child_row.root_run_id == parent_id
            assert child_row.input["task"] == "Return the durable callback"
            assert child_row.input["locators"] == {"ticket_id": 7}
            assert child_row.correlation["kind"] == "ticket"
            assert child_row.output_schema is None
            assert child_row.execution_snapshot is not None

        # Parent consumes no worker while waiting; the child terminalizes
        # independently and wakes the same parent exactly once.
        async with async_session_factory() as session:
            child_claimed = await run_store.claim_run(
                session, child_id, "worker-b"
            )
            assert child_claimed.lease_token is not None
            await run_store.finish_run(
                session,
                child_id,
                child_claimed.lease_token,
                "completed",
                output={"text": "Durable child answer"},
            )
        with patch(
            "src.jobs.rabbitmq.publish_message", new=AsyncMock()
        ) as wake_nudges:
            assert await wake_parent_for_child(
                async_session_factory, child_id
            ) is True
            assert await wake_parent_for_child(
                async_session_factory, child_id
            ) is False
        wake_nudges.assert_awaited_once_with(
            "agent-runs", {"run_id": str(parent_id)}
        )

        async with async_session_factory() as session:
            resumed_claim = await run_store.claim_run(
                session, parent_id, "worker-c"
            )
            resume_token = resumed_claim.lease_token
            assert resume_token is not None
            assert resumed_claim.attempt == 2
        plan, _, unrecoverable = await prepare_resume(
            async_session_factory, parent_id, resume_token
        )
        assert unrecoverable is None
        assert plan is not None
        assert plan.deferred_results == {
            "parent-delegation-call": "Durable child answer"
        }

        resumed_executor = AutonomousAgentExecutor(async_session_factory)
        with (
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                return_value=DelegatingTestModel(slug),
            ),
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new_callable=AsyncMock,
                return_value=[LLMConfig(
                    provider="openai", model="test-parent", api_key="test-key"
                )],
            ),
            patch(
                "src.services.execution.run_summarizer.enqueue_summarize",
                new_callable=AsyncMock,
            ),
        ):
            result = await resumed_executor.run(
                agent,
                input_data={"task": "Delegate this request."},
                run_id=str(parent_id),
                lease_token=resume_token,
                resume_history=plan.history or None,
                deferred_results=plan.deferred_results or None,
            )
        assert result["status"] == "completed"
        assert result["output"] == "Parent received the durable callback."

        async with async_session_factory() as session:
            finished = await run_store.finish_run(
                session,
                parent_id,
                resume_token,
                "completed",
                output=(
                    result["output"]
                    if isinstance(result["output"], dict)
                    else {"text": result["output"]}
                ),
            )
            assert finished.status == "completed"

        # The durable child remains visible through the run-detail tree.
        detail_response = e2e_client.get(
            f"/api/agent-runs/{parent_id}",
            headers=platform_admin.headers,
        )
        assert detail_response.status_code == 200, detail_response.text
        child_runs = detail_response.json()["child_runs"]
        assert [run["id"] for run in child_runs] == [str(child_id)]
    finally:
        for agent in (parent, child):
            e2e_client.delete(
                f"/api/agents/{agent['id']}",
                headers=platform_admin.headers,
            )
