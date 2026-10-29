"""Agents can emit metadata during execution."""
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.agent_runs import AgentRun
from src.services.ai_behavior_service import AGENT_EXECUTION_MODE_METADATA_KEY
from src.services.execution.run_metadata import (
    TooManyMetadataKeys,
    set_run_metadata,
)


@pytest_asyncio.fixture
async def seed_completed_run(db_session, seed_agent):
    """A completed run row visible across sessions (committed)."""
    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=10,
        summary_status="pending",
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    # Manual cleanup since we committed past the rollback boundary.
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_set_metadata_persists(async_session_factory, seed_completed_run):
    await set_run_metadata(
        seed_completed_run.id,
        {
            "ticket_id": "4821",
            "severity": "high",
            AGENT_EXECUTION_MODE_METADATA_KEY: "automatic",
        },
        session_factory=async_session_factory,
    )
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.run_metadata == {"ticket_id": "4821", "severity": "high"}


@pytest.mark.asyncio
async def test_set_metadata_cap_at_16_keys(async_session_factory, seed_completed_run):
    with pytest.raises(TooManyMetadataKeys):
        await set_run_metadata(
            seed_completed_run.id,
            {f"k{i}": "v" for i in range(17)},
            session_factory=async_session_factory,
        )


@pytest.mark.asyncio
async def test_set_metadata_value_length_capped(
    async_session_factory, seed_completed_run
):
    await set_run_metadata(
        seed_completed_run.id,
        {"foo": "x" * 1000},
        session_factory=async_session_factory,
    )
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert len(run.run_metadata["foo"]) == 256


@pytest.mark.asyncio
async def test_set_metadata_key_length_capped(
    async_session_factory, seed_completed_run
):
    long_key = "k" * 200
    await set_run_metadata(
        seed_completed_run.id,
        {long_key: "v"},
        session_factory=async_session_factory,
    )
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        only_key = next(iter(run.run_metadata.keys()))
        assert len(only_key) == 64


@pytest.mark.asyncio
async def test_set_metadata_preserves_pinned_execution_mode_and_reserves_its_slot(
    async_session_factory, seed_completed_run
):
    async with async_session_factory() as db:
        run = await db.get(AgentRun, seed_completed_run.id)
        assert run is not None
        run.run_metadata = {AGENT_EXECUTION_MODE_METADATA_KEY: "automatic"}
        await db.commit()

    await set_run_metadata(
        seed_completed_run.id,
        {
            AGENT_EXECUTION_MODE_METADATA_KEY: "delegated",
            **{f"key_{index}": str(index) for index in range(15)},
        },
        session_factory=async_session_factory,
    )

    async with async_session_factory() as db:
        run = await db.get(AgentRun, seed_completed_run.id)
        assert run is not None
        assert run.run_metadata[AGENT_EXECUTION_MODE_METADATA_KEY] == "automatic"
        assert len(run.run_metadata) == 16


@pytest.mark.asyncio
async def test_set_metadata_rejects_a_full_agent_map_when_mode_is_pinned(
    async_session_factory, seed_completed_run
):
    async with async_session_factory() as db:
        run = await db.get(AgentRun, seed_completed_run.id)
        assert run is not None
        run.run_metadata = {AGENT_EXECUTION_MODE_METADATA_KEY: "delegated"}
        await db.commit()

    with pytest.raises(TooManyMetadataKeys):
        await set_run_metadata(
            seed_completed_run.id,
            {f"key_{index}": str(index) for index in range(16)},
            session_factory=async_session_factory,
        )
