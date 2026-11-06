"""One durable workflow-permission conversion journey over live storage and jobs.

This test runs two platform jobs and one workflow execution: analysis proves the repository
source -> durable report boundary, while the apply job proves the explicit
review -> durable workflow mutation boundary.  Source/workflow setup is direct
because API workflow creation and source writes are already covered elsewhere.
The converted Solution workflow then runs in the execution worker, proving
its imported SDK call succeeds with the limited identity and persisted grants.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    WorkflowPermissionGrantSpec,
    WorkflowPermissionMode,
)
from src.models.orm.solution_workflow_permission_requests import (
    SolutionWorkflowPermissionRequest,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.artifacts import ArtifactService, ArtifactAccessError, artifact_ref
from src.services.authorization.callers import load_human_caller
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SolutionStorage
from src.services.workflow_permissions import load_workflow_permissions, request_digest
from tests.e2e.conftest import execute_workflow_sync, poll_until


pytestmark = pytest.mark.e2e


def _wait_for_job(e2e_client, headers, job_id: UUID) -> None:
    """Observe the real worker through the shared job endpoint."""
    def terminal_job():
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        return job if job["status"] in {
            "succeeded", "failed", "cancelled", "requires_action",
        } else None

    job = poll_until(terminal_job, max_wait=30.0, interval=0.2)
    assert job is not None, f"Conversion job {job_id} did not complete"
    assert job["status"] == "succeeded", job


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
        "from bifrost import config, workflow\n"
        f"from .{helper_module} import load_runtime_setting\n\n"
        "@workflow()\n"
        "async def run():\n"
        "    return await load_runtime_setting(config)\n"
    )
    helper_source = (
        "from bifrost import api\n\n"
        "async def load_runtime_setting(client):\n"
        "    await api.get('/api/organizations')\n"
        "    return await client.get('conversion-runtime')\n"
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
        run_identity_id=org1_user.user_id,
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
    assert platform_admin.user_id is not None
    existing_identity_ids = {
        workflow.id: workflow.run_identity_id,
        solution_workflow.id: solution_workflow.run_identity_id,
    }
    users_before_apply = await db_session.scalar(select(func.count()).select_from(User))
    artifact_store = ArtifactService(db_session)
    # Seed another user's output in the run user's existing organization.
    # This is setup only, using the canonical authorized-job persistence path.
    artifact = await artifact_store.store_trusted_job(
        created_by_user_id=platform_admin.user_id,
        organization_id=org1_user.organization_id,
        filename=f"conversion-{suffix}.txt", content_type="text/plain",
        content=b"conversion evidence",
    )
    await db_session.commit()
    foreign_ref = artifact_ref(artifact).model_dump(mode="json")
    solution_permissions = ["artifacts.read.all", "roles.read"]
    await storage.write(entry_path, source.encode())
    await storage.write(helper_path, helper_source.encode())
    solution_storage = SolutionStorage(solution.id)
    await solution_storage.write(entry_path, source.encode())
    # The same import path in this install must not resolve to the loose
    # repository helper or reuse its context-dependent permission proposal.
    await solution_storage.write(
        helper_path,
        (
            "from bifrost import roles, artifacts\n\n"
            "async def load_runtime_setting(client):\n"
            f"    content = await artifacts.read({foreign_ref!r})\n"
            "    return {\"roles\": await roles.list(), \"text\": content.decode()}\n"
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
        _wait_for_job(e2e_client, platform_admin.headers, analysis_job_id)

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
            assert candidate["source_permissions"] == ["configs.read", "organizations.read", "secrets.read"]
            assert candidate["proposed_permissions"] == ["configs.read", "organizations.read", "secrets.read"]
            assert any(
                evidence["call"] == "bifrost.config.get"
                and evidence["path"] == helper_path
                for evidence in candidate["evidence"]
            )
            assert candidate["unknowns"] == []
        assert solution_item["source_permissions"] == solution_permissions
        assert solution_item["proposed_permissions"] == solution_permissions
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
                        "acknowledged_unknowns": False,
                    },
                    {
                        "workflow_id": str(solution_workflow.id),
                        "source_fingerprint": solution_item["source_fingerprint"],
                        "acknowledged_unknowns": False,
                    },
                ],
                "acknowledged_solution_requests": True,
            },
        )
        assert apply_response.status_code == 202, apply_response.text
        _wait_for_job(e2e_client, platform_admin.headers, UUID(apply_response.json()["job_id"]))

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
            assert review["acknowledged_unknowns"] is False

        await db_session.refresh(workflow)
        await db_session.refresh(solution_workflow)
        assert workflow.run_identity_id == existing_identity_ids[workflow.id]
        assert solution_workflow.run_identity_id == existing_identity_ids[solution_workflow.id]
        assert await db_session.scalar(select(func.count()).select_from(User)) == users_before_apply
        identity = await db_session.get(User, UUID(item["identity"]["id"]))
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
        assert sorted(grants) == ["configs.read", "organizations.read", "secrets.read"]

        # The persisted proposal must pass the runtime's actual named gate,
        # not merely save the strings the analyzer happened to recommend.
        from src.services.authorization.explain import (
            Powers, check_target, load_run_user, permission_entry,
        )

        run_user = await load_run_user(db_session, identity.id)
        assert run_user is not None
        converted_mode, converted_grants = await load_workflow_permissions(db_session, workflow)
        for permission in ("configs.read", "organizations.read", "secrets.read"):
            assert check_target(
                run_user, Powers(converted_mode, tuple(converted_grants)),
                None, permission_entry(permission),
            ).outcome == "success"

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
            grants=[WorkflowPermissionGrantSpec(permission=p) for p in solution_permissions],
        )
        assert (
            solution_request.status,
            solution_request.requested_mode,
            solution_request.requested_grants,
        ) == ("approved", "restricted", [{"permission": p} for p in solution_permissions])
        assert (
            solution_request.request_digest
            == solution_request.approved_digest
            == request_digest(expected_request)
        )
        mode, effective_grants = await load_workflow_permissions(
            db_session, solution_workflow
        )
        assert mode is WorkflowPermissionMode.RESTRICTED
        assert [grant.permission for grant in effective_grants] == solution_permissions

        # A limited identity proves the declaration supplies the required
        # power; a Platform Admin would hide an omitted grant here.
        limited_user = await load_run_user(db_session, org1_user.user_id)
        assert limited_user is not None
        assert check_target(
            limited_user, Powers(mode, ()), None, permission_entry("roles.read"),
        ).outcome == "failure"
        converted = Powers(mode, tuple(effective_grants))
        assert check_target(
            limited_user, converted, None, permission_entry("roles.read"),
        ).outcome == "success"
        assert check_target(
            limited_user, converted, None, permission_entry("users.readwrite"),
        ).outcome == "failure"
        assert check_target(
            limited_user, converted, uuid4(), permission_entry("roles.read"),
        ).outcome == "failure"

        # No historical run supplied the file-read grant. The source scan
        # must discover it, and it must not change this user's direct access.
        assert solution_item["observed_permissions"] == []
        with pytest.raises(ArtifactAccessError):
            await artifact_store.get_for_caller(
                artifact.id, caller=await load_human_caller(db_session, org1_user.user_id),
            )

        executed = execute_workflow_sync(
            e2e_client, org1_user.headers, str(solution_workflow.id), input_data={},
        )
        assert executed["status"] == "Success", (executed.get("error"), executed.get("error_message"), executed)
        assert isinstance(executed["result"]["roles"], list), executed
        assert executed["result"]["text"] == "conversion evidence", executed
    finally:
        await storage.delete(entry_path)
        await storage.delete(helper_path)
        await solution_storage.delete(entry_path)
        await solution_storage.delete(helper_path)
        await artifact_store.delete(artifact)
        # RepoStorage removes bytes, not registered workflow records. Leaving
        # these active makes a later workspace sync request delete approval.
        await db_session.execute(
            delete(Workflow).where(Workflow.id.in_(existing_identity_ids))
        )
        await db_session.execute(delete(Solution).where(Solution.id == solution.id))
        await db_session.commit()
