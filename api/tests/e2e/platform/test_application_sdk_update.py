from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.services.application_source_artifact import ApplicationSourceArtifactStorage

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_sdk_update_rows(db_session):
    """Restore the shared E2E DB after SDK-update tests commit seed rows.

    These tests intentionally use durable PlatformJobs and therefore commit
    their seeded standalone apps.  Leaving those apps behind lets later MCP
    tests encounter unrelated rows with ``repo_path=None``.
    """
    existing_app_ids = set(await db_session.scalars(select(Application.id)))
    existing_solution_ids = set(await db_session.scalars(select(Solution.id)))
    existing_job_ids = set(await db_session.scalars(select(PlatformJob.id)))
    try:
        yield
    finally:
        await db_session.execute(
            delete(PlatformJob).where(PlatformJob.id.not_in(existing_job_ids))
        )
        await db_session.execute(
            delete(Application).where(Application.id.not_in(existing_app_ids))
        )
        await db_session.execute(
            delete(Solution).where(Solution.id.not_in(existing_solution_ids))
        )
        await db_session.commit()


async def _seed_app(
    db_session,
    *,
    slug: str,
    app_model: str = "standalone_v2",
    active_deployment_id=None,
    solution_id=None,
    has_repo_source: bool = True,
    sdk_fingerprint: str | None = "old-fingerprint",
    sdk_built_at: datetime | None = datetime(2026, 1, 1, tzinfo=timezone.utc),
) -> Application:
    app = Application(
        id=uuid4(),
        name=slug.replace("-", " ").title(),
        slug=slug,
        repo_path=f"apps/{slug}" if solution_id and has_repo_source else None,
        solution_id=solution_id,
        app_model=app_model,
        active_deployment_id=active_deployment_id,
        deployed_at=sdk_built_at,
        sdk_package_version="0.0.1" if sdk_fingerprint else None,
        sdk_fingerprint=sdk_fingerprint,
        sdk_contract_version=1 if sdk_fingerprint else None,
        sdk_built_at=sdk_built_at,
        access_level="authenticated",
    )
    db_session.add(app)
    await db_session.commit()
    return app


async def _store_source(app_id: UUID, deployment_id: UUID, files: dict[str, bytes]) -> None:
    with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
        path = Path(tmp.name)
        with zipfile.ZipFile(path, "w") as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        await ApplicationSourceArtifactStorage().write_deployment_source(
            app_id, deployment_id, path
        )


async def test_app_status_source_export_and_single_update_enqueue(
    e2e_client, platform_admin, org1_user, db_session
):
    deployment_id = uuid4()
    app = await _seed_app(
        db_session,
        slug=f"sdk-app-{uuid4().hex[:8]}",
        active_deployment_id=deployment_id,
    )
    await _store_source(
        app.id,
        deployment_id,
        {
            "package.json": b'{"scripts":{"build":"vite --host 0.0.0.0"}}',
            "index.html": b"<div id='root'></div>",
            "src/App.tsx": b"export default function App(){return null}",
        },
    )

    listed = e2e_client.get("/api/applications", headers=platform_admin.headers)
    assert listed.status_code == 200, listed.text
    listed_app = next(a for a in listed.json()["applications"] if a["id"] == str(app.id))
    assert listed_app["sdk_fingerprint"] == "old-fingerprint"
    assert listed_app["sdk_status"] == "update_available"
    assert listed_app["sdk_source_available"] is True

    got = e2e_client.get(f"/api/applications/{app.slug}", headers=platform_admin.headers)
    assert got.status_code == 200, got.text
    assert got.json()["sdk_package_version"] == "0.0.1"

    denied = e2e_client.get(
        f"/api/applications/{app.id}/source", headers=org1_user.headers
    )
    assert denied.status_code == 403

    source = e2e_client.get(
        f"/api/applications/{app.id}/source", headers=platform_admin.headers
    )
    assert source.status_code == 200, source.text
    assert source.headers["content-type"].startswith("application/zip")
    assert b"src/App.tsx" in source.content

    queued = e2e_client.post(
        f"/api/applications/{app.id}/sdk/update", headers=platform_admin.headers
    )
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert queued.headers["location"] == f"/api/platform-jobs/{body['job_id']}"
    assert body["status"] == "queued"

    reused = e2e_client.post(
        f"/api/applications/{app.id}/sdk/update", headers=platform_admin.headers
    )
    assert reused.status_code == 202, reused.text
    assert reused.json()["job_id"] == body["job_id"]
    assert reused.json()["reused"] is True

    job = await db_session.get(PlatformJob, UUID(body["job_id"]))
    assert job is not None
    assert job.job_type == "application.sdk_update"
    assert job.resource_lock_key == f"application:{app.id}"
    assert job.notification_id is not None


async def test_source_export_unavailable_fails_safely(
    e2e_client, platform_admin, db_session
):
    app = await _seed_app(
        db_session,
        slug=f"sdk-nosource-{uuid4().hex[:8]}",
        active_deployment_id=None,
        sdk_built_at=None,
    )

    source = e2e_client.get(
        f"/api/applications/{app.id}/source", headers=platform_admin.headers
    )

    assert source.status_code == 409


async def test_batch_update_filters_actionable_apps(e2e_client, platform_admin, db_session):
    actionable_deployment_id = uuid4()
    actionable = await _seed_app(
        db_session,
        slug=f"sdk-batch-action-{uuid4().hex[:8]}",
        active_deployment_id=actionable_deployment_id,
    )
    await _store_source(
        actionable.id,
        actionable_deployment_id,
        {"package.json": b"{}", "index.html": b"<div id='root'></div>"},
    )
    inline = await _seed_app(
        db_session,
        slug=f"sdk-batch-inline-{uuid4().hex[:8]}",
        app_model="inline_v1",
        active_deployment_id=None,
        sdk_fingerprint=None,
        sdk_built_at=None,
    )
    unavailable = await _seed_app(
        db_session,
        slug=f"sdk-batch-nosource-{uuid4().hex[:8]}",
        active_deployment_id=None,
        sdk_built_at=None,
    )

    response = e2e_client.post(
        "/api/applications/sdk/update",
        headers=platform_admin.headers,
        json={"application_ids": [str(actionable.id), str(inline.id), str(unavailable.id)]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["application_id"] for item in body["accepted"]] == [str(actionable.id)]
    skipped = {item["application_id"]: item["reason"] for item in body["skipped"]}
    assert skipped[str(inline.id)] == "not_applicable"
    assert skipped[str(unavailable.id)] == "source_unavailable"
    jobs = (
        await db_session.execute(
            select(PlatformJob).where(PlatformJob.job_type == "application.sdk_update")
        )
    ).scalars().all()
    assert len([j for j in jobs if j.resource_id == str(actionable.id)]) == 1


async def test_solution_sdk_status_and_update_enqueue_app_jobs(
    e2e_client, platform_admin, db_session
):
    solution = Solution(id=uuid4(), slug=f"sdk-sol-{uuid4().hex[:8]}", name="SDK Sol")
    db_session.add(solution)
    await db_session.flush()
    app = await _seed_app(
        db_session,
        slug=f"sdk-sol-app-{uuid4().hex[:8]}",
        active_deployment_id=uuid4(),
        solution_id=solution.id,
    )

    status = e2e_client.get(
        f"/api/solutions/{solution.id}/sdk/status", headers=platform_admin.headers
    )
    assert status.status_code == 200, status.text
    assert status.json()["actionable_count"] == 1
    assert status.json()["sdk_status"] == "update_available"

    update = e2e_client.post(
        f"/api/solutions/{solution.id}/sdk/update", headers=platform_admin.headers
    )
    assert update.status_code == 200, update.text
    assert [item["application_id"] for item in update.json()["accepted"]] == [
        str(app.id)
    ]

    job = (
        await db_session.execute(
            select(PlatformJob).where(
                PlatformJob.job_type == "application.sdk_update",
                PlatformJob.resource_id == str(app.id),
            )
        )
    ).scalar_one()
    assert job.resource_lock_key == f"application:{app.id}"


async def test_batch_solution_sdk_update_enqueues_actionable_apps_from_selected_solutions(
    e2e_client, platform_admin, db_session
):
    first_solution = Solution(
        id=uuid4(),
        slug=f"sdk-bulk-sol-a-{uuid4().hex[:8]}",
        name="SDK Bulk Sol A",
    )
    second_solution = Solution(
        id=uuid4(),
        slug=f"sdk-bulk-sol-b-{uuid4().hex[:8]}",
        name="SDK Bulk Sol B",
    )
    db_session.add_all([first_solution, second_solution])
    await db_session.flush()
    first_actionable = await _seed_app(
        db_session,
        slug=f"sdk-bulk-action-a-{uuid4().hex[:8]}",
        active_deployment_id=uuid4(),
        solution_id=first_solution.id,
    )
    second_actionable = await _seed_app(
        db_session,
        slug=f"sdk-bulk-action-b-{uuid4().hex[:8]}",
        active_deployment_id=uuid4(),
        solution_id=second_solution.id,
    )
    unavailable_app = await _seed_app(
        db_session,
        slug=f"sdk-bulk-nosource-{uuid4().hex[:8]}",
        active_deployment_id=None,
        solution_id=second_solution.id,
        has_repo_source=False,
        sdk_built_at=None,
    )

    response = e2e_client.post(
        "/api/solutions/sdk/update",
        headers=platform_admin.headers,
        json={"solution_ids": [str(first_solution.id), str(second_solution.id)]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert {item["application_id"] for item in body["accepted"]} == {
        str(first_actionable.id),
        str(second_actionable.id),
    }, body
    assert {
        item["application_id"]: item["solution_id"] for item in body["accepted"]
    } == {
        str(first_actionable.id): str(first_solution.id),
        str(second_actionable.id): str(second_solution.id),
    }
    skipped = {item["application_id"]: item["reason"] for item in body["skipped"]}
    assert skipped[str(unavailable_app.id)] == "source_unavailable"
    jobs = (
        await db_session.execute(
            select(PlatformJob).where(
                PlatformJob.job_type == "application.sdk_update",
                PlatformJob.resource_id.in_(
                    [str(first_actionable.id), str(second_actionable.id)]
                ),
            )
        )
    ).scalars().all()
    assert {job.resource_id for job in jobs} == {
        str(first_actionable.id),
        str(second_actionable.id),
    }


async def test_batch_solution_sdk_update_preserves_solution_deploy_conflict(
    e2e_client, platform_admin, db_session
):
    solution = Solution(
        id=uuid4(),
        slug=f"sdk-bulk-lock-{uuid4().hex[:8]}",
        name="SDK Bulk Lock",
    )
    db_session.add(solution)
    await db_session.flush()
    await _seed_app(
        db_session,
        slug=f"sdk-bulk-lock-app-{uuid4().hex[:8]}",
        active_deployment_id=uuid4(),
        solution_id=solution.id,
    )
    solution_job = PlatformJob(
        job_type="solution.deploy",
        payload_version=1,
        payload={},
        dedupe_key=str(uuid4()),
        resource_lock_key=f"solution:{solution.id}",
        organization_id=None,
        requested_by_user_id=str(platform_admin.user_id),
        requested_by_email=platform_admin.email,
        requested_by_name=platform_admin.email,
        resource_type="solution_deploy",
        resource_id=str(uuid4()),
        title="Deploying solution",
        action_url=f"/solutions/{solution.id}",
        status="running",
    )
    db_session.add(solution_job)
    await db_session.commit()

    response = e2e_client.post(
        "/api/solutions/sdk/update",
        headers=platform_admin.headers,
        json={"solution_ids": [str(solution.id)]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A Solution deployment is already in progress."


async def test_solution_list_and_get_include_sdk_aggregates(
    e2e_client, platform_admin, db_session
):
    actionable_solution = Solution(
        id=uuid4(),
        slug=f"sdk-list-action-{uuid4().hex[:8]}",
        name="SDK List Action",
    )
    no_app_solution = Solution(
        id=uuid4(),
        slug=f"sdk-list-empty-{uuid4().hex[:8]}",
        name="SDK List Empty",
    )
    inactive_solution = Solution(
        id=uuid4(),
        slug=f"sdk-list-inactive-{uuid4().hex[:8]}",
        name="SDK List Inactive",
        status="inactive",
    )
    db_session.add_all([actionable_solution, no_app_solution, inactive_solution])
    await db_session.flush()
    actionable_app = await _seed_app(
        db_session,
        slug=f"sdk-list-app-{uuid4().hex[:8]}",
        active_deployment_id=uuid4(),
        solution_id=actionable_solution.id,
    )
    await _seed_app(
        db_session,
        slug=f"sdk-list-inactive-app-{uuid4().hex[:8]}",
        active_deployment_id=uuid4(),
        solution_id=inactive_solution.id,
    )

    listed = e2e_client.get("/api/solutions", headers=platform_admin.headers)

    assert listed.status_code == 200, listed.text
    by_id = {item["id"]: item for item in listed.json()["solutions"]}
    assert by_id[str(actionable_solution.id)]["sdk_status"] == "update_available"
    assert by_id[str(actionable_solution.id)]["sdk_actionable_count"] == 1
    assert by_id[str(no_app_solution.id)]["sdk_status"] == "not_applicable"
    assert by_id[str(no_app_solution.id)]["sdk_actionable_count"] == 0
    assert by_id[str(inactive_solution.id)]["sdk_status"] == "not_applicable"
    assert by_id[str(inactive_solution.id)]["sdk_actionable_count"] == 0

    got = e2e_client.get(
        f"/api/solutions/{actionable_solution.id}", headers=platform_admin.headers
    )

    assert got.status_code == 200, got.text
    assert got.json()["sdk_status"] == "update_available"
    assert got.json()["sdk_actionable_count"] == 1
    assert actionable_app.solution_id == actionable_solution.id


async def test_solution_deploy_and_solution_app_sdk_update_conflict(
    e2e_client, platform_admin, db_session
):
    solution = Solution(id=uuid4(), slug=f"sdk-lock-{uuid4().hex[:8]}", name="SDK Lock")
    db_session.add(solution)
    await db_session.flush()
    deployment_id = uuid4()
    app = await _seed_app(
        db_session,
        slug=f"sdk-lock-app-{uuid4().hex[:8]}",
        active_deployment_id=deployment_id,
        solution_id=solution.id,
    )
    await _store_source(
        app.id,
        deployment_id,
        {"package.json": b"{}", "index.html": b"<div id='root'></div>"},
    )

    solution_job = PlatformJob(
        job_type="solution.deploy",
        payload_version=1,
        payload={},
        dedupe_key=str(uuid4()),
        resource_lock_key=f"solution:{solution.id}",
        organization_id=None,
        requested_by_user_id=str(platform_admin.user_id),
        requested_by_email=platform_admin.email,
        requested_by_name=platform_admin.email,
        resource_type="solution_deploy",
        resource_id=str(uuid4()),
        title="Deploying solution",
        action_url=f"/solutions/{solution.id}",
        status="running",
    )
    db_session.add(solution_job)
    await db_session.commit()

    blocked_update = e2e_client.post(
        f"/api/applications/{app.id}/sdk/update", headers=platform_admin.headers
    )
    assert blocked_update.status_code == 409

    solution_job.status = "succeeded"
    app_job = PlatformJob(
        job_type="application.sdk_update",
        payload_version=1,
        payload={"application_id": str(app.id)},
        dedupe_key=str(app.id),
        resource_lock_key=f"application:{app.id}",
        organization_id=None,
        requested_by_user_id=str(platform_admin.user_id),
        requested_by_email=platform_admin.email,
        requested_by_name=platform_admin.email,
        resource_type="application",
        resource_id=str(app.id),
        title="Updating SDK",
        action_url=f"/apps/{app.slug}",
        status="running",
    )
    db_session.add(app_job)
    await db_session.commit()

    blocked_deploy = e2e_client.post(
        f"/api/solutions/{solution.id}/deploy",
        headers=platform_admin.headers,
        json={"apps": []},
    )
    assert blocked_deploy.status_code == 409
