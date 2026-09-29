"""Solution deploy records each workflow's requested permissions, and export
carries them back out (requested side only)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest
from src.models.orm.solutions import Solution
from src.services.solutions.capture import SolutionCaptureService
from src.services.solutions.deploy import SolutionBundle, SolutionDeployConflict, SolutionDeployer

pytestmark = pytest.mark.e2e

WF_ID = "33333333-3333-3333-3333-333333333333"
REQUEST = {
    "mode": "restricted",
    "grants": [{"permission": "tables.read", "boundary": "managed_organizations"}],
}


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


async def _solution(db) -> Solution:
    sol = Solution(
        id=uuid.uuid4(), slug=f"perm-req-{uuid.uuid4().hex[:8]}", name="Perm", organization_id=None
    )
    db.add(sol)
    await db.flush()
    return sol


def _bundle(sol: Solution, requested_permissions: dict | None) -> SolutionBundle:
    entry = {"id": WF_ID, "name": "sync", "path": "workflows/sync.py", "function_name": "sync"}
    if requested_permissions is not None:
        entry["requested_permissions"] = requested_permissions
    return SolutionBundle(solution=sol, workflows=[entry])


async def _requests(db, sol: Solution) -> list[SolutionWorkflowPermissionRequest]:
    return list(
        (
            await db.execute(
                select(SolutionWorkflowPermissionRequest).where(
                    SolutionWorkflowPermissionRequest.solution_id == sol.id
                )
            )
        ).scalars()
    )


async def test_deploy_records_a_pending_request(db_session):
    sol = await _solution(db_session)
    await SolutionDeployer(db_session).deploy(_bundle(sol, REQUEST), force=True)
    (row,) = await _requests(db_session, sol)
    assert (row.status, row.requested_mode, row.requested_grants) == (
        "pending", "restricted", REQUEST["grants"]
    )


async def test_export_carries_the_requested_permissions(db_session):
    sol = await _solution(db_session)
    await SolutionDeployer(db_session).deploy(_bundle(sol, REQUEST), force=True)
    (row,) = await _requests(db_session, sol)
    row.status = "approved"
    row.approved_digest = row.request_digest
    await db_session.flush()

    exported = await SolutionCaptureService(db_session).bundle_for(sol)
    (workflow,) = exported.workflows
    assert workflow["requested_permissions"] == REQUEST


async def test_redeploy_without_a_request_removes_it(db_session):
    sol = await _solution(db_session)
    await SolutionDeployer(db_session).deploy(_bundle(sol, REQUEST), force=True)
    await SolutionDeployer(db_session).deploy(_bundle(sol, None), force=True)
    assert await _requests(db_session, sol) == []


async def test_export_of_a_workflow_without_a_request_omits_the_field(db_session):
    sol = await _solution(db_session)
    await SolutionDeployer(db_session).deploy(_bundle(sol, None), force=True)
    exported = await SolutionCaptureService(db_session).bundle_for(sol)
    (workflow,) = exported.workflows
    assert "requested_permissions" not in workflow


async def test_deploy_rejects_an_unknown_permission(db_session):
    sol = await _solution(db_session)
    bad = {"mode": "restricted", "grants": [{"permission": "nonsense.read"}]}
    with pytest.raises(SolutionDeployConflict, match="Unknown permission domain"):
        await SolutionDeployer(db_session).deploy(_bundle(sol, bad), force=True)


async def test_deploy_rejects_grants_on_a_full_request(db_session):
    sol = await _solution(db_session)
    bad = {"mode": "full", "grants": [{"permission": "tables.read"}]}
    with pytest.raises(SolutionDeployConflict, match="empty when mode is 'full'"):
        await SolutionDeployer(db_session).deploy(_bundle(sol, bad), force=True)
