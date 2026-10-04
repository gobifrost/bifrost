"""Backup download links retain admin artifact guards and bounded validity."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from src.routers import solutions


def completed_job():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        solution_id=uuid4(),
        organization_id=None,
        requested_by_id=None,
        status="completed",
        progress_percent=100,
        message=None,
        failure_message=None,
        artifact_storage_key="solution-exports/test/backup.zip",
        artifact_filename="backup.zip",
        artifact_size_bytes=10_000_000_000,
        artifact_sha256="a" * 64,
        expires_at=now + timedelta(hours=1),
        completed_at=now,
        created_at=now,
        updated_at=now,
    )


async def test_download_link_signs_only_completed_artifact_without_reading_bytes(
    monkeypatch,
):
    job = completed_job()
    db = SimpleNamespace(get=AsyncMock(return_value=job))
    storage = SimpleNamespace(
        generate_presigned_download_url=AsyncMock(
            return_value="https://storage.test/signed-download"
        ),
        iter_raw_s3_chunks=AsyncMock(
            side_effect=AssertionError("the API must not read the artifact")
        ),
    )
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService", lambda _: storage
    )
    response = Response()
    result = await solutions.create_solution_export_download_link(
        job.id,
        SimpleNamespace(db=db),
        SimpleNamespace(),
        response,
    )
    assert result.url == "https://storage.test/signed-download"
    assert result.filename == "backup.zip"
    assert result.expires_in == 600
    assert response.headers["Cache-Control"] == "no-store"
    storage.generate_presigned_download_url.assert_awaited_once_with(
        job.artifact_storage_key,
        expires_in=600,
        response_content_type="application/zip",
        response_content_disposition='attachment; filename="backup.zip"',
    )
    storage.iter_raw_s3_chunks.assert_not_called()


@pytest.mark.parametrize("state", ["missing", "pending", "expired", "missing_artifact"])
async def test_download_link_rejects_unavailable_artifacts_before_presigning(
    monkeypatch, state
):
    job = completed_job()
    if state == "missing":
        job = None
    elif state == "pending":
        job.status = "pending"
    elif state == "expired":
        job.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        job.artifact_storage_key = None
    signer = AsyncMock(
        side_effect=AssertionError("unavailable artifact must not be signed")
    )
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService",
        lambda _: SimpleNamespace(generate_presigned_download_url=signer),
    )
    with pytest.raises(HTTPException) as error:
        await solutions.create_solution_export_download_link(
            uuid4(),
            SimpleNamespace(db=SimpleNamespace(get=AsyncMock(return_value=job))),
            SimpleNamespace(),
            Response(),
        )
    assert error.value.status_code == (404 if state == "missing" else 409)
    signer.assert_not_called()


async def test_link_validity_cannot_exceed_artifact_expiry(monkeypatch):
    job = completed_job()
    job.expires_at = datetime.now(timezone.utc) + timedelta(seconds=20)
    signer = AsyncMock(return_value="https://storage.test/signed-download")
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService",
        lambda _: SimpleNamespace(generate_presigned_download_url=signer),
    )
    result = await solutions.create_solution_export_download_link(
        job.id,
        SimpleNamespace(db=SimpleNamespace(get=AsyncMock(return_value=job))),
        SimpleNamespace(),
        Response(),
    )
    assert 1 <= result.expires_in <= 20
    assert signer.await_args.kwargs["expires_in"] == result.expires_in
