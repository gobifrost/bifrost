"""Task 16: bundle_for populates table_data for full exports (include_data=True)."""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution
from src.models.orm.tables import Document, Table
from src.services.solutions.capture import SolutionCaptureService

pytestmark = pytest.mark.e2e


async def _make_solution_with_table_rows(
    db,
    *,
    table: str,
    rows: list[dict],
) -> Solution:
    """Create a solution that owns a table with the given rows."""
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"test-tabledata-{uuid.uuid4().hex[:8]}",
        name="TableData Test",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()

    tbl = Table(
        id=uuid.uuid4(),
        name=table,
        organization_id=None,
        solution_id=sol.id,
        schema={"columns": [{"name": k, "type": "string"} for k in (rows[0] if rows else {}).keys()]},
    )
    db.add(tbl)
    await db.flush()

    for i, row_data in enumerate(rows):
        doc = Document(
            id=str(row_data.get("id", uuid.uuid4())),
            table_id=tbl.id,
            data=row_data,
        )
        db.add(doc)
    await db.flush()

    return sol


async def test_bundle_includes_table_rows_when_requested(db_session) -> None:
    db = db_session
    sol = await _make_solution_with_table_rows(
        db, table="widgets", rows=[{"id": 1, "name": "a"}]
    )
    bundle = await SolutionCaptureService(db).bundle_for(
        sol, include_values=True, include_data=True
    )
    assert bundle.table_data["widgets"] == [{"id": 1, "name": "a"}]
    assert bundle.table_document_ids["widgets"] == ["1"]


async def test_bundle_excludes_table_data_by_default(db_session) -> None:
    """include_data=False (default) must leave table_data empty — existing exports unchanged."""
    db = db_session
    sol = await _make_solution_with_table_rows(
        db, table="things", rows=[{"id": "x", "val": 99}]
    )
    bundle = await SolutionCaptureService(db).bundle_for(sol)
    assert bundle.table_data == {}


async def test_bundle_table_data_includes_empty_table_for_replace(db_session) -> None:
    """A full backup declares empty owned tables so restore can clear stale rows."""
    db = db_session
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"test-empty-{uuid.uuid4().hex[:8]}",
        name="Empty Table Test",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()
    tbl = Table(id=uuid.uuid4(), name="empty", organization_id=None, solution_id=sol.id)
    db.add(tbl)
    await db.flush()

    bundle = await SolutionCaptureService(db).bundle_for(sol, include_data=True)
    assert bundle.table_data["empty"] == []
    assert bundle.table_document_ids["empty"] == []


async def test_bundle_refuses_table_data_above_cap(db_session, monkeypatch) -> None:
    """A partial full backup is unsafe: reject it rather than truncating rows."""
    import src.services.solutions.capture as capture

    monkeypatch.setattr(capture, "TABLE_ROW_CAP", 1)
    sol = await _make_solution_with_table_rows(
        db_session,
        table="over-cap",
        rows=[{"id": "one", "name": "first"}, {"id": "two", "name": "second"}],
    )

    with pytest.raises(ValueError, match="over-cap.*1"):
        await SolutionCaptureService(db_session).bundle_for(sol, include_data=True)
