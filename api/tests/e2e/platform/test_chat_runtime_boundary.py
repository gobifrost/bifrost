"""Real dispatch-process coverage for one durable chat completion."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent, Conversation
from src.models.orm.ai_models import (
    AIModelAssignment,
    AIModelProfile,
    AIProviderConnection,
)
from src.models.orm.ai_usage import AIUsage
from src.models.orm.work_dispatches import WorkDispatch
from tests.e2e.conftest import poll_until


pytestmark = pytest.mark.e2e


async def _force_chat_completions(async_session_factory, profile_id: UUID) -> None:
    async with async_session_factory() as session:
        profile = await session.get(AIModelProfile, profile_id)
        assert profile is not None
        profile.openai_transport = "chat_completions"
        await session.commit()


async def _chat_usage(async_session_factory, conversation_id: UUID) -> list[AIUsage]:
    async with async_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(AIUsage).where(AIUsage.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )


async def _cleanup_fixture(
    async_session_factory,
    *,
    connection_id: UUID | None,
    profile_id: UUID | None,
    agent_id: UUID | None,
    conversation_id: UUID | None,
    run_id: UUID | None,
) -> None:
    async with async_session_factory() as session:
        if conversation_id is not None:
            await session.execute(
                delete(AIUsage).where(AIUsage.conversation_id == conversation_id)
            )
        if run_id is not None:
            await session.execute(
                delete(WorkDispatch).where(WorkDispatch.subject_id == run_id)
            )
            await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        if conversation_id is not None:
            await session.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
        if agent_id is not None:
            await session.execute(delete(Agent).where(Agent.id == agent_id))
        if profile_id is not None:
            await session.execute(
                delete(AIModelAssignment).where(
                    AIModelAssignment.profile_id == profile_id
                )
            )
            await session.execute(
                delete(AIModelProfile).where(AIModelProfile.id == profile_id)
            )
        if connection_id is not None:
            await session.execute(
                delete(AIProviderConnection).where(
                    AIProviderConnection.id == connection_id
                )
            )
        await session.commit()


def test_durable_chat_completion_persists_replayable_turn_and_usage(
    e2e_client,
    platform_admin,
    async_session_factory,
) -> None:
    suffix = uuid4().hex
    connection_id: UUID | None = None
    profile_id: UUID | None = None
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    run_id: UUID | None = None

    try:
        connection = e2e_client.post(
            "/api/admin/ai/connections",
            headers=platform_admin.headers,
            json={
                "name": f"durable-chat-connection-{suffix}",
                "provider": "openai_compatible",
                "api_key": "fixture-key",
                "endpoint": "http://scheduler-fixtures:8080/v1",
            },
        )
        assert connection.status_code == 201, connection.text
        connection_id = UUID(connection.json()["id"])

        profile = e2e_client.post(
            "/api/admin/ai/profiles",
            headers=platform_admin.headers,
            json={
                "name": f"fixture-chat-{suffix}",
                "connection_id": str(connection_id),
                "model": "fixture-chat",
            },
        )
        assert profile.status_code == 201, profile.text
        profile_id = UUID(profile.json()["id"])
        asyncio.run(_force_chat_completions(async_session_factory, profile_id))

        agent = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": f"durable-chat-agent-{suffix}",
                "description": "Durable chat fixture agent",
                "system_prompt": "Reply with exactly ok.",
                "access_level": "authenticated",
                "llm_profile_id": str(profile_id),
            },
        )
        assert agent.status_code == 201, agent.text
        agent_id = UUID(agent.json()["id"])

        conversation = e2e_client.post(
            "/api/chat/conversations",
            headers=platform_admin.headers,
            json={
                "agent_id": str(agent_id),
                "channel": "chat",
                "title": "Durable chat fixture",
            },
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = UUID(conversation.json()["id"])

        submitted = e2e_client.post(
            "/api/chat/runs",
            headers=platform_admin.headers,
            json={
                "conversation_id": str(conversation_id),
                "client_run_id": str(uuid4()),
                "user_message_id": str(uuid4()),
                "content": "Reply with exactly ok.",
            },
        )
        assert submitted.status_code == 201, submitted.text
        run_id = UUID(submitted.json()["run_id"])

        def completed_state() -> dict | None:
            response = e2e_client.get(
                f"/api/chat/conversations/{conversation_id}/state",
                headers=platform_admin.headers,
            )
            if response.status_code != 200:
                return None
            state = response.json()
            run = state["active_run"]
            assert run is not None
            assert run["status"] not in {
                "failed",
                "cancelled",
                "timeout",
                "budget_exceeded",
            }, state
            return state if run["status"] == "completed" else None

        state = poll_until(completed_state, max_wait=45, interval=0.2)
        assert state is not None, "durable chat run did not complete"
        assert state["active_run"]["id"] == str(run_id)
        assert state["active_run"]["started_at"] is not None
        assert state["active_run"]["completed_at"] is not None
        assert [message["content"] for message in state["messages"]] == [
            "Reply with exactly ok.",
            "ok",
        ]
        assert [message["role"] for message in state["messages"]] == [
            "user",
            "assistant",
        ]

        usage = asyncio.run(_chat_usage(async_session_factory, conversation_id))
        assert len(usage) == 1
        assert usage[0].model == "fixture-chat"
        assert (usage[0].input_tokens, usage[0].output_tokens) == (1, 1)
    finally:
        asyncio.run(
            _cleanup_fixture(
                async_session_factory,
                connection_id=connection_id,
                profile_id=profile_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                run_id=run_id,
            )
        )
