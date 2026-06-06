"""Read-only enforcement for solution-managed entities.

Criterion 6: a solution-managed entity (solution_id IS NOT NULL) is read-only on
the platform — every non-deploy mutation rejects with the locked message. The
guard is the shared chokepoint each entity router's update/delete calls after
loading the row.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.services.solutions.guard import (
    SOLUTION_MANAGED_MESSAGE,
    assert_not_solution_managed,
)


def test_repo_entity_passes() -> None:
    # solution_id is None → ad-hoc _repo/ entity → mutation allowed (no raise).
    assert_not_solution_managed(SimpleNamespace(solution_id=None))


def test_entity_without_solution_id_attr_passes() -> None:
    # An entity type that never gained solution_id is never solution-managed.
    assert_not_solution_managed(SimpleNamespace())


def test_solution_managed_entity_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        assert_not_solution_managed(SimpleNamespace(solution_id=uuid.uuid4()))
    assert exc.value.status_code == 409
    assert exc.value.detail == SOLUTION_MANAGED_MESSAGE


def test_message_is_the_locked_wording() -> None:
    # Success-criteria §3.2 — exact, stable wording the UI + tests rely on.
    assert SOLUTION_MANAGED_MESSAGE == (
        "Solution-managed entities can only be managed by deployment methods."
    )


@pytest.mark.e2e
class TestAssertEntityIdNotSolutionManaged:
    async def test_raw_lookup_rejects_managed_row(self, db_session) -> None:
        """Even though repo.get() hides solution rows (cascade is _repo/-only),
        the raw id guard still sees them and returns the specific error."""
        from src.models.orm.solutions import Solution
        from src.models.orm.workflows import Workflow
        from src.services.solutions.guard import assert_entity_id_not_solution_managed

        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"g-{uuid.uuid4().hex[:8]}", name="G", organization_id=None)
        db.add(sol)
        await db.flush()
        wf_id = uuid.uuid4()
        db.add(Workflow(
            id=wf_id, name="m", function_name="run", path="workflows/m.py",
            type="workflow", organization_id=None, solution_id=sol.id,
        ))
        await db.flush()

        with pytest.raises(HTTPException) as exc:
            await assert_entity_id_not_solution_managed(db, Workflow, wf_id)
        assert exc.value.status_code == 409
        assert exc.value.detail == SOLUTION_MANAGED_MESSAGE

    async def test_repo_row_and_missing_row_pass(self, db_session) -> None:
        from src.models.orm.workflows import Workflow
        from src.services.solutions.guard import assert_entity_id_not_solution_managed

        db = db_session
        # _repo/ row (solution_id NULL) — allowed.
        wf_id = uuid.uuid4()
        db.add(Workflow(
            id=wf_id, name="r", function_name="run", path="workflows/r.py",
            type="workflow", organization_id=None, solution_id=None,
        ))
        await db.flush()
        await assert_entity_id_not_solution_managed(db, Workflow, wf_id)
        # Missing row — no raise (caller's own 404 handling applies).
        await assert_entity_id_not_solution_managed(db, Workflow, uuid.uuid4())


@pytest.fixture(autouse=True)
def _install_backstop_and_reset_redis():
    """Production installs the before_flush backstop via get_session_factory();
    the test conftest builds its own sessionmaker, so install it here too (it
    targets the global Session class and is idempotent). Also reset the async
    Redis singleton around deploy-touching tests (cross-file loop isolation)."""
    import src.core.redis_client as rc
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.mark.e2e
class TestBeforeFlushBackstop:
    """The session-wide before_flush backstop blocks ANY ORM-object mutation of
    a solution-managed entity, even surfaces that forgot the explicit guard
    (old direct-ORM MCP tools, secondary endpoints). Deploy is unaffected — it
    writes via Core statements that bypass the unit of work."""

    async def _managed_wf(self, db):
        from src.models.orm.solutions import Solution
        from src.models.orm.workflows import Workflow

        sol = Solution(id=uuid.uuid4(), slug=f"bf-{uuid.uuid4().hex[:8]}", name="BF", organization_id=None)
        db.add(sol)
        await db.flush()
        wf = Workflow(
            id=uuid.uuid4(), name="bfw", function_name="run", path="workflows/bfw.py",
            type="workflow", organization_id=None, solution_id=sol.id,
        )
        db.add(wf)
        await db.flush()
        return sol, wf

    async def test_direct_orm_modify_is_blocked(self, db_session) -> None:
        from src.services.solutions.guard import SolutionManagedWriteError

        db = db_session
        _sol, wf = await self._managed_wf(db)
        # Simulate an unguarded surface loading + mutating the ORM instance.
        wf.display_name = "hijack-via-mcp"
        with pytest.raises(SolutionManagedWriteError):
            await db.flush()
        await db.rollback()

    async def test_direct_orm_delete_is_blocked(self, db_session) -> None:
        from src.services.solutions.guard import SolutionManagedWriteError

        db = db_session
        _sol, wf = await self._managed_wf(db)
        await db.delete(wf)
        with pytest.raises(SolutionManagedWriteError):
            await db.flush()
        await db.rollback()

    async def test_deploy_core_upsert_is_allowed(self, db_session) -> None:
        """Deploy's Core update()/insert() path must NOT be blocked by the
        backstop — it does not go through the ORM unit of work."""
        from src.models.orm.solutions import Solution
        from src.models.orm.workflows import Workflow
        from src.services.solutions.deploy import (
            SolutionBundle,
            SolutionDeployer,
            solution_entity_id,
        )

        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"bfd-{uuid.uuid4().hex[:8]}", name="BFD", organization_id=None)
        db.add(sol)
        await db.flush()

        wf_id = str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            python_files={"workflows/w.py": "x"},
            workflows=[{"id": wf_id, "name": "bfd_w", "function_name": "run", "path": "workflows/w.py", "type": "workflow"}],
        ))
        await db.flush()  # must not raise
        row = await db.get(Workflow, solution_entity_id(sol.id, uuid.UUID(wf_id)))
        assert row is not None and row.solution_id == sol.id
