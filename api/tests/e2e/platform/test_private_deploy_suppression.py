"""Private Builder deploys must not create shared control-plane side effects."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from src.models.orm.agents import Agent, AgentRole
from src.models.orm.events import EventSource, ScheduleSource
from src.models.orm.solutions import Solution
from src.models.orm.users import Role
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    solution_entity_id,
)
from src.services.solutions.private_deploy_guard import (
    PrivateDeployPolicy,
    PrivateDeployViolation,
    is_private_install,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


async def _make_solution(db, prefix: str, *, visibility: str) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"{prefix}-{uuid.uuid4().hex[:8]}",
        name=f"Private Deploy Suppression ({prefix})",
        organization_id=None,
        visibility=visibility,
    )
    db.add(sol)
    await db.flush()
    return sol


WF_ID = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
SUB_ID = "33333333-3333-3333-3333-333333333333"


def _bundle(sol: Solution, role_name: str) -> SolutionBundle:
    return SolutionBundle(
        solution=sol,
        version="0.1.0",
        python_files={"workflows/w.py": "def run():\n    return 1\n"},
        workflows=[
            {
                "id": WF_ID,
                "name": "suppression-probe",
                "path": "workflows/w.py",
                "function_name": "run",
                "role_names": [role_name],
            }
        ],
        events=[
            {
                "id": EVENT_ID,
                "name": "nightly",
                "source_type": "schedule",
                "is_active": True,
                "cron_expression": "0 0 * * *",
                "timezone": "UTC",
                "subscriptions": [
                    {
                        "id": SUB_ID,
                        "target_type": "workflow",
                        "workflow_id": WF_ID,
                        "is_active": True,
                    }
                ],
            }
        ],
    )


async def _count(db, model, *where) -> int:
    return (
        await db.execute(select(func.count()).select_from(model).where(*where))
    ).scalar_one()


async def test_private_deploy_creates_no_role_no_junction_no_event(db_session):
    role_name = f"Suppressed Role {uuid.uuid4().hex[:8]}"
    sol = await _make_solution(db_session, "private", visibility="private")

    result = await SolutionDeployer(db_session).deploy(
        _bundle(sol, role_name), force=True
    )

    assert await _count(db_session, Role, Role.name == role_name) == 0
    assert result.roles_created == []
    assert result.roles_unresolved == [role_name]

    wf_id = solution_entity_id(sol.id, uuid.UUID(WF_ID))
    assert await _count(db_session, WorkflowRole, WorkflowRole.workflow_id == wf_id) == 0
    assert await _count(db_session, EventSource, EventSource.solution_id == sol.id) == 0
    event_id = solution_entity_id(sol.id, uuid.UUID(EVENT_ID))
    assert (
        await _count(db_session, ScheduleSource, ScheduleSource.event_source_id == event_id)
        == 0
    )

    wf = await db_session.get(Workflow, wf_id)
    assert wf is not None
    assert wf.is_active is False


async def test_shared_deploy_of_same_workspace_still_creates_everything(db_session):
    role_name = f"Shared Role {uuid.uuid4().hex[:8]}"
    sol = await _make_solution(db_session, "shared", visibility="shared")

    result = await SolutionDeployer(db_session).deploy(
        _bundle(sol, role_name), force=True
    )

    assert result.roles_created == [role_name]
    assert result.roles_unresolved == []
    assert await _count(db_session, Role, Role.name == role_name) == 1

    wf_id = solution_entity_id(sol.id, uuid.UUID(WF_ID))
    assert await _count(db_session, WorkflowRole, WorkflowRole.workflow_id == wf_id) == 1
    assert await _count(db_session, EventSource, EventSource.solution_id == sol.id) == 1
    event_id = solution_entity_id(sol.id, uuid.UUID(EVENT_ID))
    assert (
        await _count(db_session, ScheduleSource, ScheduleSource.event_source_id == event_id)
        == 1
    )

    wf = await db_session.get(Workflow, wf_id)
    assert wf is not None
    assert wf.is_active is True


async def test_private_deploy_maps_existing_role_but_still_binds_nothing(db_session):
    role_name = f"Preexisting Role {uuid.uuid4().hex[:8]}"
    db_session.add(Role(name=role_name, created_by="test"))
    await db_session.flush()

    sol = await _make_solution(db_session, "private-existing", visibility="private")
    result = await SolutionDeployer(db_session).deploy(
        _bundle(sol, role_name), force=True
    )

    assert result.roles_unresolved == []
    assert await _count(db_session, Role, Role.name == role_name) == 1
    wf_id = solution_entity_id(sol.id, uuid.UUID(WF_ID))
    assert await _count(db_session, WorkflowRole, WorkflowRole.workflow_id == wf_id) == 0


async def test_redeploy_after_flipping_private_clears_prior_shared_effects(db_session):
    role_name = f"Flip Role {uuid.uuid4().hex[:8]}"
    sol = await _make_solution(db_session, "flip", visibility="shared")

    await SolutionDeployer(db_session).deploy(_bundle(sol, role_name), force=True)
    wf_id = solution_entity_id(sol.id, uuid.UUID(WF_ID))
    assert await _count(db_session, WorkflowRole, WorkflowRole.workflow_id == wf_id) == 1
    assert await _count(db_session, EventSource, EventSource.solution_id == sol.id) == 1

    sol.visibility = "private"
    await db_session.flush()

    await SolutionDeployer(db_session).deploy(_bundle(sol, role_name), force=True)

    assert await _count(db_session, WorkflowRole, WorkflowRole.workflow_id == wf_id) == 0
    assert await _count(db_session, EventSource, EventSource.solution_id == sol.id) == 0
    wf = await db_session.get(Workflow, wf_id)
    assert wf is not None
    assert wf.is_active is False


async def test_private_agent_is_runtime_blocked(db_session):
    sol = await _make_solution(db_session, "private-agent", visibility="private")
    agent_manifest_id = "44444444-4444-4444-4444-444444444444"
    bundle = SolutionBundle(
        solution=sol,
        version="0.1.0",
        agents=[
            {
                "id": agent_manifest_id,
                "name": f"probe-{uuid.uuid4().hex[:8]}",
                "system_prompt": "You are a probe.",
                "role_names": [],
            }
        ],
    )
    await SolutionDeployer(db_session).deploy(bundle, force=True)

    agent_id = solution_entity_id(sol.id, uuid.UUID(agent_manifest_id))
    agent = await db_session.get(Agent, agent_id)
    assert agent is not None
    assert agent.is_active is False
    assert await _count(db_session, AgentRole, AgentRole.agent_id == agent_id) == 0


async def test_is_private_install_reads_persisted_visibility(db_session):
    private = await _make_solution(db_session, "probe-priv", visibility="private")
    shared = await _make_solution(db_session, "probe-shared", visibility="shared")

    assert await is_private_install(db_session, private.id) is True
    assert await is_private_install(db_session, shared.id) is False
    assert await is_private_install(db_session, uuid.uuid4()) is False


def test_policy_suppresses_only_private_preview_side_effects():
    shared = PrivateDeployPolicy(private=False)
    assert not shared.suppress_role_materialization
    assert not shared.suppress_entity_role_junctions
    assert not shared.suppress_event_activation
    assert not shared.suppress_connection_resolution
    assert not shared.suppress_shared_config_writes

    private = PrivateDeployPolicy(private=True)
    assert private.suppress_role_materialization
    assert private.suppress_entity_role_junctions
    assert private.suppress_event_activation
    assert private.suppress_connection_resolution
    assert private.suppress_shared_config_writes

    promotion = PrivateDeployPolicy(private=True, promotion=True)
    assert not promotion.suppress_role_materialization
    assert not promotion.suppress_entity_role_junctions
    assert promotion.suppress_event_activation
    assert promotion.suppress_shared_config_writes


async def test_violation_raises_when_suppression_is_bypassed(db_session):
    sol = await _make_solution(db_session, "violation", visibility="private")
    deployer = SolutionDeployer(db_session)
    deployer._policy = PrivateDeployPolicy(private=True)
    deployer._created_roles.add("Escalated Role")

    with pytest.raises(PrivateDeployViolation):
        await deployer._assert_no_shared_side_effects(
            SolutionBundle(solution=sol, version="0.1.0")
        )
