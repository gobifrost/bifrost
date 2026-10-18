"""One durable workflow-permission conversion journey over live storage and jobs.

This test deliberately runs two jobs: the analysis job proves the repository
source -> durable report boundary, while the apply job proves the explicit
review -> durable workflow mutation boundary.  Source/workflow setup is direct
because API workflow creation and source writes are already covered elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.jobs.platform import runner as platform_runner
from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    WorkflowPermissionGrantSpec,
    WorkflowPermissionMode,
)
from src.models.enums import IdentityKind
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_workflow_permission_requests import (
    SolutionWorkflowPermissionRequest,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SolutionStorage
from src.services.workflow_permissions import load_workflow_permissions, request_digest


pytestmark = pytest.mark.e2e


async def _run_queued_job(db_session, job_id: UUID) -> PlatformJob:
    """Claim once in-process, using the production platform-job runner."""
    job = await db_session.get(PlatformJob, job_id)
    assert job is not None
    assert job.status == "queued"
    now = datetime.now(timezone.utc)
    lease_token = uuid4()
    job.status = "running"
    job.phase = "Starting e2e conversion job"
    job.attempt = 1
    job.started_at = now
    job.lease_owner = "e2e-workflow-conversion"
    job.lease_token = lease_token
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(minutes=5)
    job.revision = (job.revision or 0) + 1
    await db_session.commit()

    assert await platform_runner.run_claimed_platform_job(job_id, lease_token)
    await db_session.refresh(job)
    assert job.status == "succeeded", f"{job.error_code}: {job.error_message}"
    return job


async def test_reviewed_conversion_discovers_helper_sdk_calls_and_applies_restricted_access(
    e2e_client, platform_admin, org1_user, db_session
) -> None:
    """Analysis/report/apply/reopen preserves one reviewable permission decision.

    The source SDK use is intentionally only in the imported helper.  This is
    the one full worker journey for conversion: one analysis and one apply job.
    Staleness and cutover refusals belong in direct service tests because they
    do not need an additional worker execution to prove their gates.
    """
    suffix = uuid4().hex[:10]
    entry_path = f"workflows/conversion_{suffix}.py"
    helper_path = f"workflows/conversion_helper_{suffix}.py"
    helper_module = f"conversion_helper_{suffix}"
    source = (
        f"from .{helper_module} import load_runtime_setting\n\n"
        "async def run():\n"
        "    return await load_runtime_setting()\n"
    )
    helper_source = (
        "from bifrost import config\n\n"
        "async def load_runtime_setting():\n"
        "    return await config.get('conversion-runtime')\n"
    )
    storage = RepoStorage()
    workflow = Workflow(
        id=uuid4(),
        name=f"conversion-{suffix}",
        function_name="run",
        path=entry_path,
        access_level="authenticated",
        permission_mode="full",
        is_active=True,
    )
    solution = Solution(
        id=uuid4(),
        slug=f"conversion-solution-{suffix}",
        name=f"Conversion Solution {suffix}",
        organization_id=None,
    )
    solution_workflow = Workflow(
        id=uuid4(),
        solution_id=solution.id,
        name=f"conversion-solution-{suffix}",
        function_name="run",
        path=entry_path,
        access_level="authenticated",
        permission_mode="full",
        is_active=True,
    )
    db_session.add(workflow)
    db_session.add(solution)
    db_session.add(solution_workflow)
    await db_session.commit()
    await storage.write(entry_path, source.encode())
    await storage.write(helper_path, helper_source.encode())
    solution_storage = SolutionStorage(solution.id)
    await solution_storage.write(entry_path, source.encode())
    # The same import path in this install must not resolve to the loose
    # repository helper or reuse its context-dependent permission proposal.
    await solution_storage.write(
        helper_path,
        (
            "from bifrost import roles\n\n"
            "async def load_runtime_setting():\n"
            "    return await roles.list()\n"
        ).encode(),
    )

    try:
        analysis_response = e2e_client.post(
            "/api/workflow-conversions",
            headers=platform_admin.headers,
            json={
                "workflow_ids": [str(workflow.id), str(solution_workflow.id)],
                "scope": "all",
                "platform_cutover": False,
            },
        )
        assert analysis_response.status_code == 202, analysis_response.text
        analysis_job_id = UUID(analysis_response.json()["job_id"])
        assert (
            analysis_response.headers["Location"]
            == f"/api/platform-jobs/{analysis_job_id}"
        )
        duplicate = e2e_client.post(
            "/api/workflow-conversions",
            headers=platform_admin.headers,
            json={
                "workflow_ids": [str(workflow.id), str(solution_workflow.id)],
                "scope": "all",
            },
        )
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["job_id"] == str(analysis_job_id)
        assert duplicate.json()["reused"] is True
        denied = e2e_client.get(
            f"/api/workflow-conversions/{analysis_job_id}/report",
            headers=org1_user.headers,
        )
        assert denied.status_code == 403, denied.text
        await _run_queued_job(db_session, analysis_job_id)

        report_response = e2e_client.get(
            f"/api/workflow-conversions/{analysis_job_id}/report",
            headers=platform_admin.headers,
            params={"offset": 0, "limit": 1},
        )
        assert report_response.status_code == 200, report_response.text
        report = report_response.json()
        assert (
            report["job_id"],
            report["total"],
            report["offset"],
            report["limit"],
        ) == (
            str(analysis_job_id),
            2,
            0,
            1,
        )
        second_page = e2e_client.get(
            f"/api/workflow-conversions/{analysis_job_id}/report",
            headers=platform_admin.headers,
            params={"offset": 1, "limit": 1},
        )
        assert second_page.status_code == 200, second_page.text
        assert (
            second_page.json()["total"],
            second_page.json()["offset"],
            second_page.json()["limit"],
            len(second_page.json()["items"]),
        ) == (2, 1, 1, 1)
        empty_page = e2e_client.get(
            f"/api/workflow-conversions/{analysis_job_id}/report",
            headers=platform_admin.headers,
            params={"offset": 2, "limit": 1},
        )
        assert empty_page.status_code == 200, empty_page.text
        assert empty_page.json()["items"] == []
        assert empty_page.json()["total"] == 2
        items = {
            item["workflow_id"]: item
            for item in [*report["items"], *second_page.json()["items"]]
        }
        item = items[str(workflow.id)]
        solution_item = items[str(solution_workflow.id)]
        for candidate in (item,):
            assert candidate["status"] == "ready"
            assert candidate["source_permissions"] == ["configs.read", "secrets.read"]
            assert candidate["proposed_permissions"] == ["configs.read", "secrets.read"]
            assert any(
                evidence["call"] == "bifrost.config.get"
                and evidence["path"] == helper_path
                for evidence in candidate["evidence"]
            )
            assert candidate["unknowns"] == []
        assert solution_item["source_permissions"] == ["roles.read"]
        assert solution_item["proposed_permissions"] == ["roles.read"]
        assert any(
            evidence["call"] == "bifrost.roles.list"
            for evidence in solution_item["evidence"]
        )

        apply_response = e2e_client.post(
            f"/api/workflow-conversions/{analysis_job_id}/apply",
            headers=platform_admin.headers,
            json={
                "selections": [
                    {
                        "workflow_id": str(workflow.id),
                        "source_fingerprint": item["source_fingerprint"],
                        "identity_strategy": "dedicated",
                        "acknowledged_unknowns": False,
                        "acknowledged_broad_identity": False,
                    },
                    {
                        "workflow_id": str(solution_workflow.id),
                        "source_fingerprint": solution_item["source_fingerprint"],
                        "identity_strategy": "dedicated",
                        "acknowledged_unknowns": False,
                        "acknowledged_broad_identity": False,
                    },
                ],
                "acknowledged_solution_requests": True,
            },
        )
        assert apply_response.status_code == 202, apply_response.text
        await _run_queued_job(db_session, UUID(apply_response.json()["job_id"]))

        reopened = e2e_client.get(
            f"/api/workflow-conversions/{analysis_job_id}/report",
            headers=platform_admin.headers,
            params={"offset": 0, "limit": 2},
        )
        assert reopened.status_code == 200, reopened.text
        reopened_items = {
            item["workflow_id"]: item for item in reopened.json()["items"]
        }
        for workflow_id in (str(workflow.id), str(solution_workflow.id)):
            review = reopened_items[workflow_id]["review"]
            assert review["applied"] is True
            assert review["identity_strategy"] == "dedicated"
            assert review["acknowledged_unknowns"] is False

        await db_session.refresh(workflow)
        identity = await db_session.get(User, workflow.run_identity_id)
        grants = list(
            (
                await db_session.execute(
                    select(WorkflowPermissionGrant.permission).where(
                        WorkflowPermissionGrant.workflow_id == workflow.id
                    )
                )
            ).scalars()
        )
        assert workflow.permission_mode == "restricted"
        assert identity is not None
        assert identity.identity_kind == IdentityKind.CUSTOM.value
        assert identity.organization_id == workflow.organization_id
        assert identity.base_role_id == UUID("00000000-0000-0000-0000-000000000006")
        assert sorted(grants) == ["configs.read", "secrets.read"]

        # The persisted proposal must pass the runtime's actual named gate,
        # not merely save the strings the analyzer happened to recommend.
        from src.services.authorization.explain import (
            Powers, check_target, load_run_user, permission_entry,
        )

        run_user = await load_run_user(db_session, identity.id)
        assert run_user is not None
        converted_mode, converted_grants = await load_workflow_permissions(db_session, workflow)
        for permission in ("configs.read", "secrets.read"):
            assert check_target(
                run_user, Powers(converted_mode, tuple(converted_grants)),
                None, permission_entry(permission),
            ).outcome == "success"

        await db_session.refresh(solution_workflow)
        assert solution_workflow.permission_mode == "restricted"
        solution_request = (
            await db_session.execute(
                select(SolutionWorkflowPermissionRequest).where(
                    SolutionWorkflowPermissionRequest.solution_id == solution.id,
                    SolutionWorkflowPermissionRequest.workflow_id
                    == solution_workflow.id,
                )
            )
        ).scalar_one()
        expected_request = RequestedWorkflowPermissions(
            mode=WorkflowPermissionMode.RESTRICTED,
            grants=[WorkflowPermissionGrantSpec(permission="roles.read")],
        )
        assert (
            solution_request.status,
            solution_request.requested_mode,
            solution_request.requested_grants,
        ) == ("approved", "restricted", [{"permission": "roles.read"}])
        assert (
            solution_request.request_digest
            == solution_request.approved_digest
            == request_digest(expected_request)
        )
        mode, effective_grants = await load_workflow_permissions(
            db_session, solution_workflow
        )
        assert mode is WorkflowPermissionMode.RESTRICTED
        assert [grant.permission for grant in effective_grants] == ["roles.read"]
    finally:
        await storage.delete(entry_path)
        await storage.delete(helper_path)
        await solution_storage.delete(entry_path)
        await solution_storage.delete(helper_path)
