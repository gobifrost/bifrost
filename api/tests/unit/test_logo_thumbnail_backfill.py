"""Legacy logo backfill drains every entity kind, including Solution-managed rows.

The backfill writes with Core ``UPDATE`` so the always-on Solution write guard
(an ORM ``before_flush`` hook) is neither triggered nor loosened.
"""
from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import delete, select

from src.jobs.schedulers.logo_thumbnail_backfill import _FAILED_VERSION, _backfill_model
from src.models.orm.agents import Agent
from src.models.orm.forms import Form
from src.models.orm.integrations import Integration
from src.models.orm.solutions import Solution
from src.services.solutions.guard import (
    SolutionManagedWriteError,
    install_solution_write_guard,
)

pytestmark = pytest.mark.e2e

_buffer = io.BytesIO()
Image.new("RGB", (600, 300), (10, 120, 200)).save(_buffer, "PNG")
LARGE_PNG = _buffer.getvalue()
BAD_LOGO = b"not an image"


def _agent(name: str, logo: bytes, solution_id: uuid.UUID | None = None) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        name=name,
        system_prompt="p",
        created_by="test",
        solution_id=solution_id,
        logo_data=logo,
        logo_content_type="image/png",
    )


@pytest.fixture
async def solution(async_session_factory):
    sol = Solution(
        id=uuid.uuid4(), slug=f"logo-{uuid.uuid4().hex[:8]}", name="Logo", organization_id=None
    )
    async with async_session_factory() as db:
        db.add(sol)
        await db.commit()
    yield sol
    async with async_session_factory() as db:
        await db.execute(delete(Agent).where(Agent.solution_id == sol.id))
        await db.execute(delete(Solution).where(Solution.id == sol.id))
        await db.commit()


async def _drain(factory, model) -> tuple[int, int]:
    async with factory() as db:
        return await _backfill_model(db, model, 10_000)


async def _reload(factory, model, ids):
    async with factory() as db:
        rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars().all()
        return {row.id: row for row in rows}


async def test_mixed_managed_and_unmanaged_agents_both_drain(
    async_session_factory, solution
) -> None:
    managed = _agent(f"m-{uuid.uuid4().hex[:6]}", LARGE_PNG, solution.id)
    plain = _agent(f"p-{uuid.uuid4().hex[:6]}", LARGE_PNG)
    async with async_session_factory() as db:
        db.add_all([managed, plain])
        await db.commit()
    try:
        await _drain(async_session_factory, Agent)
        rows = await _reload(async_session_factory, Agent, [managed.id, plain.id])
        for row in rows.values():
            assert row.logo_thumbnail_data
            assert row.logo_thumbnail_version not in (None, _FAILED_VERSION)
            assert len(row.logo_thumbnail_version) == 64
        assert rows[managed.id].solution_id == solution.id
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Agent).where(Agent.id.in_([managed.id, plain.id])))
            await db.commit()


async def test_form_and_integration_rows_are_backfilled(async_session_factory) -> None:
    form = Form(
        id=uuid.uuid4(),
        name=f"f-{uuid.uuid4().hex[:6]}",
        created_by="test",
        logo_data=LARGE_PNG,
        logo_content_type="image/png",
    )
    integration = Integration(
        id=uuid.uuid4(),
        name=f"i-{uuid.uuid4().hex[:6]}",
        logo_data=LARGE_PNG,
        logo_content_type="image/png",
    )
    async with async_session_factory() as db:
        db.add_all([form, integration])
        await db.commit()
    try:
        await _drain(async_session_factory, Form)
        await _drain(async_session_factory, Integration)
        (form_row,) = (await _reload(async_session_factory, Form, [form.id])).values()
        (int_row,) = (
            await _reload(async_session_factory, Integration, [integration.id])
        ).values()
        for row in (form_row, int_row):
            assert row.logo_thumbnail_data
            assert len(row.logo_thumbnail_version) == 64
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Form).where(Form.id == form.id))
            await db.execute(delete(Integration).where(Integration.id == integration.id))
            await db.commit()


async def test_processing_failure_marks_only_that_row(async_session_factory) -> None:
    bad = _agent(f"b-{uuid.uuid4().hex[:6]}", BAD_LOGO)
    good = _agent(f"g-{uuid.uuid4().hex[:6]}", LARGE_PNG)
    async with async_session_factory() as db:
        db.add_all([bad, good])
        await db.commit()
    try:
        await _drain(async_session_factory, Agent)
        rows = await _reload(async_session_factory, Agent, [bad.id, good.id])
        assert rows[bad.id].logo_thumbnail_version == _FAILED_VERSION
        assert rows[bad.id].logo_data == BAD_LOGO
        assert len(rows[good.id].logo_thumbnail_version) == 64
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Agent).where(Agent.id.in_([bad.id, good.id])))
            await db.commit()


async def test_guard_still_rejects_orm_logo_edit_of_managed_row(
    async_session_factory, solution
) -> None:
    managed = _agent(f"m-{uuid.uuid4().hex[:6]}", LARGE_PNG, solution.id)
    async with async_session_factory() as db:
        db.add(managed)
        await db.commit()
    install_solution_write_guard()
    async with async_session_factory() as db:
        row = await db.get(Agent, managed.id)
        assert row is not None
        row.logo_data = b"changed"
        with pytest.raises(SolutionManagedWriteError):
            await db.flush()
        await db.rollback()
