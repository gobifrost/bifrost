"""Unit tests for the pending_captures capture→pull→deploy round-trip queue."""

import uuid

import pytest
from sqlalchemy import select

from src.models.enums import ConfigType
from src.models.orm.agents import Agent
from src.models.orm.config import Config
from src.models.orm.forms import Form
from src.models.orm.pending_capture import PendingCaptureORM
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.services.solutions.capture import (
    SolutionCaptureSelectors,
    SolutionCaptureService,
)
from src.services.solutions.pending import unpulled_blockers


def _empty_manifest_ids() -> dict[str, set[str]]:
    return {
        "table": set(),
        "form": set(),
        "agent": set(),
        "config": set(),
        "event": set(),
        "claim": set(),
    }


def test_unpulled_capture_blocks():
    # one pending form, manifest does NOT contain it → blocker
    blockers = unpulled_blockers([("form", "form-1")], _empty_manifest_ids())
    assert blockers == [("form", "form-1")]


def test_pulled_then_removed_is_not_blocked():
    # genuine delete: entity absent from manifest AND no pending row
    assert unpulled_blockers([], _empty_manifest_ids()) == []


def test_pending_present_in_manifest_clears():
    # captured AND now in the manifest (pulled) → not a blocker
    manifest = _empty_manifest_ids()
    manifest["form"] = {"form-1"}
    assert unpulled_blockers([("form", "form-1")], manifest) == []


async def _make_solution(db_session, slug: str = "test-sol") -> Solution:
    sol = Solution(slug=f"{slug}-{uuid.uuid4().hex[:8]}", name="Test Solution")
    db_session.add(sol)
    await db_session.commit()
    await db_session.refresh(sol)
    return sol


@pytest.mark.asyncio
async def test_pending_capture_row_roundtrips(db_session):
    sol = await _make_solution(db_session)
    sol_id = sol.id
    row = PendingCaptureORM(
        solution_id=sol_id,
        entity_type="form",
        entity_id="abc-123",
        captured_by=None,
    )
    db_session.add(row)
    await db_session.commit()

    got = (
        await db_session.execute(
            select(PendingCaptureORM).where(PendingCaptureORM.solution_id == sol_id)
        )
    ).scalars().all()
    assert len(got) == 1
    assert got[0].entity_type == "form"
    assert got[0].entity_id == "abc-123"
    assert got[0].captured_at is not None


async def _make_loose_entities(db_session):
    """Create a loose global table + form + agent + config (solution scope)."""
    table = Table(
        id=uuid.uuid4(),
        name=f"docs_{uuid.uuid4().hex[:8]}",
        organization_id=None,
        solution_id=None,
        schema={"columns": [{"name": "title", "type": "string"}]},
    )
    form = Form(
        id=uuid.uuid4(),
        name="intake",
        organization_id=None,
        solution_id=None,
        created_by="test",
    )
    agent = Agent(
        id=uuid.uuid4(),
        name="helper",
        system_prompt="hi",
        organization_id=None,
        solution_id=None,
        created_by="test",
    )
    config = Config(
        id=uuid.uuid4(),
        key=f"API_KEY_{uuid.uuid4().hex[:8].upper()}",
        value={"value": "secret"},
        config_type=ConfigType.SECRET,
        organization_id=None,
        updated_by="test",
    )
    db_session.add_all([table, form, agent, config])
    await db_session.commit()
    return table, form, agent, config


def _selectors(*, tables=(), forms=(), agents=(), configs=()):
    return SolutionCaptureSelectors(
        workflows=[],
        tables=list(tables),
        apps=[],
        forms=list(forms),
        agents=list(agents),
        claims=[],
        configs=list(configs),
        events=[],
    )


@pytest.mark.asyncio
async def test_capture_enqueues_one_row_per_entity(db_session):
    sol = await _make_solution(db_session)
    table, form, agent, config = await _make_loose_entities(db_session)
    selectors = _selectors(
        tables=[table.id], forms=[form.id], agents=[agent.id], configs=[config.key]
    )

    await SolutionCaptureService(db_session).capture(sol, selectors)
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(PendingCaptureORM).where(PendingCaptureORM.solution_id == sol.id)
        )
    ).scalars().all()
    keyset = {(r.entity_type, r.entity_id) for r in rows}
    assert ("table", str(table.id)) in keyset
    assert ("form", str(form.id)) in keyset
    assert ("agent", str(agent.id)) in keyset
    assert ("config", config.key) in keyset

    # Re-capture is idempotent (UNIQUE constraint) — no duplicate rows.
    await SolutionCaptureService(db_session).capture(sol, selectors)
    await db_session.commit()
    rows2 = (
        await db_session.execute(
            select(PendingCaptureORM).where(PendingCaptureORM.solution_id == sol.id)
        )
    ).scalars().all()
    assert len(rows2) == len(rows)
