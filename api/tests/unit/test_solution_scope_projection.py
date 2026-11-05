"""Projection contracts for active Solution reads on runtime hot paths."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import InvalidRequestError

from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.services.solution_scope import get_active_solution


@pytest.mark.asyncio
async def test_active_solution_defers_large_assets_and_relationships(db_session) -> None:
    """Hot-path scope checks need only durable routing fields."""
    solution = Solution(
        slug=f"projection-{uuid4().hex}",
        name="Projection",
        allow_outbound_access=True,
        logo_data=b"x" * 750_000,
        logo_thumbnail_data=b"y" * 8_000,
        readme="z" * 16_000,
    )
    db_session.add(solution)
    await db_session.flush()
    solution_id = solution.id
    db_session.add_all([
        SolutionConnectionSchema(solution_id=solution_id, integration_name="local"),
        SolutionFileLocation(solution_id=solution_id, location="docs", position=0),
    ])
    await db_session.commit()
    db_session.expunge_all()

    statements: list[str] = []
    engine = db_session.sync_session.bind
    assert engine is not None

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(engine, "after_cursor_execute", capture)
    try:
        active = await get_active_solution(db_session, solution_id)
    finally:
        event.remove(engine, "after_cursor_execute", capture)

    assert active is not None
    assert active.id == solution_id
    assert active.status == "active"
    assert active.organization_id is None
    assert active.allow_outbound_access is True
    assert len(statements) == 1
    unloaded = inspect(active).unloaded
    assert {"logo_data", "logo_thumbnail_data", "readme", "connection_schema", "file_locations"} <= unloaded
    with pytest.raises(InvalidRequestError):
        _ = active.logo_data
    with pytest.raises(InvalidRequestError):
        _ = active.connection_schema
