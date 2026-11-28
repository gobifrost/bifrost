from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from types import SimpleNamespace

from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.services.solutions.deploy import SolutionBundle
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.schedulers import solution_export_jobs as scheduler
from src.models.contracts.solutions import SolutionExportOptions
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.services.solutions.export_jobs import encrypt_export_options


@pytest.fixture(autouse=True)
def patch_scheduler_db(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    @asynccontextmanager
    async def _test_db_context() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db_session
            await db_session.commit()
        except BaseException:
            await db_session.rollback()
            raise

    monkeypatch.setattr(scheduler, "get_db_context", _test_db_context)


@pytest.fixture
def context():
    return SimpleNamespace(
        organization_id=None, file_compute=AsyncMock(),
        authorize_effect=AsyncMock(return_value=True),
    )


@pytest.fixture(autouse=True)
def export_boundary(monkeypatch):
    @asynccontextmanager
    async def lock(_solution_id):
        yield

    async def capture(_db, solution, options, path):
        assert options.password == "pw"
        path.write_bytes(b"source")
        return SolutionBundle(solution=solution)

    async def append(_source, _bundle, _db, destination, *, password):
        assert password == "pw"
        destination.write_bytes(b"zip")

    monkeypatch.setattr(scheduler, "solution_write_lock", lock)
    monkeypatch.setattr(scheduler, "capture_export_source", capture)
    monkeypatch.setattr(scheduler, "pack_solution_archive", AsyncMock(return_value=b"packed"))
    monkeypatch.setattr(scheduler, "append_export_runtime_content", append)
    monkeypatch.setattr(scheduler, "delete_solution_export_artifact", AsyncMock())


async def _solution(db_session) -> Solution:
    solution = Solution(
        id=uuid4(),
        slug=f"backup-{uuid4().hex[:8]}",
        name="Backup Test",
        version="1.2.3",
        organization_id=None,
    )
    db_session.add(solution)
    await db_session.flush()
    return solution


def _options() -> SolutionExportOptions:
    return SolutionExportOptions(
        include_configs=True,
        include_secrets=False,
        include_tables=False,
        include_files=False,
        password="pw",
    )


@pytest.mark.asyncio
async def test_pending_job_completes_and_clears_options(
    db_session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context,
) -> None:
    solution = await _solution(db_session)
    job = SolutionExportJob(
        solution_id=solution.id,
        status="pending",
        encrypted_options=encrypt_export_options(_options()),
        artifact_filename="backup.zip",
    )
    db_session.add(job)
    await db_session.commit()

    async def fake_upload_artifact(self, storage_key, artifact_path_arg):
        assert storage_key.startswith(f"solution-exports/{solution.id}/{job.id}/")
        assert artifact_path_arg.read_bytes() == b"zip"
        return "a" * 64, 3

    monkeypatch.setattr(scheduler, "_update_notification", AsyncMock())
    monkeypatch.setattr(
        scheduler.SolutionExportArtifactService,
        "upload_artifact",
        fake_upload_artifact,
    )

    completed = await scheduler.run_solution_export_job(job.id, context=context)

    assert completed
    await db_session.refresh(job)
    assert job.status == "completed"
    assert job.progress_percent == 100
    assert job.message == "Backup ready"
    assert job.failure_message is None
    assert job.encrypted_options is None
    assert job.artifact_storage_key.startswith(f"solution-exports/{solution.id}/{job.id}/")
    assert job.artifact_size_bytes == 3
    assert job.artifact_sha256 == "a" * 64
    assert job.expires_at is not None
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_missing_encrypted_options_fails_and_clears_job(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    context,
) -> None:
    solution = await _solution(db_session)
    job = SolutionExportJob(
        solution_id=solution.id,
        status="pending",
        encrypted_options=None,
    )
    db_session.add(job)
    await db_session.commit()

    monkeypatch.setattr(scheduler, "_update_notification", AsyncMock())

    with pytest.raises(PlatformJobFailure, match="Missing backup export options"):
        await scheduler.run_solution_export_job(job.id, context=context)

    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.message == "Backup failed"
    assert job.failure_message == "Missing backup export options"
    assert job.encrypted_options is None
    assert job.progress_percent == 100


@pytest.mark.asyncio
async def test_running_job_can_resume_after_platform_runner_retry(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    context,
) -> None:
    solution = await _solution(db_session)
    job = SolutionExportJob(
        solution_id=solution.id,
        status="running",
        progress_percent=5,
        message="Building backup",
        claimed_at=datetime.now(timezone.utc) - timedelta(hours=3),
        encrypted_options=encrypt_export_options(_options()),
    )
    db_session.add(job)
    await db_session.commit()

    monkeypatch.setattr(scheduler, "_update_notification", AsyncMock())

    monkeypatch.setattr(
        scheduler,
        "capture_export_source",
        AsyncMock(side_effect=RuntimeError("storage unavailable")),
    )
    completed = await scheduler.run_solution_export_job(job.id, context=context)

    assert not completed
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.encrypted_options is None


@pytest.mark.asyncio
async def test_cleanup_expired_completed_job_deletes_artifact_and_marks_expired(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    context,
) -> None:
    solution = await _solution(db_session)
    artifact_key = f"solution-exports/{solution.id}/{uuid4()}.zip"
    job = SolutionExportJob(
        solution_id=solution.id,
        status="completed",
        progress_percent=100,
        message="Backup ready",
        artifact_storage_key=artifact_key,
        artifact_size_bytes=42,
        artifact_sha256="b" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        encrypted_options=encrypt_export_options(_options()),
    )
    db_session.add(job)
    await db_session.commit()

    deleted: list[str | None] = []

    async def fake_delete(db, storage_key):  # noqa: ANN001
        deleted.append(storage_key)

    monkeypatch.setattr(scheduler, "delete_solution_export_artifact", fake_delete)

    cleaned = await scheduler.cleanup_expired_solution_export_jobs(batch_limit=1)

    assert cleaned == 1
    await db_session.refresh(job)
    assert deleted == [artifact_key]
    assert job.status == "expired"
    assert job.message == "Backup expired"
    assert job.artifact_storage_key is None
    assert job.artifact_size_bytes == 42
    assert job.artifact_sha256 == "b" * 64
    assert job.encrypted_options is None

    assert await scheduler.cleanup_expired_solution_export_jobs(batch_limit=1) == 0


@pytest.mark.asyncio
async def test_lost_owner_after_upload_cannot_publish_or_clear_retry_input(db_session, monkeypatch, context):
    solution = await _solution(db_session)
    job = SolutionExportJob(solution_id=solution.id, encrypted_options=encrypt_export_options(_options()))
    db_session.add(job)
    await db_session.commit()
    uploaded = []

    async def upload(_self, key, _path):
        uploaded.append(key)
        context.authorize_effect.return_value = False
        return "b" * 64, 3

    monkeypatch.setattr(scheduler.SolutionExportArtifactService, "upload_artifact", upload)
    with pytest.raises(PlatformJobCancelled):
        await scheduler.run_solution_export_job(job.id, context=context)
    await db_session.refresh(job)
    assert job.status == "running"
    assert job.encrypted_options is not None
    assert job.artifact_sha256 is None
    assert job.artifact_storage_key == uploaded[0]


@pytest.mark.asyncio
async def test_completed_receipt_resumes_without_decrypting_or_uploading(db_session, monkeypatch, context):
    solution = await _solution(db_session)
    job = SolutionExportJob(
        solution_id=solution.id, status="completed", encrypted_options=None,
        artifact_storage_key=f"solution-exports/{solution.id}/receipt.zip",
    )
    db_session.add(job)
    await db_session.commit()
    capture, upload = AsyncMock(), AsyncMock()
    monkeypatch.setattr(scheduler, "capture_export_source", capture)
    monkeypatch.setattr(scheduler.SolutionExportArtifactService, "upload_artifact", upload)
    monkeypatch.setattr(scheduler, "_update_notification", AsyncMock())
    assert await scheduler.run_solution_export_job(job.id, context=context)
    capture.assert_not_awaited()
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_compute_holds_no_database_session(db_session, monkeypatch, context):
    solution = await _solution(db_session)
    job = SolutionExportJob(solution_id=solution.id, encrypted_options=encrypt_export_options(_options()))
    db_session.add(job)
    await db_session.commit()
    active = 0

    @asynccontextmanager
    async def database():
        nonlocal active
        active += 1
        try:
            yield db_session
            await db_session.commit()
        finally:
            active -= 1

    async def pack(_source, compute):
        assert active == 0
        assert compute is context.file_compute
        return b"packed"

    monkeypatch.setattr(scheduler, "get_db_context", database)
    monkeypatch.setattr(scheduler, "pack_solution_archive", pack)
    monkeypatch.setattr(scheduler.SolutionExportArtifactService, "upload_artifact", AsyncMock(return_value=("a" * 64, 3)))
    assert await scheduler.run_solution_export_job(job.id, context=context)


@pytest.mark.asyncio
async def test_expired_terminal_runner_loss_cleans_unpublished_artifact(db_session, monkeypatch):
    from src.models.orm.platform_jobs import PlatformJob
    from sqlalchemy import delete

    solution = await _solution(db_session)
    job = SolutionExportJob(
        solution_id=solution.id, status="running", encrypted_options=encrypt_export_options(_options()),
        artifact_storage_key=f"solution-exports/{solution.id}/orphan.zip",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(job)
    await db_session.flush()
    canonical = PlatformJob(
        job_type="solution.export", payload={}, status="failed",
        resource_type="solution_export", resource_id=str(job.id),
        requested_by_user_id=str(uuid4()), requested_by_email="test@example.com",
        requested_by_name="Test", title="Export",
    )
    db_session.add(canonical)
    await db_session.commit()
    deletion = AsyncMock()
    monkeypatch.setattr(scheduler, "delete_solution_export_artifact", deletion)
    try:
        assert await scheduler.cleanup_expired_solution_export_jobs() == 1
        deletion.assert_awaited_once()
        await db_session.refresh(job)
        assert job.status == "failed" and job.encrypted_options is None
        assert job.artifact_storage_key is None
    finally:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == canonical.id))
        await db_session.commit()
