"""Orchestration row for a background mass file operation.

Created when an admin enqueues a file mass-op via
``POST /api/solutions/file-jobs``.  The endpoint returns the ``file_job_id``
immediately; the background task flips the row ``queued`` → ``running`` →
``succeeded`` / ``failed``.

Three kinds:

* ``restore``     — copy files from the solution workspace back to the install
                    (used on re-install).
* ``orphan``      — re-stamp solution-owned files as org-owned orphans (used on
                    uninstall).  This job runs **while** the Solution row is being
                    deleted, so the FK from ``install_id`` to ``solutions.id``
                    intentionally has **NO cascade** — a cascade would delete this
                    row before the background task could complete.
* ``bulk_delete`` — delete every file belonging to an install from both S3 and the
                    metadata index.

C3 note: ``install_id`` is nullable with **no FK to ``solutions``** precisely
because an ``orphan`` job may outlive the Solution row it references.  The job
carries ``origin_solution_id`` (plain UUID, no FK) and a ``captured_keys`` JSON
blob holding the file IDs / old S3 keys snapshotted at enqueue time, so the
worker can do its work without re-querying ``solution_id == install_id`` (which
would return nothing after the in-txn re-stamp on uninstall).
"""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionFileJob(Base):
    __tablename__ = "solution_file_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # Nullable, no FK to solutions — see module docstring (C3 note).
    install_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    # Plain UUID column; no FK so the job survives Solution deletion.
    origin_solution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False  # 'restore' | 'orphan' | 'bulk_delete'
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Snapshotted at enqueue: for 'orphan' jobs, the list of FileMetadata IDs
    # (as hex strings) that must be re-stamped.  The worker reads this blob
    # rather than re-querying solution_id so it is immune to post-restamp state.
    captured_keys: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    # On success: counts produced by the operation.
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
