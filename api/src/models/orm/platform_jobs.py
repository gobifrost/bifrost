"""Durable records for scheduler-owned platform jobs."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class PlatformJob(Base):
    """One durable, observable unit of non-execution platform work."""

    __tablename__ = "platform_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    encrypted_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_lock_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requested_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_by_email: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notification_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    memory_profile_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="queued", index=True
    )
    phase: Mapped[str | None] = mapped_column(String(200), nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_percent: Mapped[float | None] = mapped_column(nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20 * 60
    )
    memory_required_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=256 * 1024 * 1024
    )
    retry_on_runner_loss: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    memory_start_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memory_peak_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memory_limit_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_platform_jobs_active_dedupe",
            "job_type",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "dedupe_key IS NOT NULL AND "
                "status IN ('queued', 'running', 'waiting', 'cancel_requested')"
            ),
        ),
        Index(
            "ix_platform_jobs_claimable",
            "status",
            "available_at",
            "created_at",
        ),
    )
