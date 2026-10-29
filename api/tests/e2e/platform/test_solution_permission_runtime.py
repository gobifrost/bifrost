"""Installed permission requests constrain powers without adding organization reach.

The matrix uses durable rows and the runtime loader: no worker jobs or builds.
The deployment boundary remains in test_solution_deploy_permission_requests.
"""

from uuid import uuid4

import pytest
from sqlalchemy import update

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    WorkflowPermissionMode,
)
from src.models.orm.solution_workflow_permission_requests import (
    SolutionWorkflowPermissionRequest,
)
from src.models.orm.solutions import Solution
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import (
    RunUser,
    check_permission,
    check_secret,
    load_powers,
)
from src.services.workflow_permissions import (
    request_digest,
    sync_solution_permission_requests,
)

pytestmark = pytest.mark.e2e


async def _installed(db, requested, *, stored_mode=None):
    solution = Solution(
        id=uuid4(),
        slug=f"consent-{uuid4().hex[:8]}",
        name="Contoso",
        organization_id=None,
    )
    workflow = Workflow(
        id=uuid4(),
        solution_id=solution.id,
        name="consent",
        function_name="consent",
        path="workflows/consent.py",
        permission_mode=stored_mode,
    )
    db.add(solution)
    await db.flush()
    db.add(workflow)
    await db.flush()
    row = SolutionWorkflowPermissionRequest(
        solution_id=solution.id,
        workflow_id=workflow.id,
        requested_mode=requested.mode.value,
        requested_grants=[grant.model_dump() for grant in requested.grants],
        request_digest=request_digest(requested),
        status="pending",
    )
    db.add(row)
    await db.flush()
    return workflow, row


def _caller():
    ctx = AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=uuid4(),
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=(),
    )
    return RunUser(user_id=ctx.user_id, ctx=ctx, identity_kind=None, is_active=True)


@pytest.mark.parametrize("status", ["pending", "declined"])
async def test_unapproved_declared_request_cannot_inherit_full_or_legacy_grants(
    db_session, status
):
    requested = RequestedWorkflowPermissions(
        mode="restricted", grants=[{"permission": "tables.read"}]
    )
    workflow, row = await _installed(db_session, requested)
    row.status = status
    db_session.add(
        WorkflowPermissionGrant(workflow_id=workflow.id, permission="secrets.read")
    )
    await db_session.flush()

    powers = await load_powers(db_session, workflow.id)
    caller = _caller()
    assert powers.mode is WorkflowPermissionMode.RESTRICTED
    assert powers.grants == ()
    assert (
        check_secret(caller, powers, caller.home, kind="config", name="Contoso").outcome
        == "failure"
    )


async def test_current_approval_adds_only_requested_permissions_inside_original_reach(
    db_session,
):
    requested = RequestedWorkflowPermissions(
        mode="restricted", grants=[{"permission": "tables.read"}]
    )
    workflow, row = await _installed(db_session, requested)
    row.status, row.approved_digest = "approved", row.request_digest
    await db_session.flush()

    powers = await load_powers(db_session, workflow.id)
    caller = _caller()
    assert [grant.permission for grant in powers.grants] == ["tables.read"]
    assert (
        check_permission(caller, powers, "tables.read", caller.home).outcome
        == "success"
    )
    assert check_permission(caller, powers, "tables.read", uuid4()).outcome == "failure"
    assert (
        check_secret(caller, powers, caller.home, kind="config", name="Contoso").outcome
        == "failure"
    )


@pytest.mark.parametrize(
    "mutation", ["approval_digest", "request_contents", "request_digest", "status"]
)
async def test_stale_or_revoked_consent_adds_no_powers(db_session, mutation):
    requested = RequestedWorkflowPermissions(
        mode="restricted", grants=[{"permission": "tables.read"}]
    )
    workflow, row = await _installed(db_session, requested)
    row.status, row.approved_digest = "approved", row.request_digest
    if mutation == "approval_digest":
        row.approved_digest = "0" * 64
    elif mutation == "request_contents":
        row.requested_grants = [{"permission": "secrets.read"}]
    elif mutation == "request_digest":
        row.request_digest = "0" * 64
    else:
        row.status = "declined"
    await db_session.flush()
    powers = await load_powers(db_session, workflow.id)
    assert powers.mode is WorkflowPermissionMode.RESTRICTED
    assert powers.grants == ()


async def test_new_full_request_requires_current_consent(db_session):
    workflow, row = await _installed(
        db_session, RequestedWorkflowPermissions(mode="full"), stored_mode="restricted"
    )
    assert (
        await load_powers(db_session, workflow.id)
    ).mode is WorkflowPermissionMode.RESTRICTED
    row.status, row.approved_digest = "approved", row.request_digest
    await db_session.flush()
    assert (
        await load_powers(db_session, workflow.id)
    ).mode is WorkflowPermissionMode.FULL
    row.status = "declined"
    await db_session.flush()
    assert (
        await load_powers(db_session, workflow.id)
    ).mode is WorkflowPermissionMode.RESTRICTED


async def test_legacy_full_default_remains_full(db_session):
    workflow, _ = await _installed(
        db_session, RequestedWorkflowPermissions(mode="full")
    )
    assert (
        await load_powers(db_session, workflow.id)
    ).mode is WorkflowPermissionMode.FULL


async def test_removing_a_legacy_declared_request_keeps_its_restriction(db_session):
    workflow, _ = await _installed(
        db_session, RequestedWorkflowPermissions(mode="restricted")
    )
    await sync_solution_permission_requests(
        db_session, solution_id=workflow.solution_id, requests={}
    )
    powers = await load_powers(db_session, workflow.id)
    assert powers.mode is WorkflowPermissionMode.RESTRICTED
    assert powers.grants == ()


async def test_another_installs_request_cannot_grant_powers(db_session):
    workflow, row = await _installed(
        db_session, RequestedWorkflowPermissions(mode="full"), stored_mode="restricted"
    )
    other = Solution(
        id=uuid4(),
        slug=f"fabrikam-{uuid4().hex[:8]}",
        name="Fabrikam",
        organization_id=None,
    )
    db_session.add(other)
    await db_session.flush()
    row.solution_id, row.status, row.approved_digest = (
        other.id,
        "approved",
        row.request_digest,
    )
    await db_session.flush()
    assert (
        await load_powers(db_session, workflow.id)
    ).mode is WorkflowPermissionMode.RESTRICTED


async def test_reloaded_powers_observe_revocation_over_a_cached_approval(db_session):
    requested = RequestedWorkflowPermissions(
        mode="restricted", grants=[{"permission": "tables.read"}]
    )
    workflow, row = await _installed(db_session, requested)
    row.status, row.approved_digest = "approved", row.request_digest
    await db_session.flush()
    assert len((await load_powers(db_session, workflow.id)).grants) == 1
    # Mimic a separately committed change without updating this identity map.
    await db_session.execute(
        update(SolutionWorkflowPermissionRequest)
        .where(SolutionWorkflowPermissionRequest.id == row.id)
        .values(status="declined")
        .execution_options(synchronize_session=False)
    )
    assert row.status == "approved"
    assert (await load_powers(db_session, workflow.id)).grants == ()


@pytest.mark.parametrize("cached_revocation", [False, True])
async def test_declined_approval_cannot_return_when_bundle_reverts(
    db_session, cached_revocation
):
    requested = RequestedWorkflowPermissions(
        mode="restricted", grants=[{"permission": "tables.read"}]
    )
    workflow, row = await _installed(db_session, requested)
    row.status, row.approved_digest = "approved", row.request_digest
    await db_session.flush()
    if cached_revocation:
        await db_session.execute(
            update(SolutionWorkflowPermissionRequest)
            .where(SolutionWorkflowPermissionRequest.id == row.id)
            .values(status="declined")
            .execution_options(synchronize_session=False)
        )
        assert row.status == "approved"
    else:
        row.status = "declined"
        await db_session.flush()
    changed = RequestedWorkflowPermissions(
        mode="restricted", grants=[{"permission": "secrets.read"}]
    )
    await sync_solution_permission_requests(
        db_session, solution_id=workflow.solution_id, requests={workflow.id: changed}
    )
    await sync_solution_permission_requests(
        db_session, solution_id=workflow.solution_id, requests={workflow.id: requested}
    )
    assert (await load_powers(db_session, workflow.id)).grants == ()
    assert row.status == "pending"
    assert row.approved_digest is None
