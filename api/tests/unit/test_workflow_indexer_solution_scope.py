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
