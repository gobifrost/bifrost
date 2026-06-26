from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.models.orm.solution_export_jobs import SolutionExportJob
from src.services.solutions.export_jobs import decrypt_export_options

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers: dict[str, str]) -> str:
    slug = f"backup-jobs-{uuid.uuid4().hex[:8]}"
    response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": "Backup Jobs",
            "organization_id": None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _job_payload(password: str = "correct horse battery staple") -> dict:
    return {
        "options": {
            "include_configs": True,
            "include_secrets": False,
            "include_tables": False,
            "include_files": False,
            "password": password,
        }
    }


@pytest.mark.asyncio
async def test_create_list_get_and_pending_download(
    e2e_client,
    platform_admin,
    db_session,
) -> None:
    headers = platform_admin.headers
    solution_id = _create_solution(e2e_client, headers)

    create_response = e2e_client.post(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
        json=_job_payload(),
    )

    assert create_response.status_code == 202, create_response.text
    created = create_response.json()
    job_id = created["id"]
    assert created["solution_id"] == solution_id
    assert created["status"] == "pending"
    assert created["progress_percent"] == 0
    assert created["message"] == "queued"
    assert created["artifact_size_bytes"] is None
    assert created["artifact_sha256"] is None
    assert created["download_url"] is None

    row = await db_session.get(SolutionExportJob, uuid.UUID(job_id))
    assert row is not None
    assert row.artifact_storage_key is None
    assert row.notification_id is not None
    assert row.encrypted_options is not None
    assert "correct horse battery staple" not in row.encrypted_options
    assert decrypt_export_options(row.encrypted_options).password == (
        "correct horse battery staple"
    )

    download_response = e2e_client.get(
        f"/api/solutions/export-jobs/{job_id}/download",
        headers=headers,
    )
    assert download_response.status_code == 409, download_response.text

    get_response = e2e_client.get(
        f"/api/solutions/export-jobs/{job_id}",
        headers=headers,
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["id"] == job_id

    list_response = e2e_client.get(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
    )
    assert list_response.status_code == 200, list_response.text
    jobs = list_response.json()["jobs"]
    assert jobs
    assert jobs[0]["id"] == job_id


def test_export_job_rejects_empty_content_selection(e2e_client, platform_admin) -> None:
    headers = platform_admin.headers
    solution_id = _create_solution(e2e_client, headers)

    response = e2e_client.post(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
        json={
            "options": {
                "include_configs": False,
                "include_secrets": False,
                "include_tables": False,
                "include_files": False,
                "password": "pw",
            }
        },
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_export_job_rejects_missing_password_without_persisting(
    e2e_client,
    platform_admin,
    db_session,
) -> None:
    headers = platform_admin.headers
    solution_id = _create_solution(e2e_client, headers)

    response = e2e_client.post(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
        json={
            "options": {
                "include_configs": True,
                "include_secrets": False,
                "include_tables": False,
                "include_files": False,
                "password": None,
            }
        },
    )

    assert response.status_code == 422, response.text
    rows = (
        await db_session.execute(
            select(SolutionExportJob).where(
                SolutionExportJob.solution_id == uuid.UUID(solution_id)
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_list_export_jobs_orders_recent_first(
    e2e_client,
    platform_admin,
    db_session,
) -> None:
    headers = platform_admin.headers
    solution_id = _create_solution(e2e_client, headers)

    first = e2e_client.post(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
        json=_job_payload("first-password"),
    )
    assert first.status_code == 202, first.text
    second = e2e_client.post(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
        json=_job_payload("second-password"),
    )
    assert second.status_code == 202, second.text

    rows = (
        await db_session.execute(
            select(SolutionExportJob)
            .where(SolutionExportJob.solution_id == uuid.UUID(solution_id))
            .order_by(SolutionExportJob.created_at.desc())
        )
    ).scalars().all()
    assert len(rows) == 2

    response = e2e_client.get(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert [job["id"] for job in response.json()["jobs"][:2]] == [
        str(rows[0].id),
        str(rows[1].id),
    ]
