"""SolutionConnectionSchema ORM: a Solution-owned integration (connection) DECLARATION.

Mirrors test_solution_config_schema_model.py: the real ``Solution`` ORM has no
``scope`` column (scope is expressed via ``organization_id`` — UUID or NULL); there
is no ``SolutionScope`` enum in the ORM layer (``SolutionScope`` is a contracts-level
``Literal``). So these tests construct ``Solution`` with ``organization_id``.
"""
import pytest
from uuid import uuid4
from sqlalchemy import select

from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solutions import Solution


@pytest.mark.e2e
async def test_connection_schema_round_trips(db_session):
    sol = Solution(id=uuid4(), slug=f"conn-{uuid4().hex[:8]}", name="S", organization_id=None)
    db_session.add(sol)
    await db_session.flush()
    row = SolutionConnectionSchema(
        solution_id=sol.id,
        integration_name="HaloPSA",
        position=0,
        template={"name": "HaloPSA", "config_schema": [], "oauth": None},
    )
    db_session.add(row)
    await db_session.flush()
    got = (await db_session.execute(
        select(SolutionConnectionSchema).where(
            SolutionConnectionSchema.solution_id == sol.id
        )
    )).scalar_one()
    assert got.integration_name == "HaloPSA"
    assert got.template["name"] == "HaloPSA"


@pytest.mark.e2e
async def test_solution_readme_column(db_session):
    sol = Solution(id=uuid4(), slug=f"conn-{uuid4().hex[:8]}", name="S2",
                   organization_id=None, readme="# Hello")
    db_session.add(sol)
    await db_session.flush()
    assert sol.readme == "# Hello"


@pytest.mark.e2e
async def test_duplicate_integration_name_same_solution_rejected(db_session):
    import sqlalchemy.exc
    sol = Solution(id=uuid4(), slug=f"conn-{uuid4().hex[:8]}", name="S3", organization_id=None)
    db_session.add(sol)
    await db_session.flush()
    db_session.add(SolutionConnectionSchema(solution_id=sol.id, integration_name="DUP"))
    await db_session.flush()
    db_session.add(SolutionConnectionSchema(solution_id=sol.id, integration_name="DUP"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await db_session.flush()
