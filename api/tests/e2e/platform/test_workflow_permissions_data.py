"""DB-backed behaviour of workflow permission data: the platform default mode,
a workflow's effective mode, and Solution permission-request syncing."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    WorkflowPermissionGrantSpec,
    WorkflowPermissionMode,
)
from src.models.orm.config import SystemConfig
from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest
from src.models.orm.solutions import Solution
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.workflow_permissions import (
    effective_workflow_permission_mode,
    get_default_workflow_permission_mode,
    list_workflow_grants,
    request_digest,
    sync_solution_permission_requests,
)

pytestmark = pytest.mark.e2e


async def _workflow(db, *, solution_id=None, permission_mode=None) -> Workflow:
    wf = Workflow(
        id=uuid.uuid4(),
        name=f"wf-{uuid.uuid4().hex[:8]}",
        function_name="run",
        path=f"workflows/{uuid.uuid4().hex[:8]}.py",
        solution_id=solution_id,
        permission_mode=permission_mode,
    )
    db.add(wf)
    await db.flush()
    return wf


async def _solution(db) -> Solution:
    sol = Solution(
        id=uuid.uuid4(), slug=f"perm-{uuid.uuid4().hex[:8]}", name="Perm", organization_id=None
    )
    db.add(sol)
    await db.flush()
    return sol


def _restricted(permission: str) -> RequestedWorkflowPermissions:
    return RequestedWorkflowPermissions(
        mode="restricted", grants=[WorkflowPermissionGrantSpec(permission=permission)]
    )


async def _only_request(db, solution_id) -> SolutionWorkflowPermissionRequest | None:
    return (
        await db.execute(
            select(SolutionWorkflowPermissionRequest).where(
                SolutionWorkflowPermissionRequest.solution_id == solution_id
            )
        )
    ).scalars().one_or_none()


async def _set_default_row(db, mode: str) -> None:
    existing = (
        await db.execute(
            select(SystemConfig).where(
                SystemConfig.category == "workflow_permissions",
                SystemConfig.key == "default_mode",
                SystemConfig.organization_id.is_(None),
            )
        )
    ).scalars().first()
    if existing is None:
        db.add(
            SystemConfig(
                category="workflow_permissions",
                key="default_mode",
                value_json={"mode": mode},
                organization_id=None,
            )
        )
    else:
        existing.value_json = {"mode": mode}
    await db.flush()


async def test_default_mode_is_full_without_a_stored_row(db_session):
    assert await get_default_workflow_permission_mode(db_session) is WorkflowPermissionMode.FULL


async def test_stored_default_mode_is_returned(db_session):
    await _set_default_row(db_session, "restricted")
    assert (
        await get_default_workflow_permission_mode(db_session)
        is WorkflowPermissionMode.RESTRICTED
    )


async def test_effective_mode_prefers_the_workflows_own(db_session):
    await _set_default_row(db_session, "restricted")
    own = await _workflow(db_session, permission_mode="full")
    inherits = await _workflow(db_session)
    assert await effective_workflow_permission_mode(db_session, own) is WorkflowPermissionMode.FULL
    assert (
        await effective_workflow_permission_mode(db_session, inherits)
        is WorkflowPermissionMode.RESTRICTED
    )


async def test_list_workflow_grants_returns_boundaries(db_session):
    wf = await _workflow(db_session)
    db_session.add(
        WorkflowPermissionGrant(
            workflow_id=wf.id, permission="tables.read", boundary_kind="platform"
        )
    )
    await db_session.flush()
    grants = await list_workflow_grants(db_session, wf.id)
    assert [(g.permission, g.boundary, g.organization_id) for g in grants] == [
        ("tables.read", "platform", None)
    ]


async def test_sync_inserts_new_request_as_pending(db_session):
    sol = await _solution(db_session)
    wf = await _workflow(db_session, solution_id=sol.id)
    requested = _restricted("tables.read")
    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: requested}
    )
    row = await _only_request(db_session, sol.id)
    assert (row.status, row.requested_mode, row.request_digest) == (
        "pending", "restricted", request_digest(requested)
    )
    assert row.requested_grants == [{"permission": "tables.read", "boundary": None}]


async def test_sync_unchanged_request_keeps_approval(db_session):
    sol = await _solution(db_session)
    wf = await _workflow(db_session, solution_id=sol.id)
    requested = _restricted("tables.read")
    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: requested}
    )
    row = await _only_request(db_session, sol.id)
    row.status = "approved"
    row.approved_digest = row.request_digest
    row.decided_by = "admin@example.com"
    await db_session.flush()

    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: requested}
    )
    await db_session.refresh(row)
    assert (row.status, row.decided_by) == ("approved", "admin@example.com")


async def test_sync_changed_request_is_pending_again(db_session):
    sol = await _solution(db_session)
    wf = await _workflow(db_session, solution_id=sol.id)
    original = _restricted("tables.read")
    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: original}
    )
    row = await _only_request(db_session, sol.id)
    row.status = "approved"
    row.approved_digest = row.request_digest
    await db_session.flush()

    changed = _restricted("tables.readwrite")
    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: changed}
    )
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.request_digest == request_digest(changed)
    assert row.approved_digest == request_digest(original)


async def test_sync_changed_back_to_approved_digest_is_approved(db_session):
    sol = await _solution(db_session)
    wf = await _workflow(db_session, solution_id=sol.id)
    original = _restricted("tables.read")
    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: original}
    )
    row = await _only_request(db_session, sol.id)
    row.status = "approved"
    row.approved_digest = row.request_digest
    await db_session.flush()
    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: _restricted("tables.readwrite")}
    )

    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: original}
    )
    await db_session.refresh(row)
    assert row.status == "approved"


async def test_sync_deletes_request_no_longer_declared(db_session):
    sol = await _solution(db_session)
    wf = await _workflow(db_session, solution_id=sol.id)
    await sync_solution_permission_requests(
        db_session, solution_id=sol.id, requests={wf.id: _restricted("tables.read")}
    )
    await sync_solution_permission_requests(db_session, solution_id=sol.id, requests={})
    assert await _only_request(db_session, sol.id) is None
