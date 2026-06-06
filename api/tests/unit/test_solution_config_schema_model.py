"""SolutionConfigSchema ORM: a Solution-owned config DECLARATION (no value)."""
import pytest
from uuid import uuid4

from src.models.orm.solutions import Solution
from src.models.orm.solution_config_schema import SolutionConfigSchema


@pytest.mark.e2e
class TestSolutionConfigSchemaModel:
    async def test_insert_and_read_declaration(self, db_session) -> None:
        db = db_session
        sol = Solution(id=uuid4(), slug=f"cfg-{uuid4().hex[:8]}", name="CFG", organization_id=None)
        db.add(sol)
        await db.flush()

        decl = SolutionConfigSchema(
            id=uuid4(),
            solution_id=sol.id,
            key="STRIPE_KEY",
            type="secret",
            required=True,
            description="Stripe secret key",
            default=None,
            position=0,
        )
        db.add(decl)
        await db.flush()

        assert decl.key == "STRIPE_KEY"
        assert decl.required is True
        assert not hasattr(decl, "value")

    async def test_duplicate_key_same_solution_rejected(self, db_session) -> None:
        import sqlalchemy.exc
        db = db_session
        sol = Solution(id=uuid4(), slug=f"cfg-{uuid4().hex[:8]}", name="CFG", organization_id=None)
        db.add(sol)
        await db.flush()
        db.add(SolutionConfigSchema(id=uuid4(), solution_id=sol.id, key="DUP", type="string"))
        await db.flush()
        db.add(SolutionConfigSchema(id=uuid4(), solution_id=sol.id, key="DUP", type="string"))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await db.flush()
