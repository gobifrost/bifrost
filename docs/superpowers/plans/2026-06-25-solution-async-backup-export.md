# Solution Async Backup Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Solution Backup export from a long browser request to scheduler-owned durable export jobs with notification progress, export history, retention cleanup, and Playwright coverage.

**Architecture:** Package export stays synchronous. Backup export creates a `solution_export_jobs` row and returns immediately; the scheduler claims queued jobs with `FOR UPDATE SKIP LOCKED`, builds the existing Zip64/encrypted export into a temporary file, uploads it to object storage, marks the job complete, and updates Notification Center. Solution detail gets an Exports tab that polls job status and downloads completed artifacts through an authenticated endpoint.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, APScheduler, S3-compatible storage via existing `FileStorageService`/`S3StorageClient`, Pydantic contracts, React, React Query, Vitest, Playwright.

---

## File Structure

Create:
- `api/src/models/orm/solution_export_jobs.py` — ORM row for durable export jobs.
- `api/alembic/versions/20260625_solution_export_jobs.py` — migration for the job table.
- `api/src/services/solutions/export_job_artifacts.py` — object-storage helper for final/temp export artifacts.
- `api/src/services/solutions/export_jobs.py` — job creation, export build, status transitions, cleanup, and download lookup.
- `api/src/jobs/schedulers/solution_export_jobs.py` — scheduler tick functions for processing queued jobs and expiring old artifacts.
- `api/tests/unit/test_solution_export_jobs_contracts.py` — DTO validation and serialization.
- `api/tests/unit/test_solution_export_job_artifacts.py` — storage key and chunk-upload behavior.
- `api/tests/unit/jobs/schedulers/test_solution_export_jobs.py` — scheduler claim/process/failure/cleanup behavior.
- `api/tests/e2e/platform/test_solution_export_jobs.py` — endpoint lifecycle and download behavior.
- `client/e2e/solution-backup-export.admin.spec.ts` — admin browser journey for queue, completion, and download.

Modify:
- `api/src/config.py` — add retention/stale settings.
- `api/src/models/contracts/solutions.py` — add export job request/response DTOs.
- `api/src/models/orm/__init__.py` — export `SolutionExportJob`.
- `api/src/routers/solutions.py` — add export-job endpoints; keep shareable export direct.
- `api/src/scheduler/main.py` — register process and cleanup jobs.
- `client/src/lib/v1.d.ts` — regenerated OpenAPI types.
- `client/src/services/solutions.ts` and `client/src/services/solutions.test.ts` — export-job API client helpers.
- `client/src/components/solutions/ExportSolutionDialog.tsx` and `.test.tsx` — distinguish Package direct download from Backup queueing.
- `client/src/components/layout/NotificationCenter.tsx` and `client/src/components/layout/NotificationCenter.test.tsx` — add `download_solution_export` action.
- `client/src/pages/SolutionDetail.tsx` and `.test.tsx` — add Exports tab and polling/download behavior.

Do not modify worker consumers or RabbitMQ publishing for export execution.

---

### Task 1: Backend Contract, ORM, Migration, And Settings

**Files:**
- Create: `api/src/models/orm/solution_export_jobs.py`
- Create: `api/alembic/versions/20260625_solution_export_jobs.py`
- Create: `api/tests/unit/test_solution_export_jobs_contracts.py`
- Modify: `api/src/models/contracts/solutions.py`
- Modify: `api/src/models/orm/__init__.py`
- Modify: `api/src/config.py`

- [ ] **Step 1: Write failing contract tests**

Add `api/tests/unit/test_solution_export_jobs_contracts.py`:

```python
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from src.models.contracts.solutions import (
    SolutionExportJobCreate,
    SolutionExportJobStatus,
)


def test_export_job_create_requires_password_when_any_content_selected() -> None:
    body = SolutionExportJobCreate(
        password="pw",
        include_values=True,
        include_files=False,
        include_data=False,
    )

    assert body.password == "pw"
    assert body.include_values is True


def test_export_job_create_rejects_no_selected_content() -> None:
    try:
        SolutionExportJobCreate(
            password="pw",
            include_values=False,
            include_files=False,
            include_data=False,
        )
    except ValidationError as exc:
        assert "Select at least one backup content type" in str(exc)
    else:
        raise AssertionError("expected validation error")


def test_export_job_status_serializes_downloadable_state() -> None:
    now = datetime.now(timezone.utc)
    job = SolutionExportJobStatus(
        id=uuid4(),
        solution_id=uuid4(),
        requested_by=uuid4(),
        mode="full",
        include_values=True,
        include_files=True,
        include_data=False,
        status="completed",
        filename="demo.zip",
        size_bytes=123,
        error=None,
        created_at=now,
        started_at=now,
        completed_at=now,
        expires_at=now,
        updated_at=now,
        downloadable=True,
    )

    dumped = job.model_dump(mode="json")
    assert dumped["mode"] == "full"
    assert dumped["status"] == "completed"
    assert dumped["downloadable"] is True
```

- [ ] **Step 2: Run contract test to verify it fails**

Run:

```bash
./test.sh tests/unit/test_solution_export_jobs_contracts.py -q
```

Expected: import failure for missing `SolutionExportJobCreate` / `SolutionExportJobStatus`.

- [ ] **Step 3: Add DTOs**

Add to `api/src/models/contracts/solutions.py` after `SolutionDeployJobStatus`:

```python
class SolutionExportJobCreate(BaseModel):
    """Request body for scheduler-owned Backup export."""

    password: str = Field(min_length=1)
    include_values: bool = True
    include_files: bool = True
    include_data: bool = False

    @model_validator(mode="after")
    def _requires_content(self) -> "SolutionExportJobCreate":
        if not (self.include_values or self.include_files or self.include_data):
            raise ValueError("Select at least one backup content type")
        return self


class SolutionExportJobStatus(BaseModel):
    """Public state for one scheduler-owned Backup export job."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    solution_id: UUID
    requested_by: UUID
    mode: Literal["full"] = "full"
    include_values: bool
    include_files: bool
    include_data: bool
    status: Literal["queued", "running", "completed", "failed", "expired"]
    filename: str
    size_bytes: int | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime
    downloadable: bool = False


class SolutionExportJobsList(BaseModel):
    jobs: list[SolutionExportJobStatus] = Field(default_factory=list)
```

Also update the existing import line in `solutions.py` contracts file to include:

```python
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
```

- [ ] **Step 4: Add settings**

Add to `api/src/config.py` near file/storage settings:

```python
    solution_export_retention_hours: int = Field(
        default=168,
        ge=1,
        description="Hours completed Solution backup exports remain downloadable",
    )

    solution_export_stale_running_minutes: int = Field(
        default=60,
        ge=5,
        description="Minutes before a running Solution export job is reset to queued",
    )
```

- [ ] **Step 5: Add ORM model**

Create `api/src/models/orm/solution_export_jobs.py`:

```python
"""Durable scheduler-owned Solution backup export jobs."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base

SolutionExportJobState = Literal["queued", "running", "completed", "failed", "expired"]


class SolutionExportJob(Base):
    __tablename__ = "solution_export_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="full")
    include_values: Mapped[bool] = mapped_column(nullable=False, default=True)
    include_files: Mapped[bool] = mapped_column(nullable=False, default=True)
    include_data: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    temp_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notification_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_envelope: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

Modify `api/src/models/orm/__init__.py`:

```python
from src.models.orm.solution_export_jobs import SolutionExportJob
```

and add `"SolutionExportJob"` to `__all__`.

- [ ] **Step 6: Add migration**

Create `api/alembic/versions/20260625_solution_export_jobs.py`:

```python
"""solution export jobs

Revision ID: 20260625_solution_export_jobs
Revises: 20260624_solution_file_locations
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_solution_export_jobs"
down_revision: str = "20260624_solution_file_locations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solution_export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="full"),
        sa.Column("include_values", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("include_files", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("include_data", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("temp_storage_key", sa.String(length=1024), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("notification_id", sa.String(length=64), nullable=True),
        sa.Column("password_envelope", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_solution_export_jobs_solution_id", "solution_export_jobs", ["solution_id"])
    op.create_index("ix_solution_export_jobs_requested_by", "solution_export_jobs", ["requested_by"])
    op.create_index("ix_solution_export_jobs_status", "solution_export_jobs", ["status"])
    op.create_index("ix_solution_export_jobs_solution_created", "solution_export_jobs", ["solution_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_solution_export_jobs_solution_created", table_name="solution_export_jobs")
    op.drop_index("ix_solution_export_jobs_status", table_name="solution_export_jobs")
    op.drop_index("ix_solution_export_jobs_requested_by", table_name="solution_export_jobs")
    op.drop_index("ix_solution_export_jobs_solution_id", table_name="solution_export_jobs")
    op.drop_table("solution_export_jobs")
```

- [ ] **Step 7: Run test and migration smoke**

Run:

```bash
./test.sh tests/unit/test_solution_export_jobs_contracts.py -q
./test.sh tests/unit/test_solutions_orm.py -q
```

Expected: contract tests pass; ORM test suite still passes after model import.

- [ ] **Step 8: Commit**

```bash
git add api/src/models/contracts/solutions.py api/src/models/orm/solution_export_jobs.py api/src/models/orm/__init__.py api/src/config.py api/alembic/versions/20260625_solution_export_jobs.py api/tests/unit/test_solution_export_jobs_contracts.py
git commit -m "Add solution export job model"
```

---

### Task 2: Artifact Storage And Export Build Service

**Files:**
- Create: `api/src/services/solutions/export_job_artifacts.py`
- Create: `api/src/services/solutions/export_jobs.py`
- Create: `api/tests/unit/test_solution_export_job_artifacts.py`
- Modify: `api/src/services/solutions/export.py`

- [ ] **Step 1: Write failing artifact tests**

Create `api/tests/unit/test_solution_export_job_artifacts.py`:

```python
from uuid import uuid4

import pytest

from src.services.solutions.export_job_artifacts import SolutionExportArtifactStorage


def test_artifact_keys_are_scoped_by_job_id() -> None:
    job_id = uuid4()
    storage = SolutionExportArtifactStorage(job_id)

    assert storage.temp_key.endswith(f"{job_id}.zip.tmp")
    assert storage.final_key.endswith(f"{job_id}.zip")
    assert storage.temp_key.startswith("_solution_exports/")


@pytest.mark.asyncio
async def test_upload_path_uses_raw_chunk_writer(monkeypatch, tmp_path) -> None:
    source = tmp_path / "export.zip"
    source.write_bytes(b"zip-bytes")
    job_id = uuid4()
    calls = []

    async def fake_write(self, path, chunks, *, content_type=None):  # noqa: ANN001
        collected = bytearray()
        async for chunk in chunks:
            collected.extend(chunk)
        calls.append((path, bytes(collected), content_type))
        return "hash", len(collected)

    monkeypatch.setattr(
        "src.services.file_storage.service.FileStorageService.write_raw_chunks_to_s3",
        fake_write,
    )

    storage = SolutionExportArtifactStorage(job_id)
    size = await storage.upload_temp_file_from_path(source)

    assert size == 9
    assert calls == [(storage.temp_key, b"zip-bytes", "application/zip")]
```

- [ ] **Step 2: Run artifact test to verify it fails**

Run:

```bash
./test.sh tests/unit/test_solution_export_job_artifacts.py -q
```

Expected: import failure for missing artifact storage.

- [ ] **Step 3: Implement artifact storage**

Create `api/src/services/solutions/export_job_artifacts.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.services.file_storage import FileStorageService

EXPORT_PREFIX = "_solution_exports"
CHUNK_SIZE = 8 * 1024 * 1024


class SolutionExportArtifactStorage:
    def __init__(self, job_id: UUID | str):
        self.job_id = str(job_id)
        self.temp_key = f"{EXPORT_PREFIX}/tmp/{self.job_id}.zip.tmp"
        self.final_key = f"{EXPORT_PREFIX}/completed/{self.job_id}.zip"

    async def upload_temp_file_from_path(self, path: Path | str) -> int:
        source = Path(path)

        async def chunks() -> AsyncIterator[bytes]:
            with source.open("rb") as fh:
                while chunk := fh.read(CHUNK_SIZE):
                    yield chunk

        storage = FileStorageService(db=None)  # type: ignore[arg-type]
        _, size = await storage.write_raw_chunks_to_s3(
            self.temp_key,
            chunks(),
            content_type="application/zip",
        )
        return size

    async def promote_temp_to_final(self) -> str:
        storage = FileStorageService(db=None)  # type: ignore[arg-type]
        async with storage._s3_storage.get_client() as s3:  # noqa: SLF001
            await s3.copy_object(
                Bucket=storage.settings.s3_bucket,
                Key=self.final_key,
                CopySource={"Bucket": storage.settings.s3_bucket, "Key": self.temp_key},
                ContentType="application/zip",
                MetadataDirective="REPLACE",
            )
            await s3.delete_object(Bucket=storage.settings.s3_bucket, Key=self.temp_key)
        return self.final_key

    async def delete_key(self, key: str | None) -> None:
        if not key:
            return
        storage = FileStorageService(db=None)  # type: ignore[arg-type]
        await storage.delete_raw_from_s3(key)

    def iter_download_chunks(self, key: str) -> AsyncIterator[bytes]:
        storage = FileStorageService(db=None)  # type: ignore[arg-type]
        return storage.iter_raw_s3_chunks(key)
```

Keep the `type: ignore[arg-type]` comments in `export_job_artifacts.py`; do not spread additional private storage access outside that helper module.

- [ ] **Step 4: Extract current export build into reusable service function**

In `api/src/services/solutions/export_jobs.py`, add:

```python
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution as SolutionORM
from src.services.solutions.capture import SolutionCaptureService
from src.services.solutions.export import (
    add_live_content_to_workspace_zip_file,
    build_workspace_zip_for_export,
)
from src.services.solutions.export_job_artifacts import SolutionExportArtifactStorage
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage


def encrypt_export_password(password: str) -> str:
    return encrypt_secret(password)


def decrypt_export_password(envelope: str) -> str:
    return decrypt_secret(envelope)


def export_job_status(row: SolutionExportJob) -> str:
    return str(row.status)


async def build_backup_zip_to_path(
    db: AsyncSession,
    *,
    solution: SolutionORM,
    include_values: bool,
    include_files: bool,
    include_data: bool,
    password: str,
    out_path: Path,
) -> None:
    artifact = SolutionSourceArtifactStorage(solution.id)
    stored_source = tempfile.NamedTemporaryFile(
        prefix=f"bifrost-solution-source-{solution.id}-",
        suffix=".zip",
        delete=False,
    )
    source_path = Path(stored_source.name)
    stored_source.close()
    try:
        has_stored_source = await artifact.copy_to_path(source_path)
        bundle = await SolutionCaptureService(db).bundle_for(
            solution,
            include_imports=True,
            include_values=include_values,
            include_data=include_data,
            include_files=include_files,
        )
        if has_stored_source:
            await add_live_content_to_workspace_zip_file(
                source_path,
                bundle,
                db,
                out_path,
                password=password,
            )
        else:
            await build_workspace_zip_for_export(
                bundle,
                db,
                out_path,
                password=password,
            )
    finally:
        source_path.unlink(missing_ok=True)


def completed_expiry(now: datetime | None = None) -> datetime:
    settings = get_settings()
    base = now or datetime.now(timezone.utc)
    return base + timedelta(hours=settings.solution_export_retention_hours)
```

Then plan Task 4 will call this from scheduler. Do not move router logic yet.

- [ ] **Step 5: Run artifact tests**

Run:

```bash
./test.sh tests/unit/test_solution_export_job_artifacts.py -q
cd api && pyright
```

Expected: artifact tests pass and pyright has 0 errors. If pyright dislikes private storage access in `promote_temp_to_final`, move copy/delete into a public `copy_raw_s3_key(source, dest, content_type)` method on `FileStorageService` and test that helper.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solutions/export_job_artifacts.py api/src/services/solutions/export_jobs.py api/tests/unit/test_solution_export_job_artifacts.py api/src/services/file_storage/service.py
git commit -m "Add solution export artifact helpers"
```

---

### Task 3: Export Job API Endpoints

**Files:**
- Modify: `api/src/routers/solutions.py`
- Modify: `api/src/services/solutions/export_jobs.py`
- Create: `api/tests/e2e/platform/test_solution_export_jobs.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `api/tests/e2e/platform/test_solution_export_jobs.py`:

```python
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers) -> str:
    slug = f"export-job-{uuid.uuid4().hex[:8]}"
    resp = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": "Export Job", "organization_id": None},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def test_create_export_job_returns_queued_job(e2e_client, platform_admin):
    sol_id = _create_solution(e2e_client, platform_admin.headers)

    resp = e2e_client.post(
        f"/api/solutions/{sol_id}/export-jobs",
        headers=platform_admin.headers,
        json={"password": "pw", "include_values": True, "include_files": False, "include_data": False},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["solution_id"] == sol_id
    assert body["status"] == "queued"
    assert body["downloadable"] is False


def test_create_export_job_rejects_no_content(e2e_client, platform_admin):
    sol_id = _create_solution(e2e_client, platform_admin.headers)

    resp = e2e_client.post(
        f"/api/solutions/{sol_id}/export-jobs",
        headers=platform_admin.headers,
        json={"password": "pw", "include_values": False, "include_files": False, "include_data": False},
    )

    assert resp.status_code == 422


def test_download_queued_job_returns_409(e2e_client, platform_admin):
    sol_id = _create_solution(e2e_client, platform_admin.headers)
    created = e2e_client.post(
        f"/api/solutions/{sol_id}/export-jobs",
        headers=platform_admin.headers,
        json={"password": "pw", "include_values": True, "include_files": False, "include_data": False},
    )
    job_id = created.json()["id"]

    resp = e2e_client.get(
        f"/api/solutions/export-jobs/{job_id}/download",
        headers=platform_admin.headers,
    )

    assert resp.status_code == 409
```

- [ ] **Step 2: Run endpoint tests to verify they fail**

Run:

```bash
./test.sh tests/e2e/platform/test_solution_export_jobs.py -q
```

Expected: route 404 or DTO import failure.

- [ ] **Step 3: Add service functions for create/list/download gate**

Add to `api/src/services/solutions/export_jobs.py`:

```python
from sqlalchemy import select

from src.models.contracts.solutions import SolutionExportJobCreate, SolutionExportJobStatus
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution as SolutionORM


def _public_job(row: SolutionExportJob) -> SolutionExportJobStatus:
    now = datetime.now(timezone.utc)
    downloadable = (
        row.status == "completed"
        and row.storage_key is not None
        and row.expires_at is not None
        and row.expires_at > now
    )
    return SolutionExportJobStatus.model_validate(row).model_copy(
        update={"downloadable": downloadable}
    )


async def create_export_job(
    db: AsyncSession,
    *,
    solution: SolutionORM,
    requested_by: UUID,
    body: SolutionExportJobCreate,
) -> SolutionExportJobStatus:
    filename = f"{solution.slug}-{solution.version or 'unversioned'}-backup.zip"
    row = SolutionExportJob(
        solution_id=solution.id,
        requested_by=requested_by,
        mode="full",
        include_values=body.include_values,
        include_files=body.include_files,
        include_data=body.include_data,
        status="queued",
        filename=filename,
        password_envelope=encrypt_export_password(body.password),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _public_job(row)


async def list_export_jobs(db: AsyncSession, solution_id: UUID) -> list[SolutionExportJobStatus]:
    rows = (
        await db.execute(
            select(SolutionExportJob)
            .where(SolutionExportJob.solution_id == solution_id)
            .order_by(SolutionExportJob.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [_public_job(row) for row in rows]
```

- [ ] **Step 4: Add routes**

In `api/src/routers/solutions.py`, import:

```python
from fastapi.responses import StreamingResponse
from src.models.contracts.solutions import SolutionExportJobCreate, SolutionExportJobsList, SolutionExportJobStatus
from src.models.contracts.notifications import NotificationCategory, NotificationCreate, NotificationStatus
from src.services.notification_service import get_notification_service
from src.services.solutions.export_jobs import create_export_job, list_export_jobs
from src.services.solutions.export_job_artifacts import SolutionExportArtifactStorage
from src.models.orm.solution_export_jobs import SolutionExportJob
```

Add routes after the existing synchronous export route:

```python
@router.post(
    "/{solution_id}/export-jobs",
    response_model=SolutionExportJobStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a scheduler-owned Solution backup export",
)
async def create_solution_export_job(
    solution_id: UUID,
    body: SolutionExportJobCreate,
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionExportJobStatus:
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    public_job = await create_export_job(
        ctx.db,
        solution=sol,
        requested_by=user.id,
        body=body,
    )
    notification = await get_notification_service().create_notification(
        user_id=str(user.id),
        request=NotificationCreate(
            category=NotificationCategory.SYSTEM,
            title="Backup queued",
            description=f"Queued backup export for {sol.name}",
            metadata={
                "solution_id": str(solution_id),
                "job_id": str(public_job.id),
                "action": "download_solution_export",
                "action_label": "Download",
            },
        ),
        initial_status=NotificationStatus.PENDING,
    )
    row = await ctx.db.get(SolutionExportJob, public_job.id)
    if row is not None:
        row.notification_id = notification.id
        await ctx.db.commit()
        await ctx.db.refresh(row)
        from src.services.solutions.export_jobs import _public_job

        return _public_job(row)
    return public_job


@router.get(
    "/{solution_id}/export-jobs",
    response_model=SolutionExportJobsList,
    summary="List Solution backup export jobs",
)
async def list_solution_export_jobs(
    solution_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionExportJobsList:
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return SolutionExportJobsList(jobs=await list_export_jobs(ctx.db, solution_id))
```

Add `GET /export-jobs/{job_id}` and download route:

```python
@router.get("/export-jobs/{job_id}", response_model=SolutionExportJobStatus)
async def get_solution_export_job(job_id: UUID, ctx: Context, user: CurrentSuperuser) -> SolutionExportJobStatus:
    from src.services.solutions.export_jobs import _public_job

    row = await ctx.db.get(SolutionExportJob, job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    return _public_job(row)


@router.get("/export-jobs/{job_id}/download")
async def download_solution_export_job(job_id: UUID, ctx: Context, user: CurrentSuperuser) -> StreamingResponse:
    row = await ctx.db.get(SolutionExportJob, job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    if row.status != "completed" or not row.storage_key or not row.expires_at or row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Export is not downloadable")

    storage = SolutionExportArtifactStorage(row.id)
    return StreamingResponse(
        storage.iter_download_chunks(row.storage_key),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{row.filename}"'},
    )
```

- [ ] **Step 5: Run endpoint tests**

Run:

```bash
./test.sh tests/e2e/platform/test_solution_export_jobs.py -q
```

Expected: the three endpoint tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/solutions.py api/src/services/solutions/export_jobs.py api/tests/e2e/platform/test_solution_export_jobs.py
git commit -m "Add solution export job endpoints"
```

---

### Task 4: Scheduler Processing And Cleanup

**Files:**
- Create: `api/src/jobs/schedulers/solution_export_jobs.py`
- Create: `api/tests/unit/jobs/schedulers/test_solution_export_jobs.py`
- Modify: `api/src/scheduler/main.py`
- Modify: `api/src/services/solutions/export_jobs.py`

- [ ] **Step 1: Write failing scheduler tests**

Create `api/tests/unit/jobs/schedulers/test_solution_export_jobs.py`:

```python
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.jobs.schedulers.solution_export_jobs import process_solution_export_jobs
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.models.orm.users import User


@pytest.mark.asyncio
async def test_process_export_job_completes_and_clears_password(db_session, monkeypatch):
    user = User(email=f"export-{uuid4().hex}@example.com", hashed_password="x", is_superuser=True)
    solution = Solution(slug=f"export-{uuid4().hex[:8]}", name="Exportable")
    db_session.add_all([user, solution])
    await db_session.commit()
    await db_session.refresh(user)
    await db_session.refresh(solution)
    job = SolutionExportJob(
        solution_id=solution.id,
        requested_by=user.id,
        filename="backup.zip",
        status="queued",
        include_values=True,
        include_files=False,
        include_data=False,
        password_envelope="encrypted",
    )
    db_session.add(job)
    await db_session.commit()

    async def fake_run_one(db, row):  # noqa: ANN001
        row.status = "completed"
        row.storage_key = f"_solution_exports/completed/{row.id}.zip"
        row.size_bytes = 10
        row.password_envelope = None
        now = datetime.now(timezone.utc)
        row.completed_at = now
        row.expires_at = now + timedelta(hours=1)

    monkeypatch.setattr(
        "src.jobs.schedulers.solution_export_jobs._process_one_export_job",
        fake_run_one,
    )

    processed, failed = await process_solution_export_jobs(batch_limit=10)

    assert processed == 1
    assert failed == 0
```

- [ ] **Step 2: Run scheduler test to verify it fails**

Run:

```bash
./test.sh tests/unit/jobs/schedulers/test_solution_export_jobs.py -q
```

Expected: import failure for missing scheduler module.

- [ ] **Step 3: Implement scheduler module**

Create `api/src/jobs/schedulers/solution_export_jobs.py`:

```python
from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update

from src.config import get_settings
from src.core.database import get_db_context
from src.models.contracts.notifications import NotificationStatus, NotificationUpdate
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.services.notification_service import get_notification_service
from src.services.solutions.export_job_artifacts import SolutionExportArtifactStorage
from src.services.solutions.export_jobs import (
    build_backup_zip_to_path,
    completed_expiry,
    decrypt_export_password,
)

logger = logging.getLogger(__name__)
DEFAULT_BATCH_LIMIT = 5


async def process_solution_export_jobs(batch_limit: int = DEFAULT_BATCH_LIMIT) -> tuple[int, int]:
    processed = 0
    failed = 0
    async with get_db_context() as db:
        await _reset_stale_running_jobs(db)
        result = await db.execute(
            select(SolutionExportJob)
            .where(SolutionExportJob.status == "queued")
            .order_by(SolutionExportJob.created_at.asc())
            .limit(batch_limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(result.scalars().all())
        if not jobs:
            return 0, 0
        now = datetime.now(timezone.utc)
        for job in jobs:
            job.status = "running"
            job.started_at = now
            job.updated_at = now
        await db.commit()

        for job in jobs:
            try:
                await _process_one_export_job(db, job)
                processed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.exception("Solution export job failed", extra={"job_id": str(job.id)})
                await _fail_job(db, job, str(exc))
    return processed, failed


async def _process_one_export_job(db, job: SolutionExportJob) -> None:  # noqa: ANN001
    solution = await db.get(Solution, job.solution_id)
    if solution is None:
        await _fail_job(db, job, "Solution not found")
        return
    if not job.password_envelope:
        await _fail_job(db, job, "Backup password was unavailable")
        return

    await _update_notification(job, NotificationStatus.RUNNING, "Building backup")
    password = decrypt_export_password(job.password_envelope)
    tmp = tempfile.NamedTemporaryFile(prefix=f"bifrost-export-job-{job.id}-", suffix=".zip", delete=False)
    out_path = Path(tmp.name)
    tmp.close()
    artifact = SolutionExportArtifactStorage(job.id)
    try:
        await build_backup_zip_to_path(
            db,
            solution=solution,
            include_values=job.include_values,
            include_files=job.include_files,
            include_data=job.include_data,
            password=password,
            out_path=out_path,
        )
        size = await artifact.upload_temp_file_from_path(out_path)
        final_key = await artifact.promote_temp_to_final()
        now = datetime.now(timezone.utc)
        job.status = "completed"
        job.storage_key = final_key
        job.temp_storage_key = None
        job.size_bytes = size
        job.password_envelope = None
        job.completed_at = now
        job.expires_at = completed_expiry(now)
        job.updated_at = now
        await db.commit()
        await _update_notification(
            job,
            NotificationStatus.COMPLETED,
            "Backup ready",
            result={"job_id": str(job.id), "filename": job.filename},
        )
    finally:
        out_path.unlink(missing_ok=True)


async def _fail_job(db, job: SolutionExportJob, error: str) -> None:  # noqa: ANN001
    job.status = "failed"
    job.error = error[:1000]
    job.password_envelope = None
    job.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await _update_notification(job, NotificationStatus.FAILED, "Backup failed", error=job.error)


async def _update_notification(
    job: SolutionExportJob,
    status: NotificationStatus,
    description: str,
    *,
    error: str | None = None,
    result: dict | None = None,
) -> None:
    if not job.notification_id:
        return
    await get_notification_service().update_notification(
        job.notification_id,
        NotificationUpdate(
            status=status,
            description=description,
            error=error,
            result=result,
        ),
    )


async def _reset_stale_running_jobs(db) -> None:  # noqa: ANN001
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.solution_export_stale_running_minutes)
    await db.execute(
        update(SolutionExportJob)
        .where(SolutionExportJob.status == "running")
        .where(SolutionExportJob.updated_at < cutoff)
        .values(status="queued", started_at=None, updated_at=datetime.now(timezone.utc))
    )


async def cleanup_expired_solution_exports() -> dict[str, int]:
    expired = 0
    deleted = 0
    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(SolutionExportJob)
                .where(SolutionExportJob.status == "completed")
                .where(SolutionExportJob.expires_at <= datetime.now(timezone.utc))
                .limit(100)
            )
        ).scalars().all()
        for row in rows:
            artifact = SolutionExportArtifactStorage(row.id)
            await artifact.delete_key(row.storage_key)
            row.storage_key = None
            row.status = "expired"
            row.updated_at = datetime.now(timezone.utc)
            expired += 1
            deleted += 1
        await db.commit()
    return {"expired": expired, "deleted": deleted}
```

- [ ] **Step 4: Register scheduler jobs**

In `api/src/scheduler/main.py`, add after deferred execution promoter registration:

```python
        try:
            from src.jobs.schedulers.solution_export_jobs import (
                cleanup_expired_solution_exports,
                process_solution_export_jobs,
            )
            scheduler.add_job(
                process_solution_export_jobs,
                IntervalTrigger(seconds=30),
                id="solution_export_jobs",
                name="Process queued Solution backup exports",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),
                **misfire_options,
            )
            scheduler.add_job(
                cleanup_expired_solution_exports,
                IntervalTrigger(hours=1),
                id="solution_export_cleanup",
                name="Cleanup expired Solution backup exports",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),
                **misfire_options,
            )
            logger.info("Solution export jobs scheduled")
        except ImportError:
            logger.warning("Solution export jobs scheduler not available")
```

- [ ] **Step 5: Run scheduler tests**

Run:

```bash
./test.sh tests/unit/jobs/schedulers/test_solution_export_jobs.py -q
cd api && pyright
cd api && ruff check .
```

Expected: scheduler tests, pyright, and ruff pass.

- [ ] **Step 6: Commit**

```bash
git add api/src/jobs/schedulers/solution_export_jobs.py api/src/scheduler/main.py api/src/services/solutions/export_jobs.py api/tests/unit/jobs/schedulers/test_solution_export_jobs.py
git commit -m "Process solution export jobs in scheduler"
```

---

### Task 5: Frontend Services And Generated Types

**Files:**
- Modify: `client/src/services/solutions.ts`
- Modify: `client/src/services/solutions.test.ts`
- Modify: `client/src/lib/v1.d.ts`

- [ ] **Step 1: Write failing service tests**

Add to `client/src/services/solutions.test.ts`:

```typescript
describe("solution export jobs", () => {
	it("queues a backup export job", async () => {
		mockPost.mockResolvedValue({ data: { id: "job-1", status: "queued" } });
		const { createSolutionExportJob } = await import("./solutions");

		const out = await createSolutionExportJob("sol-1", {
			password: "pw",
			includeValues: true,
			includeFiles: true,
			includeData: false,
		});

		expect(mockPost).toHaveBeenCalledWith(
			"/api/solutions/{solution_id}/export-jobs",
			{
				params: { path: { solution_id: "sol-1" } },
				body: {
					password: "pw",
					include_values: true,
					include_files: true,
					include_data: false,
				},
			},
		);
		expect(out.status).toBe("queued");
	});

	it("downloads a completed export job through authFetch", async () => {
		mockAuthFetch.mockResolvedValue(
			new Response(new Blob(["zip"]), {
				status: 200,
				headers: { "Content-Disposition": 'attachment; filename="backup.zip"' },
			}),
		);
		const { downloadSolutionExportJob } = await import("./solutions");

		const out = await downloadSolutionExportJob("job-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/solutions/export-jobs/job-1/download",
		);
		expect(out.filename).toBe("backup.zip");
	});
});
```

- [ ] **Step 2: Run service tests to verify they fail**

Run:

```bash
cd client && npm run test -- solutions.test.ts
```

Expected: missing exports.

- [ ] **Step 3: Regenerate types**

Ensure debug stack is up, then run:

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up
cd client && OPENAPI_URL=http://localhost:34212/openapi.json npm run generate:types
```

If `./debug.sh status` reports a different URL, replace `http://localhost:34212` in the command with the reported `Open:` URL.

- [ ] **Step 4: Add client service helpers**

In `client/src/services/solutions.ts`, add types:

```typescript
export type SolutionExportJobStatus =
	components["schemas"]["SolutionExportJobStatus"];

export interface CreateSolutionExportJobOptions {
	password: string;
	includeValues: boolean;
	includeFiles: boolean;
	includeData: boolean;
}
```

Add helpers:

```typescript
export async function createSolutionExportJob(
	solutionId: string,
	options: CreateSolutionExportJobOptions,
): Promise<SolutionExportJobStatus> {
	const { data, error } = await apiClient.POST(
		"/api/solutions/{solution_id}/export-jobs",
		{
			params: { path: { solution_id: solutionId } },
			body: {
				password: options.password,
				include_values: options.includeValues,
				include_files: options.includeFiles,
				include_data: options.includeData,
			},
		},
	);
	if (error) throw new Error(getErrorMessage(error, "Failed to queue backup export"));
	return data;
}

export async function listSolutionExportJobs(
	solutionId: string,
): Promise<SolutionExportJobStatus[]> {
	const { data, error } = await apiClient.GET(
		"/api/solutions/{solution_id}/export-jobs",
		{ params: { path: { solution_id: solutionId } } },
	);
	if (error) throw new Error(getErrorMessage(error, "Failed to list backup exports"));
	return data.jobs;
}

export async function downloadSolutionExportJob(
	jobId: string,
): Promise<{ blob: Blob; filename: string }> {
	const response = await authFetch(`/api/solutions/export-jobs/${jobId}/download`);
	if (!response.ok) {
		throw new Error(await parseUploadError(response, "Failed to download backup export"));
	}
	const disposition = response.headers.get("Content-Disposition") ?? "";
	const match = /filename="([^"]+)"/.exec(disposition);
	return { blob: await response.blob(), filename: match?.[1] ?? `solution-export-${jobId}.zip` };
}
```

- [ ] **Step 5: Run service tests**

Run:

```bash
cd client && npm run test -- solutions.test.ts
cd client && npm run tsc
```

Expected: service tests and TypeScript pass.

- [ ] **Step 6: Commit**

```bash
git add client/src/services/solutions.ts client/src/services/solutions.test.ts client/src/lib/v1.d.ts
git commit -m "Add solution export job client API"
```

---

### Task 6: Dialog, Notification Action, And Exports Tab

**Files:**
- Modify: `client/src/components/solutions/ExportSolutionDialog.tsx`
- Modify: `client/src/components/solutions/ExportSolutionDialog.test.tsx`
- Modify: `client/src/components/layout/NotificationCenter.tsx`
- Modify or create: `client/src/components/layout/NotificationCenter.test.tsx`
- Modify: `client/src/pages/SolutionDetail.tsx`
- Modify: `client/src/pages/SolutionDetail.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Update `client/src/components/solutions/ExportSolutionDialog.test.tsx`:

```typescript
it("uses queueing label for pending Backup export", async () => {
	render(
		<ExportSolutionDialog
			open
			onOpenChange={() => {}}
			onExport={() => {}}
			isPending
			pendingMode="full"
		/>,
	);
	await userEvent.click(screen.getByLabelText(/^backup/i));
	expect(screen.getByRole("button", { name: /queueing backup/i })).toBeDisabled();
});
```

Update `client/src/pages/SolutionDetail.test.tsx` mocks:

```typescript
const mockCreateSolutionExportJob = vi.fn();
const mockListSolutionExportJobs = vi.fn();
const mockDownloadSolutionExportJob = vi.fn();
```

Add to the `@/services/solutions` mock:

```typescript
createSolutionExportJob: (...a: unknown[]) => mockCreateSolutionExportJob(...a),
listSolutionExportJobs: (...a: unknown[]) => mockListSolutionExportJobs(...a),
downloadSolutionExportJob: (...a: unknown[]) => mockDownloadSolutionExportJob(...a),
```

In `beforeEach`:

```typescript
mockListSolutionExportJobs.mockResolvedValue([]);
```

Add tests:

```typescript
it("renders an Exports tab with completed backup jobs", async () => {
	mockListSolutionExportJobs.mockResolvedValue([
		{
			id: "job-1",
			solution_id: "sol-1",
			status: "completed",
			filename: "my-solution-backup.zip",
			include_values: true,
			include_files: true,
			include_data: false,
			size_bytes: 123,
			downloadable: true,
			created_at: new Date().toISOString(),
			updated_at: new Date().toISOString(),
		},
	]);
	const { user } = await renderPage();
	await screen.findByTestId("solution-detail");

	await user.click(screen.getByTestId("tab-exports"));

	expect(screen.getByText("my-solution-backup.zip")).toBeInTheDocument();
	expect(screen.getByRole("button", { name: /download backup/i })).toBeEnabled();
});

it("queues Backup export instead of direct downloading it", async () => {
	mockCreateSolutionExportJob.mockResolvedValue({ id: "job-1", status: "queued" });
	const { user } = await renderPage();
	await screen.findByTestId("solution-detail");

	await user.click(screen.getByTestId("export-solution"));
	await user.click(screen.getByLabelText(/^backup/i));
	await user.type(screen.getByLabelText(/^password/i), "pw");
	await user.click(screen.getByRole("button", { name: /^export$/i }));

	expect(mockCreateSolutionExportJob).toHaveBeenCalledWith("sol-1", {
		password: "pw",
		includeValues: true,
		includeFiles: true,
		includeData: false,
	});
	expect(mockExportSolution).not.toHaveBeenCalledWith("sol-1", "full", expect.anything(), expect.anything());
});
```

- [ ] **Step 2: Run UI tests to verify they fail**

Run:

```bash
cd client && npm run test -- ExportSolutionDialog.test.tsx SolutionDetail.test.tsx
```

Expected: missing `pendingMode`, missing Exports tab, missing service calls.

- [ ] **Step 3: Update dialog props**

In `ExportSolutionDialog.tsx`, extend props:

```typescript
pendingMode?: "shareable" | "full";
```

Use button label:

```typescript
const pendingLabel = pendingMode === "full" ? "Queueing backup..." : "Downloading...";
...
{isPending ? pendingLabel : "Export"}
```

- [ ] **Step 4: Add Solution detail export mutations/query**

In `SolutionDetail.tsx`, import:

```typescript
createSolutionExportJob,
downloadSolutionExportJob,
listSolutionExportJobs,
type SolutionExportJobStatus,
```

Add state:

```typescript
const [exportPendingMode, setExportPendingMode] = useState<"shareable" | "full" | undefined>();
```

Add query:

```typescript
const { data: exportJobs = [] } = useQuery({
	queryKey: ["solutions", solutionId, "export-jobs"],
	queryFn: () => listSolutionExportJobs(solutionId!),
	enabled: !!solutionId,
	refetchInterval: (query) => {
		const jobs = query.state.data as SolutionExportJobStatus[] | undefined;
		return jobs?.some((job) => job.status === "queued" || job.status === "running")
			? 2000
			: false;
	},
});
```

Add backup mutation:

```typescript
const backupExportMut = useMutation({
	mutationFn: ({ password, options }: { password: string; options: SolutionExportOptions }) =>
		createSolutionExportJob(solutionId!, {
			password,
			includeValues: Boolean(options.includeValues),
			includeFiles: Boolean(options.includeFiles),
			includeData: Boolean(options.includeData),
		}),
	onSuccess: () => {
		setExportDialogOpen(false);
		toast.success("Backup export queued");
		void queryClient.invalidateQueries({ queryKey: ["solutions", solutionId, "export-jobs"] });
	},
	onError: (err: unknown) => {
		toast.error("Failed to queue backup", {
			description: err instanceof Error ? err.message : "Unknown error",
		});
	},
	onSettled: () => setExportPendingMode(undefined),
});
```

Keep `exportMut` for Package only. Pass dialog `onExport`:

```typescript
onExport={(mode, password, options) => {
	setExportPendingMode(mode);
	if (mode === "full") {
		backupExportMut.mutate({ password: password ?? "", options: options ?? { includeValues: true, includeFiles: true, includeData: false } });
		return;
	}
	exportMut.mutate({ mode, password, options });
}}
isPending={exportMut.isPending || backupExportMut.isPending}
pendingMode={exportPendingMode}
```

- [ ] **Step 5: Add Exports tab renderer**

Add a tab trigger with `data-testid="tab-exports"`.

Add renderer:

```tsx
function ExportsTab({
	jobs,
	onDownload,
}: {
	jobs: SolutionExportJobStatus[];
	onDownload: (job: SolutionExportJobStatus) => void;
}) {
	if (jobs.length === 0) {
		return <p className="text-sm text-muted-foreground">No backup exports yet.</p>;
	}
	return (
		<div className="space-y-2">
			{jobs.map((job) => (
				<div key={job.id} className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
					<div className="min-w-0 flex-1">
						<p className="truncate text-sm font-medium">{job.filename}</p>
						<p className="text-xs text-muted-foreground">
							{job.status} · {job.include_values ? "Config/secrets" : "No config/secrets"} · {job.include_files ? "Files" : "No files"} · {job.include_data ? "Table data" : "No table data"}
						</p>
					</div>
					<Button
						type="button"
						size="sm"
						disabled={!job.downloadable}
						onClick={() => onDownload(job)}
						aria-label={`Download backup ${job.filename}`}
					>
						<Download className="mr-1.5 h-4 w-4" />
						Download
					</Button>
				</div>
			))}
		</div>
	);
}
```

Use existing `Download` icon import from `lucide-react`.

- [ ] **Step 6: Add notification action**

In `NotificationCenter.tsx`, import `downloadSolutionExportJob` and add a helper:

```typescript
async function triggerBlobDownload(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	a.remove();
	URL.revokeObjectURL(url);
}
```

In `handleNotificationAction`, add:

```typescript
if (action === "download_solution_export") {
	const jobId = notification.metadata?.job_id as string | undefined;
	if (!jobId) {
		toast.error("Backup download unavailable");
		return;
	}
	const { blob, filename } = await downloadSolutionExportJob(jobId);
	await triggerBlobDownload(blob, filename);
	return;
}
```

Update the action visibility logic in `ProgressNotificationItem` so completed backup notifications can render a button:

```typescript
const isCompletedDownload =
	notification.status === "completed" &&
	action === "download_solution_export";
const hasAction =
	(isAwaitingAction || isCompletedDownload) && !!action && action !== "view_file";
```

- [ ] **Step 7: Run UI tests**

Run:

```bash
cd client && npm run test -- ExportSolutionDialog.test.tsx SolutionDetail.test.tsx NotificationCenter.test.tsx
cd client && npm run tsc
```

Expected: targeted UI tests and TypeScript pass.

- [ ] **Step 8: Commit**

```bash
git add client/src/components/solutions/ExportSolutionDialog.tsx client/src/components/solutions/ExportSolutionDialog.test.tsx client/src/components/layout/NotificationCenter.tsx client/src/components/layout/NotificationCenter.test.tsx client/src/pages/SolutionDetail.tsx client/src/pages/SolutionDetail.test.tsx
git commit -m "Add solution backup export UI"
```

---

### Task 7: Playwright Happy Path

**Files:**
- Create: `client/e2e/solution-backup-export.admin.spec.ts`

- [ ] **Step 1: Write Playwright spec**

Create `client/e2e/solution-backup-export.admin.spec.ts` by reusing the zip/deploy helpers from `solution-files-link.admin.spec.ts`. Keep payloads tiny.

Core test body:

```typescript
test.describe("Solution backup export jobs (admin)", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test("queues, completes, and downloads a Backup export", async ({ page, api }) => {
		const slug = `e2e-backup-export-${Date.now()}`;
		const createR = await api.post("/api/solutions", {
			data: { slug, name: slug.toUpperCase(), organization_id: null },
		});
		expect(createR.ok()).toBe(true);
		const sol = await createR.json();
		const solId = sol.id as string;

		try {
			await deployWithSolutionsLocation(api, page, solId, slug);
			await api.put("/api/files/policies/", {
				data: {
					policies: {
						policies: [{ name: "allow_all", actions: ["read", "write", "delete", "list"] }],
					},
				},
				params: { location: "solutions" },
			});
			const writeR = await api.post(`/api/files/write?solution=${solId}`, {
				data: { location: "solutions", path: "data/hello.txt", content: "hi", mode: "cloud" },
			});
			expect(writeR.status()).toBe(204);

			await page.goto(`/solutions/${solId}`);
			await expect(page.getByTestId("solution-detail")).toBeVisible();
			await page.getByTestId("export-solution").click();
			await page.getByLabel(/^backup/i).click();
			await page.getByLabel(/^password/i).fill("pw");
			await page.getByRole("button", { name: /^export$/i }).click();
			await expect(page.getByText(/backup export queued/i)).toBeVisible();

			await page.getByTestId("tab-exports").click();
			await expect(page.getByText(/backup.zip/i)).toBeVisible({ timeout: 10000 });
			await expect
				.poll(async () => {
					await page.reload();
					await page.getByTestId("tab-exports").click();
					return await page.getByRole("button", { name: /download backup/i }).isEnabled();
				}, { timeout: 30000 })
				.toBe(true);

			const downloadPromise = page.waitForEvent("download");
			await page.getByRole("button", { name: /download backup/i }).click();
			const download = await downloadPromise;
			expect(download.suggestedFilename()).toContain("backup");
		} finally {
			await api.delete(`/api/solutions/${solId}`, { params: { confirm: slug } }).catch(() => {});
		}
	});
});
```

If the notification action is reachable via the header bell, add a second assertion in the same test after queueing:

```typescript
await page.getByRole("button", { name: /notifications/i }).click();
await expect(page.getByText(/backup/i)).toBeVisible();
```

- [ ] **Step 2: Run Playwright spec**

Run:

```bash
./test.sh client e2e e2e/solution-backup-export.admin.spec.ts
```

Expected: spec passes. If it flakes, investigate state pollution; do not add retries or blanket timeouts.

- [ ] **Step 3: Commit**

```bash
git add client/e2e/solution-backup-export.admin.spec.ts
git commit -m "Cover async solution backup export in Playwright"
```

---

### Task 8: Full Verification

**Files:** existing files changed by fixes from earlier tasks.

- [ ] **Step 1: Run backend checks**

```bash
cd api && pyright
cd api && ruff check .
```

Expected: both pass.

- [ ] **Step 2: Regenerate types after final backend state**

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up
cd client && OPENAPI_URL=http://localhost:34212/openapi.json npm run generate:types
```

Use the reported `Open:` URL from `./debug.sh status` in `OPENAPI_URL`.

- [ ] **Step 3: Run frontend checks**

```bash
cd client && npm run tsc
cd client && npm run lint
```

Expected: `tsc` passes; lint passes. If lint reports only the pre-existing `FormRenderer.tsx` incompatible-library warning, record it in the verification notes and continue.

- [ ] **Step 4: Run backend tests**

```bash
./test.sh tests/unit/test_solution_export_jobs_contracts.py -q
./test.sh tests/unit/test_solution_export_job_artifacts.py -q
./test.sh tests/unit/jobs/schedulers/test_solution_export_jobs.py -q
./test.sh tests/e2e/platform/test_solution_export_jobs.py -q
./test.sh all
```

Expected: targeted tests and full backend suite pass.

- [ ] **Step 5: Run frontend tests**

```bash
cd client && npm run test -- solutions.test.ts ExportSolutionDialog.test.tsx SolutionDetail.test.tsx NotificationCenter.test.tsx
./test.sh client unit
./test.sh client e2e e2e/solution-backup-export.admin.spec.ts
./test.sh client e2e
```

Expected: targeted vitest, full client unit, targeted Playwright, and full Playwright pass.

- [ ] **Step 6: Final diff hygiene**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional files changed before final commit.

- [ ] **Step 7: Final commit**

```bash
git add api client docs
git commit -m "Add async solution backup exports"
```

If every earlier task committed cleanly and no final fixes remain, skip this final commit.
