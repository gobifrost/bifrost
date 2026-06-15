"""Unit tests for the pending_captures capture→pull→deploy round-trip queue."""

import pytest
from sqlalchemy import select

from src.models.orm.pending_capture import PendingCaptureORM
from src.models.orm.solutions import Solution


async def _make_solution(db_session, slug: str = "test-sol") -> Solution:
    sol = Solution(slug=slug, name="Test Solution")
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
