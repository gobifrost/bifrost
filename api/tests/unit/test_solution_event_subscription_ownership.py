"""Solution deploy manages only ITS subscriptions on a Solution-owned source.

A Solution's ``.bifrost/events.yaml`` full-replaces the subscriptions the
install manages (``solution_id == sid``). Subscriptions an operator adds to a
Solution-owned source from outside the Solution (``solution_id IS NULL`` — e.g.
a workspace workflow listening to the Solution's schedule/topic) must survive
every redeploy, stay out of the exported bundle, and not be adopted by a
re-capture of the source.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.events import EventSource, EventSubscription, ScheduleSource
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.capture import (
    SolutionCaptureSelectors,
    SolutionCaptureService,
)
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployConflict,
    SolutionDeployer,
    solution_entity_id,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    """Deploy's cache-sync binds the async Redis singleton to the first loop;
    drop the stale reference so each test rebinds (see
    test_solution_deploy_reconcile.py)."""
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.fixture(autouse=True)
def _write_guard():
    """Prod-faithful: the always-on read-only guard rejects ORM-object writes
    to managed rows, so deploy must stay on Core statements."""
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()


SOURCE_ID = str(UUID(int=0xE1))
WF_A = str(UUID(int=0xA1))
WF_B = str(UUID(int=0xB1))
SUB_A = str(UUID(int=0x5A))
SUB_B = str(UUID(int=0x5B))


async def _make_solution(db) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"subs-{uuid.uuid4().hex[:8]}",
        name="Subs",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()
    return sol


def _bundle(sol: Solution, subs: list[dict]) -> SolutionBundle:
    return SolutionBundle(
        solution=sol,
        python_files={
            "workflows/a.py": "def run():\n    return 'a'\n",
            "workflows/b.py": "def run():\n    return 'b'\n",
        },
        workflows=[
            {"id": WF_A, "name": "a", "function_name": "run",
             "path": "workflows/a.py", "type": "workflow"},
            {"id": WF_B, "name": "b", "function_name": "run",
             "path": "workflows/b.py", "type": "workflow"},
        ],
        events=[{
            "id": SOURCE_ID,
            "name": "nightly",
            "source_type": "schedule",
            "cron_expression": "0 9 * * *",
            "timezone": "UTC",
            "subscriptions": subs,
        }],
    )


async def _deployed_source_id(db, sol: Solution) -> UUID:
    return (await db.execute(
        select(EventSource.id).where(EventSource.solution_id == sol.id)
    )).scalar_one()


async def _subs(db, source_id: UUID) -> list[EventSubscription]:
    return list((await db.execute(
        select(EventSubscription)
        .where(EventSubscription.event_source_id == source_id)
        .execution_options(populate_existing=True)
    )).scalars().unique().all())


async def _add_external_sub(db, source_id: UUID) -> EventSubscription:
    """An operator's workspace workflow subscribed to the Solution's source."""
    wf = Workflow(
        id=uuid.uuid4(),
        name=f"workspace-listener-{uuid.uuid4().hex[:6]}",
        function_name="main",
        path="workflows/listener.py",
        type="workflow",
        is_active=True,
        solution_id=None,
    )
    db.add(wf)
    await db.flush()
    sub = EventSubscription(
        id=uuid.uuid4(),
        event_source_id=source_id,
        workflow_id=wf.id,
        target_type="workflow",
        solution_id=None,
        created_by="operator@example.com",
    )
    db.add(sub)
    await db.flush()
    return sub


async def test_external_subscription_survives_redeploy(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    deployer = SolutionDeployer(db)
    bundle = _bundle(sol, [{"id": SUB_A, "workflow_id": WF_A}])

    await deployer.deploy(bundle)
    await db.flush()
    source_id = await _deployed_source_id(db, sol)
    external = await _add_external_sub(db, source_id)

    await deployer.deploy(bundle)
    await db.flush()

    subs = await _subs(db, source_id)
    by_owner = {s.id: s.solution_id for s in subs}
    assert by_owner[external.id] is None  # operator's listener untouched
    managed = [s for s in subs if s.solution_id == sol.id]
    assert len(managed) == 1  # managed set replaced, not duplicated
    # Child schedule row stays full-replace (exactly one).
    schedules = (await db.execute(
        select(ScheduleSource).where(ScheduleSource.event_source_id == source_id)
    )).scalars().all()
    assert len(schedules) == 1


async def test_subscription_removed_from_manifest_is_deleted(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    deployer = SolutionDeployer(db)

    await deployer.deploy(_bundle(sol, [
        {"id": SUB_A, "workflow_id": WF_A},
        {"id": SUB_B, "workflow_id": WF_B},
    ]))
    await db.flush()
    source_id = await _deployed_source_id(db, sol)
    external = await _add_external_sub(db, source_id)

    await deployer.deploy(_bundle(sol, [{"id": SUB_A, "workflow_id": WF_A}]))
    await db.flush()

    ids = {s.id for s in await _subs(db, source_id)}
    assert ids == {solution_entity_id(sol.id, UUID(SUB_A)), external.id}


async def test_subscription_updated_in_manifest_is_updated_not_duplicated(
    db_session,
) -> None:
    db = db_session
    sol = await _make_solution(db)
    deployer = SolutionDeployer(db)

    await deployer.deploy(_bundle(sol, [
        {"id": SUB_A, "workflow_id": WF_A, "input_mapping": {"mode": "old"}},
    ]))
    await db.flush()
    source_id = await _deployed_source_id(db, sol)
    await _add_external_sub(db, source_id)

    await deployer.deploy(_bundle(sol, [
        {"id": SUB_A, "workflow_id": WF_A, "input_mapping": {"mode": "new"},
         "event_type": "nightly.run"},
    ]))
    await db.flush()

    managed = [s for s in await _subs(db, source_id) if s.solution_id == sol.id]
    assert len(managed) == 1
    assert managed[0].id == solution_entity_id(sol.id, UUID(SUB_A))
    assert managed[0].input_mapping == {"mode": "new"}
    assert managed[0].event_type == "nightly.run"


async def test_deploy_refuses_to_hijack_external_subscription_id(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    deployer = SolutionDeployer(db)
    await deployer.deploy(_bundle(sol, []))
    await db.flush()
    source_id = await _deployed_source_id(db, sol)
    external = await _add_external_sub(db, source_id)

    # A manifest sub whose install id would land on the external row's PK.
    from sqlalchemy import update

    colliding = solution_entity_id(sol.id, UUID(SUB_A))
    await db.execute(
        update(EventSubscription)
        .where(EventSubscription.id == external.id)
        .values(id=colliding)
    )
    await db.flush()

    with pytest.raises(SolutionDeployConflict):
        await deployer.deploy(_bundle(sol, [{"id": SUB_A, "workflow_id": WF_A}]))


async def test_bundle_and_recapture_leave_external_subscription_out(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    await SolutionDeployer(db).deploy(_bundle(sol, [{"id": SUB_A, "workflow_id": WF_A}]))
    await db.flush()
    source_id = await _deployed_source_id(db, sol)
    external = await _add_external_sub(db, source_id)

    # An unmanaged sub targeting the Solution's own workflow IS adopted by a
    # re-capture (the author is pulling a new trigger into the Solution).
    own_wf_id = (await db.execute(
        select(Workflow.id).where(Workflow.solution_id == sol.id, Workflow.name == "b")
    )).scalar_one()
    own_unmanaged = EventSubscription(
        id=uuid.uuid4(), event_source_id=source_id, workflow_id=own_wf_id,
        target_type="workflow", solution_id=None, created_by="author@example.com",
    )
    db.add(own_unmanaged)
    await db.flush()

    service = SolutionCaptureService(db)
    await service.capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[], apps=[], forms=[], agents=[], claims=[],
            configs=[], events=[source_id],
        ),
    )
    await db.flush()

    owners = {s.id: s.solution_id for s in await _subs(db, source_id)}
    assert owners[external.id] is None
    assert owners[own_unmanaged.id] == sol.id

    bundle = await service.bundle_for(sol)
    exported_ids = {
        sub["id"] for event in bundle.events for sub in event["subscriptions"]
    }
    assert str(external.id) not in exported_ids
    assert str(own_unmanaged.id) in exported_ids


async def _make_agent(db, *, solution_id) -> UUID:
    from src.models.orm.agents import Agent

    agent = Agent(
        id=uuid.uuid4(), name=f"agent-{uuid.uuid4().hex[:6]}",
        organization_id=None, solution_id=solution_id,
        system_prompt="help", created_by="test",
    )
    db.add(agent)
    await db.flush()
    return agent.id


async def _make_workflow(db, *, solution_id) -> UUID:
    wf = Workflow(
        id=uuid.uuid4(), name=f"wf-{uuid.uuid4().hex[:6]}", function_name="main",
        path=f"workflows/{uuid.uuid4().hex[:6]}.py", type="workflow",
        is_active=True, solution_id=solution_id,
    )
    db.add(wf)
    await db.flush()
    return wf.id


async def _add_unmanaged_sub(db, source_id, *, workflow_id=None, agent_id=None) -> UUID:
    sub = EventSubscription(
        id=uuid.uuid4(), event_source_id=source_id,
        workflow_id=workflow_id, agent_id=agent_id,
        target_type="agent" if agent_id else "workflow",
        solution_id=None, created_by="operator@example.com",
    )
    db.add(sub)
    await db.flush()
    return sub.id


def _events_selector(source_id: UUID) -> SolutionCaptureSelectors:
    return SolutionCaptureSelectors(
        workflows=[], tables=[], apps=[], forms=[], agents=[], claims=[],
        configs=[], events=[source_id],
    )


async def test_first_capture_leaves_external_listeners_external(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    source = EventSource(
        id=uuid.uuid4(), name="loose-topic", source_type="topic",
        organization_id=None, solution_id=None, created_by="test",
    )
    db.add(source)
    await db.flush()

    own_wf_sub = await _add_unmanaged_sub(
        db, source.id, workflow_id=await _make_workflow(db, solution_id=sol.id))
    own_agent_sub = await _add_unmanaged_sub(
        db, source.id, agent_id=await _make_agent(db, solution_id=sol.id))
    ext_wf_sub = await _add_unmanaged_sub(
        db, source.id, workflow_id=await _make_workflow(db, solution_id=None))
    ext_agent_sub = await _add_unmanaged_sub(
        db, source.id, agent_id=await _make_agent(db, solution_id=None))

    await SolutionCaptureService(db).capture(sol, _events_selector(source.id))
    await db.flush()

    owners = {s.id: s.solution_id for s in await _subs(db, source.id)}
    assert owners == {
        own_wf_sub: sol.id,
        own_agent_sub: sol.id,
        ext_wf_sub: None,
        ext_agent_sub: None,
    }


async def test_recapture_adopts_agent_subscription_to_solution_agent_only(
    db_session,
) -> None:
    db = db_session
    sol = await _make_solution(db)
    await SolutionDeployer(db).deploy(_bundle(sol, [{"id": SUB_A, "workflow_id": WF_A}]))
    await db.flush()
    source_id = await _deployed_source_id(db, sol)

    own_agent_sub = await _add_unmanaged_sub(
        db, source_id, agent_id=await _make_agent(db, solution_id=sol.id))
    ext_agent_sub = await _add_unmanaged_sub(
        db, source_id, agent_id=await _make_agent(db, solution_id=None))

    await SolutionCaptureService(db).capture(sol, _events_selector(source_id))
    await db.flush()

    owners = {s.id: s.solution_id for s in await _subs(db, source_id)}
    assert owners[own_agent_sub] == sol.id
    assert owners[ext_agent_sub] is None


async def test_source_removed_from_manifest_cascades_external_listeners(
    db_session,
) -> None:
    db = db_session
    sol = await _make_solution(db)
    deployer = SolutionDeployer(db)
    await deployer.deploy(_bundle(sol, [{"id": SUB_A, "workflow_id": WF_A}]))
    await db.flush()
    source_id = await _deployed_source_id(db, sol)
    external = await _add_external_sub(db, source_id)

    no_events = _bundle(sol, [])
    no_events.events = []
    await deployer.deploy(no_events)
    await db.flush()

    assert (await db.get(EventSource, source_id)) is None
    assert await _subs(db, source_id) == []
    remaining = (await db.execute(
        select(EventSubscription.id).where(EventSubscription.id == external.id)
    )).scalar_one_or_none()
    assert remaining is None  # cascaded with the source
