"""Codex #14: the workspace WorkflowIndexer is a _repo/-tier concept. Now that a
_repo/ workflow and a solution-managed workflow can share (path, function_name),
the indexer's lookup/deactivate-by-path queries must scope to solution_id IS NULL
— otherwise a workspace file op would raise MultipleResultsFound or touch the
solution-managed row (which is written ONLY by deploy)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.file_storage.indexers.workflow import WorkflowIndexer

pytestmark = pytest.mark.e2e


async def _add_wf(db, *, solution_id, path="workflows/foo.py", fn="main", active=True):
    wf = Workflow(
        id=uuid4(), name=path, function_name=fn, path=path, type="workflow",
        is_active=active, organization_id=None, solution_id=solution_id,
    )
    db.add(wf)
    await db.flush()
    return wf


async def test_delete_file_does_not_deactivate_solution_workflow(db_session):
    """Deleting a _repo/ file at a path also shipped by a solution must
    deactivate ONLY the _repo/ row, never the solution-managed one."""
    db = db_session
    sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=None)
    db.add(sol)
    await db.flush()

    repo_wf = await _add_wf(db, solution_id=None)
    sol_wf = await _add_wf(db, solution_id=sol.id)

    count = await WorkflowIndexer(db).delete_workflows_for_file("workflows/foo.py")
    await db.flush()

    assert count == 1  # only the _repo/ row
    await db.refresh(repo_wf)
    await db.refresh(sol_wf)
    assert repo_wf.is_active is False
    assert sol_wf.is_active is True  # solution workflow untouched by a workspace op


async def test_delete_file_with_only_solution_row_is_noop(db_session):
    """If ONLY a solution workflow exists at a path (no _repo/ row), a workspace
    file delete deactivates nothing (the solution row is deploy-owned)."""
    db = db_session
    sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=None)
    db.add(sol)
    await db.flush()
    sol_wf = await _add_wf(db, solution_id=sol.id)

    count = await WorkflowIndexer(db).delete_workflows_for_file("workflows/foo.py")
    await db.flush()

    assert count == 0
    await db.refresh(sol_wf)
    assert sol_wf.is_active is True


async def _new_solution(db):
    sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=None)
    db.add(sol)
    await db.flush()
    return sol


async def test_force_deactivation_does_not_touch_solution_workflow(db_session):
    """The workspace force-deactivation path (file saved with a function removed)
    must only deactivate the _repo/ row at the path, never a solution row."""
    db = db_session
    sol = await _new_solution(db)
    repo_wf = await _add_wf(db, solution_id=None, fn="dropped")
    sol_wf = await _add_wf(db, solution_id=sol.id, fn="dropped")

    from src.services.file_storage.deactivation import DeactivationProtectionService

    svc = DeactivationProtectionService(db)
    # No functions remain in the file → deactivate-all-at-path branch.
    count = await svc.deactivate_removed_workflows("workflows/foo.py", set())
    await db.flush()

    assert count == 1  # only the _repo/ row
    await db.refresh(repo_wf)
    await db.refresh(sol_wf)
    assert repo_wf.is_active is False
    assert sol_wf.is_active is True


async def test_replace_workflow_conflict_ignores_solution_row(db_session):
    """The orphan-replace path-conflict guard must not see a solution-managed row
    at the same (path, function_name): a workspace orphan replace targets _repo/,
    and the solution row is deploy-owned — it must not block the replace."""
    db = db_session
    sol = await _new_solution(db)
    # Orphaned _repo/ workflow we will repoint to (path, fn).
    orphan = Workflow(
        id=uuid4(), name="orphan", function_name="old", path="workflows/old.py",
        type="workflow", is_active=True, is_orphaned=True,
        organization_id=None, solution_id=None,
    )
    db.add(orphan)
    # A solution row already occupies the target (path, fn) — must be ignored.
    await _add_wf(db, solution_id=sol.id, path="workflows/target.py", fn="main")
    await db.flush()

    from src.services.workflow_orphan import WorkflowOrphanService

    svc = WorkflowOrphanService(db)

    async def _read(path):
        return "@workflow\ndef main():\n    pass\n"

    svc._read_file = _read  # type: ignore[method-assign]

    # Must NOT raise "already registered" — the only conflicting row is solution-owned.
    result = await svc.replace_workflow(orphan.id, "workflows/target.py", "main")
    assert result.path == "workflows/target.py"
    assert result.function_name == "main"
