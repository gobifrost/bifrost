"""Orchestration row for an async, observable solution deploy.

Created when an admin calls ``POST /api/solutions/{id}/deploy``. The endpoint
enqueues the (sometimes >100s) deploy as an in-process background task and
returns the ``deploy_job_id`` immediately; the task flips the row ``queued`` →
``running`` → ``succeeded`` / ``failed`` (capturing the error). API startup
marks stale queued/running jobs failed so pollers never wait forever after a
restart. The CLI polls ``GET /api/solutions/deploy-jobs/{id}`` until a terminal
status.

This decouples deploy from the HTTP request so the CLI no longer hits an httpx
``ReadTimeout`` while the server completes successfully (Task 7 bug).
"""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionDeployJob(Base):
    __tablename__ = "solution_deploy_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # Nullable: a zip install (Task H1) resolves-or-creates its target install
    # INSIDE the job, so the id isn't known at enqueue — the succeeded ``result``
    # carries the solution_id. Deploy / install-from-repo jobs always set it.
    install_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # On success: the per-entity upsert/delete counts the (now async) deploy
    # produced, so the operator and the poller can still see what shipped.
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
