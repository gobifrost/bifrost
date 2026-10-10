"""E2E: ``run_as`` on ``POST /api/agent-runs/enqueue``.

A Contoso person holding Impersonate Users at Contoso launches a Contoso
agent as a Contoso colleague; a Contoso person without the permission and a
Fabrikam target are refused. Nothing here waits for a launched run to finish:
the agent's ``max_run_timeout`` is small and only the queued row is read.

The last test runs an agent in process as the colleague and lets one workflow
tool run on the worker, to show the tool acts as the colleague for the
initiator.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from sqlalchemy import func, select

from tests.e2e.conftest import write_and_register
from tests.e2e.fixtures.run_as import RUN_AS_DENIED, create_person, ok, query, recorded_run_as
from tests.e2e.fixtures.users import E2EUser


pytestmark = pytest.mark.e2e

@pytest.fixture(scope="module")
def agent_run_as_world(e2e_client, platform_admin):
    """A Contoso person holding Impersonate Users at Contoso, a Contoso person
    without it, a Contoso colleague, a Fabrikam person, and a Contoso agent."""
    tag = uuid4().hex[:8]
    admin = platform_admin.headers
    contoso = ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Contoso-{tag}"}), 201)
    fabrikam = ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Fabrikam-{tag}"}), 201)
    role = ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Contoso Agent Impersonation {tag}"}), 201)
    ok(
        e2e_client.put(
            f"/api/roles/{role['id']}/permissions",
            headers=admin,
            json={"permissions": ["users.impersonate"]},
        )
    )
    initiator = create_person(
        e2e_client,
        admin,
        contoso,
        tag,
        "initiator",
        additional=[{"role_id": role["id"], "boundaries": [{"kind": "organization", "organization_id": contoso["id"]}]}],
    )
    ungranted = create_person(e2e_client, admin, contoso, tag, "ungranted", additional=[])
    colleague = create_person(e2e_client, admin, contoso, tag, "colleague", additional=[])
    outsider = create_person(e2e_client, admin, fabrikam, tag, "outsider", additional=[])
    agent = ok(
        e2e_client.post(
            "/api/agents",
            headers=admin,
            json={
                "name": f"Contoso Run As Agent {tag}",
                "description": "Agent run_as test",
                "system_prompt": "Return a short acknowledgement.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": contoso["id"],
                "max_run_timeout": 5,
            },
        ),
        201,
    )

    yield {
        "initiator": initiator,
        "ungranted": ungranted,
        "colleague": colleague,
        "outsider": outsider,
        "agent": agent,
        "contoso": contoso,
    }

    e2e_client.delete(f"/api/agents/{agent['id']}", headers=admin)
    for person in (initiator, ungranted, colleague, outsider):
        e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
    e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
    e2e_client.delete(f"/api/organizations/{contoso['id']}", headers=admin)
    e2e_client.delete(f"/api/organizations/{fabrikam['id']}", headers=admin)


def _enqueue(e2e_client, person: E2EUser, agent: dict, run_as: UUID):
    return e2e_client.post(
        "/api/agent-runs/enqueue",
        headers=person.headers,
        json={"agent_name": agent["name"], "input": {"ticket_id": 42}, "run_as": str(run_as)},
    )


def _runs_of(session_factory, agent_id: str) -> int:
    from src.models.orm.agent_runs import AgentRun

    rows = asyncio.run(query(session_factory, select(func.count()).where(AgentRun.agent_id == UUID(agent_id))))
    return rows[0][0]


def test_a_person_without_impersonate_users_is_refused_and_nothing_is_queued(
    e2e_client, agent_run_as_world, async_session_factory
):
    world = agent_run_as_world
    runs_before = _runs_of(async_session_factory, world["agent"]["id"])

    resp = _enqueue(e2e_client, world["ungranted"], world["agent"], world["colleague"].user_id)

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail == RUN_AS_DENIED
    runs_after = _runs_of(async_session_factory, world["agent"]["id"])
    assert runs_after == runs_before


def test_impersonate_users_runs_an_agent_as_a_user_in_its_organization(
    e2e_client, agent_run_as_world, async_session_factory
):
    world = agent_run_as_world
    initiator, colleague = world["initiator"], world["colleague"]

    accepted = _enqueue(e2e_client, initiator, world["agent"], colleague.user_id)

    receipt = ok(accepted, 202)
    run = ok(e2e_client.get(f"/api/agent-runs/{receipt['run_id']}", headers=initiator.headers))
    acting_user = str(colleague.user_id)
    shown = (run["run_as_user_id"], run["run_as_user_name"], run["org_id"], run["caller_user_id"])
    expected = (acting_user, colleague.name, world["contoso"]["id"], str(initiator.user_id))
    assert receipt["run_as_user_id"] == acting_user
    assert shown == expected

    checks = recorded_run_as(async_session_factory, initiator.user_id, colleague.user_id)
    summary = [(c["outcome"], str(c["organization_id"]), c["details"]["enforced"]) for c in checks]
    assert summary == [("success", world["contoso"]["id"], True)]


def test_impersonate_users_is_refused_for_a_user_outside_its_organization(e2e_client, agent_run_as_world):
    world = agent_run_as_world

    resp = _enqueue(e2e_client, world["initiator"], world["agent"], world["outsider"].user_id)

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail == RUN_AS_DENIED


class _CallsTheToolOnce(TestModel):
    """Call the agent's one tool, then answer."""

    def _request(self, messages, model_settings, model_request_parameters):
        del model_settings
        done = any(
            isinstance(m, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in m.parts)
            for m in messages
        )
        if done:
            return ModelResponse(parts=[TextPart("done")], model_name=self.model_name)
        [tool] = model_request_parameters.function_tools
        return ModelResponse(
            parts=[ToolCallPart(tool.name, {}, tool_call_id="whoami")],
            model_name=self.model_name,
        )


def _reset_loop_singletons() -> None:
    """App-side DB, Redis and RabbitMQ clients pin to the loop that made them."""
    import src.core.redis_client as redis_module
    from src.core.database import reset_db_state
    from src.jobs.rabbitmq import rabbitmq

    reset_db_state()
    redis_module._redis_client = None
    rabbitmq.reset_pools()


async def _run_agent_tool_as(session_factory, agent_id: str, initiator: E2EUser, target: UUID, org_id: UUID) -> dict:
    """Run the agent in process as ``target``; its tool runs on the worker."""
    from sqlalchemy.orm import selectinload

    from src.core.database import close_db
    from src.jobs.consumers.agent_run import _load_run_as
    from src.models.orm.agents import Agent
    from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
    from src.services.llm.base import LLMConfig

    _reset_loop_singletons()
    try:
        async with session_factory() as session:
            agent = (
                await session.execute(
                    select(Agent)
                    .options(selectinload(Agent.tools), selectinload(Agent.delegated_agents))
                    .where(Agent.id == UUID(agent_id))
                )
            ).scalar_one()
            run_as = await _load_run_as(session, target, org_id)
        executor = AutonomousAgentExecutor(session_factory)
        with (
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new_callable=AsyncMock,
                return_value=[LLMConfig(provider="openai", model="test-run-as", api_key="test-key")],
            ),
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                return_value=_CallsTheToolOnce(model_name="test-run-as"),
            ),
        ):
            await executor.run(
                agent=agent,
                input_data={"ticket_id": 42},
                run_id=str(uuid4()),
                _caller={
                    "user_id": str(initiator.user_id),
                    "email": initiator.email,
                    "name": initiator.name,
                    "organization_id": str(org_id),
                },
                run_user_id=initiator.user_id,
                run_as=run_as,
            )
        return next(step["content"] for step in executor._pending_steps if step["type"] == "tool_result")
    finally:
        await close_db()
        _reset_loop_singletons()


def test_an_agent_tool_runs_as_the_run_as_user_for_the_initiator(
    e2e_client, platform_admin, agent_run_as_world, async_session_factory
):
    from src.models.orm.executions import Execution

    world = agent_run_as_world
    initiator, colleague = world["initiator"], world["colleague"]
    contoso_id = world["contoso"]["id"]
    admin = platform_admin.headers
    tag = uuid4().hex[:8]
    name = f"contoso_whoami_{tag}"
    path = f"agent_run_as_{tag}/whoami.py"
    source = f"""from bifrost import context, workflow


@workflow(name={name!r}, description="Says who it runs as", is_tool=True)
async def {name}():
    return {{"user_id": str(context.user_id)}}
"""
    workflow = write_and_register(e2e_client, admin, path, source, name, organization_id=contoso_id)
    agent = ok(
        e2e_client.post(
            "/api/agents",
            headers=admin,
            json={
                "name": f"Contoso Run As Tool Agent {tag}",
                "description": "Agent run_as tool test",
                "system_prompt": "Call the tool.",
                "channels": ["chat"],
                "tool_ids": [workflow["id"]],
                "access_level": "authenticated",
                "organization_id": contoso_id,
            },
        ),
        201,
    )
    try:
        tool_result = asyncio.run(
            _run_agent_tool_as(async_session_factory, agent["id"], initiator, colleague.user_id, UUID(contoso_id))
        )

        rows = asyncio.run(
            query(
                async_session_factory,
                select(Execution.executed_by, Execution.run_user_id).where(
                    Execution.id == UUID(tool_result["execution_id"])
                ),
            )
        )
        assert tool_result["is_error"] is False, tool_result
        returned = json.loads(tool_result["result"])
        expected = {"user_id": str(colleague.user_id)}
        assert returned == expected
        # The tool acts as the colleague; the run is still the initiator's.
        recorded = [tuple(row) for row in rows]
        assert recorded == [(colleague.user_id, initiator.user_id)]
    finally:
        e2e_client.delete(f"/api/agents/{agent['id']}", headers=admin)
        e2e_client.delete(f"/api/files/editor?path={path}", headers=admin)
