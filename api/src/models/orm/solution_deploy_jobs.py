"""Durable orchestration row for an async, observable Solution deploy job."""
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
    # ``deploy`` (existing install), ``install`` (uploaded zip), or
    # ``install_from_repo`` (validated checkout archived at enqueue time).
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="deploy")
    # Entire options document is encrypted because install options may contain
    # config values and an export password. Queue messages carry only ``id``.
    encrypted_options: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )
    # Input is staged in S3 before the row becomes visible. The digest is
    # rechecked after the worker downloads it.
    input_key: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )
    input_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
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
