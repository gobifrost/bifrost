"""Durable fan-out: delegate_agents admits children + all-join, wakes once."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunJoin,
    AgentRunJoinMember,
)
from src.models.orm.agents import Agent, AgentDelegation
from src.services.agent_runtime import run_store
from src.services.agent_runtime.delegation import (
    notify_parent_of_completion,
    parse_fanout_args,
    ToolSuspendError,
)
from src.services.agent_runtime.resume import prepare_resume
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig

pytestmark = pytest.mark.asyncio


def _fanout_model(children, call_id="fanout-1"):
    def _fn(messages, info):
        answered = any(
            isinstance(message, ModelRequest)
            and any(
                isinstance(part, ToolReturnPart)
                and part.tool_call_id == call_id
                for part in message.parts
            )
            for message in messages
        )
        if not answered:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="delegate_agents",
                        args={"children": children},
                        tool_call_id=call_id,
                    )
                ],
                model_name="fake",
            )
        return ModelResponse(
            parts=[TextPart(content="Parent collected every child.")],
            model_name="fake",
        )

    return FunctionModel(_fn)


def _create_agent(e2e_client, platform_admin, name: str) -> dict:
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "Fan-out E2E agent",
            "system_prompt": "Complete delegated work.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": None,
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _llm_patches(model):
    return (
        patch(
            "src.services.execution.autonomous_agent_executor.create_agent_model",
            return_value=model,
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.get_llm_config",
            new_callable=AsyncMock,
            return_value=LLMConfig(
                provider="openai", model="test-parent", api_key="test-key"
            ),
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
        patch("src.jobs.rabbitmq.publish_message", new_callable=AsyncMock),
        patch(
            "src.services.execution.agent_run_service.publish_message",
            new_callable=AsyncMock,
        ),
    )


async def _load_agent(async_session_factory, agent_id: UUID) -> Agent:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.roles),
            )
            .where(Agent.id == agent_id)
        )
        return result.scalar_one()


async def _finish_child(async_session_factory, child_id, status, output, error=None):
    async with async_session_factory() as session:
        claimed = await run_store.claim_run(session, child_id, "worker-kid")
        assert claimed.lease_token is not None
        await run_store.finish_run(
            session,
            child_id,
            claimed.lease_token,
            status,
            output=output,
            error=error,
        )


class TestFanoutValidation:
    def test_rejects_empty_and_oversized_requests(self):
        with pytest.raises(ToolSuspendError):
            parse_fanout_args({"children": []})
        with pytest.raises(ToolSuspendError):
            parse_fanout_args(
                {"children": [{"agent": "x", "task": "y"}] * 11}
            )

    def test_rejects_unenabled_modes_and_policies(self):
        children = [{"agent": "x", "task": "y"}]
        with pytest.raises(ToolSuspendError, match="all"):
            parse_fanout_args({"children": children, "join": {"mode": "any"}})
        with pytest.raises(ToolSuspendError, match="collect"):
            parse_fanout_args(
                {"children": children, "failure_policy": "fail-fast"}
            )

    def test_rejects_malformed_children(self):
        with pytest.raises(ToolSuspendError):
            parse_fanout_args({"children": [{"agent": "x"}]})
        with pytest.raises(ToolSuspendError):
            parse_fanout_args({"children": ["nope"]})

    def test_accepts_bounded_all_join(self):
        specs, mode = parse_fanout_args(
            {
                "children": [
                    {"agent": "a", "task": "one"},
                    {
                        "agent": "b",
                        "task": "two",
                        "output_schema": {"type": "object"},
                    },
                ]
            }
        )
        assert mode == "all"
        assert [s.position for s in specs] == [0, 1]
        assert specs[1].output_schema == {"type": "object"}


class TestFanoutJoin:
    async def test_all_join_orders_mixed_outcomes_and_resumes_parent(
        self, e2e_client, platform_admin, db_session, async_session_factory
    ):
        child_a = _create_agent(
            e2e_client, platform_admin, f"Fanout A {uuid4().hex[:8]}"
        )
        child_b = _create_agent(
            e2e_client, platform_admin, f"Fanout B {uuid4().hex[:8]}"
        )
        parent = _create_agent(
            e2e_client, platform_admin, f"Fanout Parent {uuid4().hex[:8]}"
        )
        try:
            db_session.add_all(
                [
                    AgentDelegation(
                        parent_agent_id=UUID(parent["id"]),
                        child_agent_id=UUID(child_a["id"]),
                    ),
                    AgentDelegation(
                        parent_agent_id=UUID(parent["id"]),
                        child_agent_id=UUID(child_b["id"]),
                    ),
                ]
            )
            parent_run = AgentRun(
                agent_id=UUID(parent["id"]),
                trigger_type="manual",
                status="queued",
                input={"task": "Fan out."},
            )
            db_session.add(parent_run)
            await db_session.commit()
            parent_id = parent_run.id

            async with async_session_factory() as session:
                claimed = await run_store.claim_run(
                    session, parent_id, "worker-a"
                )
                lease_token = claimed.lease_token
                assert lease_token is not None
            agent = await _load_agent(async_session_factory, UUID(parent["id"]))

            children = [
                {
                    "agent": child_b["name"],
                    "task": "Second task.",
                    "output_schema": {"type": "object"},
                },
                {"agent": child_a["name"], "task": "First task."},
            ]
            executor = AutonomousAgentExecutor(async_session_factory)
            patches = _llm_patches(_fanout_model(children))
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
            ):
                suspended = await executor.run(
                    agent,
                    input_data={"task": "Fan out."},
                    run_id=str(parent_id),
                    lease_token=lease_token,
                )
            assert suspended["status"] == "suspended"
            join_id = UUID(suspended["suspended"]["join_id"])
            child_ids = [
                UUID(raw) for raw in suspended["suspended"]["child_run_ids"]
            ]
            assert len(child_ids) == 2

            async with async_session_factory() as session:
                parent_row = await session.get(AgentRun, parent_id)
                assert parent_row is not None
                assert parent_row.status == "waiting_children"
                join = await session.get(AgentRunJoin, join_id)
                assert join is not None
                assert join.mode == "all"
                assert join.status == "pending"
                members = (
                    await session.execute(
                        select(AgentRunJoinMember).where(
                            AgentRunJoinMember.join_id == join_id
                        )
                    )
                ).scalars().all()
                assert sorted(m.position for m in members) == [0, 1]
                first_child = await session.get(AgentRun, child_ids[0])
                assert first_child is not None
                assert first_child.output_schema == {"type": "object"}
                assert first_child.root_run_id == parent_id

            # First completion updates its member but does not wake yet.
            await _finish_child(
                async_session_factory,
                child_ids[0],
                "completed",
                {"text": "second done"},
            )
            assert await notify_parent_of_completion(
                async_session_factory, child_ids[0]
            ) is False
            async with async_session_factory() as session:
                still_waiting = await session.get(AgentRun, parent_id)
                assert still_waiting is not None
                assert still_waiting.status == "waiting_children"

            # Last terminal child stores the ordered aggregate and wakes once.
            await _finish_child(
                async_session_factory,
                child_ids[1],
                "failed",
                None,
                error="boom",
            )
            assert await notify_parent_of_completion(
                async_session_factory, child_ids[1]
            ) is True
            assert await notify_parent_of_completion(
                async_session_factory, child_ids[1]
            ) is False

            async with async_session_factory() as session:
                join = await session.get(AgentRunJoin, join_id)
                assert join is not None
                assert join.status == "complete"
                aggregate = join.result
                assert aggregate["mode"] == "all"
                assert [c["position"] for c in aggregate["children"]] == [0, 1]
                assert aggregate["children"][0]["status"] == "completed"
                assert aggregate["children"][0]["output"] == {
                    "text": "second done"
                }
                assert aggregate["children"][1]["status"] == "failed"
                assert aggregate["children"][1]["error"] == "boom"

            async with async_session_factory() as session:
                resumed_claim = await run_store.claim_run(
                    session, parent_id, "worker-c"
                )
                resume_token = resumed_claim.lease_token
                assert resume_token is not None
            plan, _, unrecoverable = await prepare_resume(
                async_session_factory, parent_id, resume_token
            )
            assert unrecoverable is None
            assert plan is not None
            assert set(plan.deferred_results) == {"fanout-1"}
            delivered = json.loads(plan.deferred_results["fanout-1"])
            assert [c["position"] for c in delivered["children"]] == [0, 1]

            resumed = AutonomousAgentExecutor(async_session_factory)
            patches = _llm_patches(_fanout_model(children))
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
            ):
                result = await resumed.run(
                    agent,
                    input_data={"task": "Fan out."},
                    run_id=str(parent_id),
                    lease_token=resume_token,
                    resume_history=plan.history or None,
                    deferred_results=plan.deferred_results or None,
                )
            assert result["status"] == "completed"
            assert result["output"] == "Parent collected every child."
        finally:
            for agent_doc in (parent, child_a, child_b):
                e2e_client.delete(
                    f"/api/agents/{agent_doc['id']}",
                    headers=platform_admin.headers,
                )

    @pytest.mark.asyncio
    async def test_fanout_rejects_unauthorized_delegate(
        self, e2e_client, platform_admin, db_session, async_session_factory
    ):
        """Unknown delegates fail the run closed with no children admitted."""
        child = _create_agent(
            e2e_client, platform_admin, f"Fanout Solo {uuid4().hex[:8]}"
        )
        parent = _create_agent(
            e2e_client, platform_admin, f"Fanout Gate {uuid4().hex[:8]}"
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
                input={"task": "Fan out."},
            )
            db_session.add(parent_run)
            await db_session.commit()
            parent_id = parent_run.id

            async with async_session_factory() as session:
                claimed = await run_store.claim_run(
                    session, parent_id, "worker-a"
                )
                lease_token = claimed.lease_token
                assert lease_token is not None
            agent = await _load_agent(async_session_factory, UUID(parent["id"]))

            executor = AutonomousAgentExecutor(async_session_factory)
            patches = _llm_patches(_fanout_model([{"agent": "Nobody", "task": "x"}]))
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
            ):
                result = await executor.run(
                    agent,
                    input_data={"task": "Fan out."},
                    run_id=str(parent_id),
                    lease_token=lease_token,
                )
            assert result["status"] == "failed"
            assert "not an active grant" in (result.get("error") or "")

            async with async_session_factory() as session:
                orphans = (
                    await session.execute(
                        select(AgentRun).where(
                            AgentRun.parent_run_id == parent_id
                        )
                    )
                ).scalars().all()
                assert orphans == []
                joins = (
                    await session.execute(
                        select(AgentRunJoin).where(
                            AgentRunJoin.parent_run_id == parent_id
                        )
                    )
                ).scalars().all()
                assert joins == []
        finally:
            for agent_doc in (parent, child):
                e2e_client.delete(
                    f"/api/agents/{agent_doc['id']}",
                    headers=platform_admin.headers,
                )

    @pytest.mark.asyncio
    async def test_oversized_fanout_fails_closed_without_side_effects(
        self, e2e_client, platform_admin, db_session, async_session_factory
    ):
        """Over-limit requests become model-visible tool errors: no children."""
        child = _create_agent(
            e2e_client, platform_admin, f"Fanout Cap {uuid4().hex[:8]}"
        )
        parent = _create_agent(
            e2e_client, platform_admin, f"Fanout Cap P {uuid4().hex[:8]}"
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
                input={"task": "Fan out."},
            )
            db_session.add(parent_run)
            await db_session.commit()
            parent_id = parent_run.id

            async with async_session_factory() as session:
                claimed = await run_store.claim_run(
                    session, parent_id, "worker-a"
                )
                lease_token = claimed.lease_token
                assert lease_token is not None
            agent = await _load_agent(async_session_factory, UUID(parent["id"]))

            oversized = [
                {"agent": child["name"], "task": f"task {i}"} for i in range(11)
            ]
            executor = AutonomousAgentExecutor(async_session_factory)
            patches = _llm_patches(_fanout_model(oversized))
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
            ):
                result = await executor.run(
                    agent,
                    input_data={"task": "Fan out."},
                    run_id=str(parent_id),
                    lease_token=lease_token,
                )
            # The limit violation surfaces as a tool error; the model finishes
            # without any child admitted.
            assert result["status"] == "completed"
            async with async_session_factory() as session:
                orphans = (
                    await session.execute(
                        select(AgentRun).where(
                            AgentRun.parent_run_id == parent_id
                        )
                    )
                ).scalars().all()
                assert orphans == []
        finally:
            for agent_doc in (parent, child):
                e2e_client.delete(
                    f"/api/agents/{agent_doc['id']}",
                    headers=platform_admin.headers,
                )

    async def test_fanout_rejects_cycles(
        self, e2e_client, platform_admin, db_session, async_session_factory
    ):
        parent = _create_agent(
            e2e_client, platform_admin, f"Fanout Self {uuid4().hex[:8]}"
        )
        try:
            db_session.add(
                AgentDelegation(
                    parent_agent_id=UUID(parent["id"]),
                    child_agent_id=UUID(parent["id"]),
                )
            )
            parent_run = AgentRun(
                agent_id=UUID(parent["id"]),
                trigger_type="manual",
                status="queued",
                input={"task": "Fan out."},
            )
            db_session.add(parent_run)
            await db_session.commit()
            parent_id = parent_run.id

            async with async_session_factory() as session:
                claimed = await run_store.claim_run(
                    session, parent_id, "worker-a"
                )
                lease_token = claimed.lease_token
                assert lease_token is not None
            agent = await _load_agent(async_session_factory, UUID(parent["id"]))

            executor = AutonomousAgentExecutor(async_session_factory)
            patches = _llm_patches(_fanout_model([{"agent": parent["name"], "task": "x"}]))
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
            ):
                result = await executor.run(
                    agent,
                    input_data={"task": "Fan out."},
                    run_id=str(parent_id),
                    lease_token=lease_token,
                )
            assert result["status"] == "failed"
            assert "cycle" in (result.get("error") or "")
        finally:
            e2e_client.delete(
                f"/api/agents/{parent['id']}",
                headers=platform_admin.headers,
            )
