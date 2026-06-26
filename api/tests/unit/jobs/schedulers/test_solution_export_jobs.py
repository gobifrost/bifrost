from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock
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
        yield db_session

    monkeypatch.setattr(scheduler, "get_db_context", _test_db_context)


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

    artifact_path = tmp_path / "backup.zip"
    artifact_path.write_bytes(b"zip")

    async def fake_build_zip_tempfile(self, solution_arg, options_arg):  # noqa: ANN001
        assert solution_arg.id == solution.id
        assert options_arg.password == "pw"
        return artifact_path

    async def fake_upload_artifact(self, storage_key, artifact_path_arg):  # noqa: ANN001
        assert storage_key == f"solution-exports/{solution.id}/{job.id}.zip"
        assert artifact_path_arg == artifact_path
        return "a" * 64, 3

    monkeypatch.setattr(scheduler, "_update_notification", AsyncMock())
    monkeypatch.setattr(
        scheduler.SolutionExportArtifactService,
        "build_zip_tempfile",
        fake_build_zip_tempfile,
    )
    monkeypatch.setattr(
        scheduler.SolutionExportArtifactService,
        "upload_artifact",
        fake_upload_artifact,
    )

    processed, failed = await scheduler.process_solution_export_jobs(batch_limit=1)

    assert (processed, failed) == (1, 0)
    await db_session.refresh(job)
    assert job.status == "completed"
    assert job.progress_percent == 100
    assert job.message == "Backup ready"
    assert job.failure_message is None
    assert job.encrypted_options is None
    assert job.artifact_storage_key == f"solution-exports/{solution.id}/{job.id}.zip"
    assert job.artifact_size_bytes == 3
    assert job.artifact_sha256 == "a" * 64
    assert job.expires_at is not None
    assert job.completed_at is not None
    assert not artifact_path.exists()


@pytest.mark.asyncio
async def test_missing_encrypted_options_fails_and_clears_job(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
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

    processed, failed = await scheduler.process_solution_export_jobs(batch_limit=1)

    assert (processed, failed) == (1, 1)
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.message == "Backup failed"
    assert job.failure_message == "Missing backup export options"
    assert job.encrypted_options is None
    assert job.progress_percent == 100


@pytest.mark.asyncio
async def test_stale_running_job_is_marked_failed_and_cleared(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
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

    processed, failed = await scheduler.process_solution_export_jobs(batch_limit=1)

    assert (processed, failed) == (1, 1)
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.message == "Backup failed"
    assert job.failure_message == "Backup export job was abandoned and will need to be retried"
    assert job.encrypted_options is None
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_running_job_without_claim_time_is_marked_failed(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution = await _solution(db_session)
    job = SolutionExportJob(
        solution_id=solution.id,
        status="running",
        progress_percent=5,
        message="Building backup",
        claimed_at=None,
        encrypted_options=encrypt_export_options(_options()),
    )
    db_session.add(job)
    await db_session.commit()

    monkeypatch.setattr(scheduler, "_update_notification", AsyncMock())

    processed, failed = await scheduler.process_solution_export_jobs(batch_limit=1)

    assert (processed, failed) == (1, 1)
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.encrypted_options is None


@pytest.mark.asyncio
async def test_cleanup_expired_completed_job_deletes_artifact_and_marks_expired(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
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
