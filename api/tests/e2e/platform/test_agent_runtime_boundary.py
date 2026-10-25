"""Real dispatch-process coverage for an API-enqueued agent run and summary."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.agents import Agent
from src.models.orm.ai_models import (
    AIModelAssignment,
    AIModelProfile,
    AIProviderConnection,
)
from src.models.orm.ai_usage import AIUsage
from src.models.orm.execution_workers import ExecutionWorkerClaim
from src.models.orm.work_dispatches import WorkDispatch
from tests.e2e.conftest import poll_until


pytestmark = pytest.mark.e2e


async def _summary_assignment_profile_id(async_session_factory) -> UUID | None:
    async with async_session_factory() as session:
        assignment = await session.get(AIModelAssignment, "summarization")
        return assignment.profile_id if assignment is not None else None


async def _force_chat_completions(
    async_session_factory, profile_ids: list[UUID]
) -> None:
    async with async_session_factory() as session:
        profiles = (
            await session.execute(
                select(AIModelProfile).where(AIModelProfile.id.in_(profile_ids))
            )
        ).scalars().all()
        assert len(profiles) == len(profile_ids)
        for profile in profiles:
            profile.openai_transport = "chat_completions"
        await session.commit()


async def _runtime_evidence(async_session_factory, run_id: UUID) -> tuple[int, int, dict[str, float]]:
    async with async_session_factory() as session:
        step_count = len(
            (
                await session.execute(
                    select(AgentRunStep).where(AgentRunStep.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        usage_count = len(
            (
                await session.execute(select(AIUsage).where(AIUsage.agent_run_id == run_id))
            )
            .scalars()
            .all()
        )
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.started_at is not None
        assert run.summary_generated_at is not None
        claims = dict((await session.execute(
            select(ExecutionWorkerClaim.family, ExecutionWorkerClaim.claimed_at)
            .where(ExecutionWorkerClaim.subject_id == run_id)
        )).all())
        return step_count, usage_count, {
            "agent_claim_to_started_seconds": round(
                (run.started_at - claims["agent_run"]).total_seconds(), 3
            ),
            "summary_claim_to_generated_seconds": round(
                (run.summary_generated_at - claims["agent_summary"]).total_seconds(), 3
            ),
        }


async def _cleanup_fixture(
    async_session_factory,
    *,
    original_summary_profile_id: UUID | None,
    connection_id: UUID | None,
    profile_ids: list[UUID],
    agent_id: UUID | None,
    run_id: UUID | None,
) -> None:
    async with async_session_factory() as session:
        if run_id is not None:
            await session.execute(delete(AIUsage).where(AIUsage.agent_run_id == run_id))
            await session.execute(delete(AgentRunStep).where(AgentRunStep.run_id == run_id))
            await session.execute(delete(WorkDispatch).where(WorkDispatch.subject_id == run_id))
            await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        if agent_id is not None:
            await session.execute(delete(Agent).where(Agent.id == agent_id))

        assignment = await session.get(AIModelAssignment, "summarization")
        if original_summary_profile_id is None:
            if assignment is not None:
                await session.delete(assignment)
        elif assignment is None:
            session.add(
                AIModelAssignment(
                    assignment_key="summarization",
                    profile_id=original_summary_profile_id,
                )
            )
        else:
            assignment.profile_id = original_summary_profile_id

        if profile_ids:
            await session.execute(
                delete(AIModelAssignment).where(
                    AIModelAssignment.profile_id.in_(profile_ids)
                )
            )
            await session.execute(
                delete(AIModelProfile).where(AIModelProfile.id.in_(profile_ids))
            )
        if connection_id is not None:
            await session.execute(
                delete(AIProviderConnection).where(
                    AIProviderConnection.id == connection_id
                )
            )
        await session.commit()


def test_api_agent_run_and_automatic_summary_use_real_dispatch_process(
    e2e_client,
    platform_admin,
    async_session_factory,
) -> None:
    suffix = uuid4().hex
    connection_id: UUID | None = None
    profile_ids: list[UUID] = []
    agent_id: UUID | None = None
    run_id: UUID | None = None
    original_summary_profile_id = asyncio.run(
        _summary_assignment_profile_id(async_session_factory)
    )

    try:
        connection = e2e_client.post(
            "/api/admin/ai/connections",
            headers=platform_admin.headers,
            json={
                "name": f"trusted-dispatch-connection-{suffix}",
                "provider": "openai_compatible",
                "api_key": "fixture-key",
                "endpoint": "http://scheduler-fixtures:8080/v1",
            },
        )
        assert connection.status_code == 201, connection.text
        connection_id = UUID(connection.json()["id"])

        chat_profile = e2e_client.post(
            "/api/admin/ai/profiles",
            headers=platform_admin.headers,
            json={
                "name": f"fixture-chat-{suffix}",
                "connection_id": str(connection_id),
                "model": "fixture-chat",
            },
        )
        assert chat_profile.status_code == 201, chat_profile.text
        profile_ids.append(UUID(chat_profile.json()["id"]))

        summary_profile = e2e_client.post(
            "/api/admin/ai/profiles",
            headers=platform_admin.headers,
            json={
                "name": f"fixture-summary-{suffix}",
                "connection_id": str(connection_id),
                "model": "fixture-summary",
            },
        )
        assert summary_profile.status_code == 201, summary_profile.text
        profile_ids.append(UUID(summary_profile.json()["id"]))
        asyncio.run(_force_chat_completions(async_session_factory, profile_ids))

        assigned = e2e_client.put(
            "/api/admin/ai/assignments/summarization",
            headers=platform_admin.headers,
            json={"profile_id": str(profile_ids[1])},
        )
        assert assigned.status_code == 200, assigned.text

        agent_name = f"trusted-dispatch-agent-{suffix}"
        agent = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": agent_name,
                "description": "Dispatch process fixture agent",
                "system_prompt": "Reply with exactly ok.",
                "access_level": "authenticated",
                "llm_profile_id": str(profile_ids[0]),
            },
        )
        assert agent.status_code == 201, agent.text
        agent_id = UUID(agent.json()["id"])

        submitted_at = time.perf_counter()
        enqueued = e2e_client.post(
            "/api/agent-runs/enqueue",
            headers=platform_admin.headers,
            json={"agent_name": agent_name, "input": {"fixture": "dispatch"}},
        )
        assert enqueued.status_code == 202, enqueued.text
        run_id = UUID(enqueued.json()["run_id"])

        def completed_with_summary() -> dict | None:
            response = e2e_client.get(
                f"/api/agent-runs/{run_id}", headers=platform_admin.headers
            )
            if response.status_code != 200:
                return None
            run = response.json()
            assert run["status"] not in {"failed", "cancelled"}, run
            assert run["summary_status"] != "failed", run
            if run["status"] == "completed" and run["summary_status"] == "completed":
                return run
            return None

        run = poll_until(completed_with_summary, max_wait=45, interval=0.2)
        assert run is not None, "agent run and automatic summary did not complete"
        assert run["output"] == {"text": "ok"}
        assert run["asked"] == "Run the fixture chat agent."
        assert run["did"] == "Called the fixture chat model."
        assert run["answered"] == "ok"
        assert run["confidence"] == 0.9
        assert run["metadata"]["fixture"] == "summary"
        assert run["metadata"]["agent_execution_mode"] in {"delegated", "automatic"}

        step_count, usage_count, stage_timings = asyncio.run(
            _runtime_evidence(async_session_factory, run_id)
        )
        assert step_count > 0
        assert usage_count == 2
        print("TRUSTED_DISPATCH_BENCHMARK " + json.dumps({
            **stage_timings,
            "enqueue_to_summary_seconds": round(time.perf_counter() - submitted_at, 3),
            "agent_execution_seconds": round((
                datetime.fromisoformat(run["completed_at"])
                - datetime.fromisoformat(run["started_at"])
            ).total_seconds(), 3),
        }, sort_keys=True))
    finally:
        asyncio.run(
            _cleanup_fixture(
                async_session_factory,
                original_summary_profile_id=original_summary_profile_id,
                connection_id=connection_id,
                profile_ids=profile_ids,
                agent_id=agent_id,
                run_id=run_id,
            )
        )
