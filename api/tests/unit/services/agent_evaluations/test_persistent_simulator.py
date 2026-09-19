"""Persisted simulation state survives fresh routers and child call IDs."""

import json
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from src.models.orm.agent_evaluations import AgentSimulationSession, AgentSimulationToolRecord
from src.models.orm.agent_runs import AgentRun
from src.services.agent_evaluations.runner import PersistentSimulatorToolRouter
from src.services.agent_evaluations.simulator import SyntheticToolError


@pytest_asyncio.fixture
async def simulation(async_session_factory):
    root_id, child_id, session_id = uuid4(), uuid4(), uuid4()
    fixture = {
        "entities": {"ticket": {}},
        "allowed_tools": ["create_ticket", "unhandled_action"],
        "seed_time": "2026-09-18T00:00:00+00:00",
    }
    async with async_session_factory() as db:
        db.add(AgentRun(id=root_id, root_run_id=root_id, status="queued", trigger_type="evaluation_synthetic"))
        await db.flush()
        db.add(AgentRun(id=child_id, root_run_id=root_id, parent_run_id=root_id, status="queued", trigger_type="evaluation_synthetic"))
        await db.flush()
        db.add(AgentSimulationSession(
            id=session_id, root_run_id=root_id, run_id=root_id,
            case_version=1, fixture=fixture, state=fixture,
            tool_schemas={"create_ticket": {"type": "object"}},
        ))
        await db.commit()
    try:
        yield session_id, root_id, child_id
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(AgentSimulationSession).where(AgentSimulationSession.id == session_id))
            await db.execute(delete(AgentRun).where(AgentRun.id.in_([child_id, root_id])))
            await db.commit()


def router(factory, session_id, run_id):
    return PersistentSimulatorToolRouter(factory, session_id, run_id=run_id, correlation={})


@pytest.mark.asyncio
async def test_resumed_and_child_routers_preserve_state_and_idempotency(
    async_session_factory, simulation,
):
    session_id, root_id, child_id = simulation
    first = await router(async_session_factory, session_id, root_id).route(
        "create_ticket", {"title": "first"}, "provider-call-1",
    )
    second = await router(async_session_factory, session_id, child_id).route(
        "create_ticket", {"title": "child"}, "provider-call-1",
    )
    replayed = await router(async_session_factory, session_id, root_id).route(
        "create_ticket", {"title": "first"}, "provider-call-1",
    )
    assert replayed == first
    assert json.loads(second)["id"] != json.loads(first)["id"]
    async with async_session_factory() as db:
        state = (await db.get(AgentSimulationSession, session_id)).state
        assert len(state["entities"]["ticket"]) == 2
        assert state["clock_time"] == "2026-09-18T00:00:02+00:00"


@pytest.mark.asyncio
async def test_record_quota_cannot_reset_with_a_new_router(
    async_session_factory, simulation, monkeypatch,
):
    from src.services.agent_evaluations import quotas

    monkeypatch.setattr(quotas, "MAX_SIM_RECORDS_PER_RUN", 1)
    session_id, root_id, _ = simulation
    await router(async_session_factory, session_id, root_id).route("create_ticket", {}, "one")
    with pytest.raises(SyntheticToolError, match="exceeded"):
        await router(async_session_factory, session_id, root_id).route("create_ticket", {}, "two")
    async with async_session_factory() as db:
        state = (await db.get(AgentSimulationSession, session_id)).state
        assert len(state["entities"]["ticket"]) == 1
        assert await db.scalar(select(func.count()).select_from(AgentSimulationToolRecord).where(
            AgentSimulationToolRecord.session_id == session_id,
        )) == 1


@pytest.mark.asyncio
async def test_failed_operation_clock_survives_restart_and_failure_replay(
    async_session_factory, simulation,
):
    session_id, root_id, _ = simulation
    for _ in range(2):
        with pytest.raises(SyntheticToolError, match="no synthetic behavior"):
            await router(async_session_factory, session_id, root_id).route("unhandled_action", {}, "failed")
    await router(async_session_factory, session_id, root_id).route("create_ticket", {}, "next")
    async with async_session_factory() as db:
        saved = await db.get(AgentSimulationSession, session_id)
        assert saved.state["clock_time"] == "2026-09-18T00:00:02+00:00"
        assert saved.version == 2  # two unique operations; replay is read-only
        assert await db.scalar(select(func.count()).select_from(AgentSimulationToolRecord).where(
            AgentSimulationToolRecord.session_id == session_id,
        )) == 2


@pytest.mark.asyncio
async def test_timer_uses_shared_fixture_clock_and_replays_without_advancing(
    async_session_factory, simulation,
):
    session_id, root_id, child_id = simulation
    first = router(async_session_factory, session_id, root_id)
    wake = await first.validate_and_advance_timer(
        {"wake_at": "2026-09-18T00:01:00+00:00", "reason": "replication"}, max_seconds=120, tool_call_id="timer-1",
    )
    await router(async_session_factory, session_id, child_id).route("create_ticket", {}, "child-1")
    replay = await router(async_session_factory, session_id, root_id).validate_and_advance_timer(
        {"wake_at": "2026-09-18T00:01:00+00:00", "reason": "replication"}, max_seconds=120, tool_call_id="timer-1",
    )
    assert replay == wake
    async with async_session_factory() as db:
        saved = await db.get(AgentSimulationSession, session_id)
        assert saved.state["clock_time"] == "2026-09-18T00:01:01+00:00"
        assert saved.version == 2
