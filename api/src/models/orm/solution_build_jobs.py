"""Builder build jobs: one bounded runner execution per app build.

The job row is the durable record of a build (spec 2026-07-25, "Build
artifacts"): what source produced it, which toolchain ran, what came out, and
how it ended. Staged output bytes live in object storage under
``_build_artifacts/{build_job_id}/``; this row carries the manifest and hashes
only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionBuildJob(Base):
    """One queued/running/finished app build for a Solution revision."""

    __tablename__ = "solution_build_jobs"
    __table_args__ = (
        Index("ix_solution_build_jobs_solution_created", "solution_id", "created_at"),
        # Reuse lookup for the idempotency key: source SHA + app + toolchain may
        # reuse a prior successful build (spec, "Build artifacts").
        Index(
            "ix_solution_build_jobs_reuse_key",
            "source_sha256",
            "app_id",
            "toolchain_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), index=True
    )
    # Deliberately not an FK: a build is requested before the deploy transaction
    # inserts its Application row. Holding an uncommitted app FK while waiting
    # for a separately committed worker job deadlocks the build plane.
    app_id: Mapped[UUID | None] = mapped_column(default=None)
    source_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="SET NULL"), default=None
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    source_sha256: Mapped[str] = mapped_column(String(64))
    toolchain_version: Mapped[str] = mapped_column(String(64))
    dependency_digest: Mapped[str | None] = mapped_column(String(64), default=None)

    # queued | running | succeeded | failed | cancelled | timeout
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued"
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # Truncated runner log (spec caps captured logs; bytes beyond the cap drop).
    log_excerpt: Mapped[str | None] = mapped_column(Text, default=None)
    # [{"path": ..., "sha256": ..., "size": ...}] for every staged output file.
    output_manifest: Mapped[list | None] = mapped_column(JSONB, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
