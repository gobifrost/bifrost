"""Agent-run response contracts expose the expected summary fields."""
from uuid import UUID, uuid4

import pytest

from shared.sdk_agent_runs import get_sdk_agent_run
from src.core.principal import UserPrincipal
from src.models.contracts.agent_runs import (
    AgentRunChildResponse,
    AgentRunDetailResponse,
    AgentRunResponse,
)
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.routers.agent_runs import list_agent_runs


def test_agent_run_response_has_new_fields():
    fields = AgentRunResponse.model_fields
    for name in (
        "asked", "did", "metadata", "confidence", "confidence_reason",
        "verdict", "verdict_note", "verdict_set_at", "verdict_set_by",
    ):
        assert name in fields, f"missing {name} on AgentRunResponse"


def test_agent_run_detail_response_inherits_new_fields():
    fields = AgentRunDetailResponse.model_fields
    for name in (
        "asked", "did", "metadata", "confidence",
        "verdict", "verdict_note",
    ):
        assert name in fields


def test_agent_run_detail_response_has_lean_child_summaries():
    assert "child_runs" in AgentRunDetailResponse.model_fields
    assert set(AgentRunChildResponse.model_fields) == {
        "id",
        "agent_id",
        "agent_name",
        "status",
        "asked",
        "did",
        "answered",
        "duration_ms",
        "created_at",
    }


def test_run_as_fields_on_list_and_detail_contracts():
    for model in (AgentRunResponse, AgentRunDetailResponse):
        fields = model.model_fields
        assert "run_as_user_id" in fields
        assert "run_as_user_name" in fields


def _superuser() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="admin@contoso.example",
        organization_id=None,
        name="Contoso Admin",
        is_superuser=True,
    )


async def _seed_run(db_session, *, run_as_user_id=None) -> AgentRun:
    agent = Agent(
        name=f"Contoso Helpdesk {uuid4().hex[:8]}",
        system_prompt="Answer helpdesk questions.",
        access_level=AgentAccessLevel.ROLE_BASED,
        created_by="run-as-test",
    )
    db_session.add(agent)
    await db_session.flush()
    run = AgentRun(
        agent_id=agent.id,
        trigger_type="api",
        status="completed",
        run_as_user_id=run_as_user_id,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.expunge_all()
    return run


async def _list_runs(db_session, agent_id: UUID):
    return await list_agent_runs(
        db=db_session,
        user=_superuser(),
        agent_id=agent_id,
        status_filter=None,
        trigger_type=None,
        org_id=None,
        start_date=None,
        end_date=None,
        q=None,
        verdict=None,
        metadata_filter=None,
        limit=50,
        offset=0,
        cursor=None,
    )


@pytest.mark.asyncio
async def test_ordinary_run_has_no_run_as_user(db_session):
    run = await _seed_run(db_session)

    detail = await get_sdk_agent_run(db_session, _superuser(), run_id=run.id)
    listed = await _list_runs(db_session, run.agent_id)

    assert detail.run_as_user_id is None
    assert detail.run_as_user_name is None
    assert [(i.run_as_user_id, i.run_as_user_name) for i in listed.items] == [(None, None)]


@pytest.mark.asyncio
async def test_run_as_user_is_named_from_the_users_row(db_session):
    fabrikam = Organization(name=f"Fabrikam {uuid4().hex[:8]}", created_by="run-as-test")
    db_session.add(fabrikam)
    await db_session.flush()
    target = User(
        email=f"{uuid4()}@fabrikam.example",
        name="Fabrikam Technician",
        organization_id=fabrikam.id,
    )
    db_session.add(target)
    await db_session.flush()
    run = await _seed_run(db_session, run_as_user_id=target.id)

    detail = await get_sdk_agent_run(db_session, _superuser(), run_id=run.id)
    listed = await _list_runs(db_session, run.agent_id)

    assert detail.run_as_user_id == target.id
    assert detail.run_as_user_name == "Fabrikam Technician"
    assert [(i.run_as_user_id, i.run_as_user_name) for i in listed.items] == [
        (target.id, "Fabrikam Technician")
    ]
