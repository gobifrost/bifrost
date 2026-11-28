"""The manifest's consent and analyzer recommendations are separate inputs."""

import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared import workflow_conversions as conversion
from shared.runtime_modules import RuntimeModuleInventory
from shared.workflow_conversion_analysis import SourceFile
from src.models.contracts.workflow_permissions import RequestedWorkflowPermissions
from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.workflow_permissions import permission_request_summary, request_digest


@pytest.mark.parametrize("consent", ["pending", "approved", "declined", "stale"])
@pytest.mark.parametrize("mode", ["restricted", "full"])
async def test_declared_request_needs_approval_even_when_workflow_is_restricted(
    db_session, monkeypatch, consent, mode
):
    solution = Solution(slug=f"declaration-{uuid4().hex[:8]}", name="Declared request")
    db_session.add(solution)
    await db_session.flush()
    workflow = Workflow(
        name="declared", function_name="run", path="workflows/declared.py",
        permission_mode="restricted", solution_id=solution.id,
    )
    db_session.add(workflow)
    await db_session.flush()
    requested = RequestedWorkflowPermissions(
        mode=mode,
        grants=[{"permission": "roles.readwrite"}] if mode == "restricted" else [],
    )
    row = SolutionWorkflowPermissionRequest(
        workflow_id=workflow.id, solution_id=solution.id,
        requested_mode=mode, requested_grants=[g.model_dump() for g in requested.grants],
        request_digest=request_digest(requested),
        approved_digest="0" * 64 if consent == "stale" else request_digest(requested),
        status="approved" if consent == "stale" else consent,
    )
    db_session.add(row)
    await db_session.flush()
    monkeypatch.setattr(conversion, "_authority_snapshot", AsyncMock(return_value=(
        {"identity_is_active": True}, {"name": "Default Identity", "shared": True},
    )))
    monkeypatch.setattr(conversion, "_policy_revision_snapshot", AsyncMock(return_value={}))
    source = "from bifrost import roles\nasync def run():\n    return await roles.list()\n"
    report = await conversion._report_for_workflow(
        db_session, workflow,
        source=SourceFile(workflow.path, workflow.path, source, hashlib.sha256(source.encode()).hexdigest()),
        module_inventory=RuntimeModuleInventory(frozenset(), True),
    )
    assert report["status"] == ("already_declared" if consent == "approved" else "ready")
    assert report["proposed_permissions"] == ["roles.read"]
    summary = report["solution_permission_request"]
    assert summary["requested_permissions"] == (["roles.readwrite"] if mode == "restricted" else [])
    assert summary["status"] == ("pending" if consent == "stale" else consent)
    assert row.request_digest == request_digest(requested)


def test_summary_refuses_content_that_no_longer_matches_the_approved_digest():
    requested = RequestedWorkflowPermissions(mode="restricted", grants=[{"permission": "tables.read"}])
    row = SolutionWorkflowPermissionRequest(
        requested_mode="restricted", requested_grants=[{"permission": "secrets.read"}],
        status="approved", request_digest=request_digest(requested),
        approved_digest=request_digest(requested),
    )
    assert permission_request_summary(row).status == "pending"
