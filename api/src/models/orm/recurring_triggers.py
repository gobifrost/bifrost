"""Shared recurring PlatformJob trigger storage.

Generic durable schedule rows plus a strict per-occurrence fire fence. Quality
scheduling (reviews first, evaluation suites later) registers operation
definitions against this storage; the rows never carry job lifecycle, which
stays on PlatformJob. Fire rows are retained as the idempotency fence.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base

TRIGGER_OPERATION_TYPES = ("agent_review", "agent_evaluation_suite")
TRIGGER_OVERLAP_POLICIES = ("skip", "queue", "replace")
FIRE_STATUSES = ("claimed", "admitted", "skipped", "failed")


class RecurringPlatformJobTrigger(Base):
    """Durable recurring trigger configuration and immutable admission policy."""

    __tablename__ = "recurring_platform_job_triggers"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False, default="UTC")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    overlap_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="skip", server_default=text("'skip'")
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    requested_by_email: Mapped[str] = mapped_column(String(320), nullable=False)
    requested_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_recurring_triggers_org_id", "org_id"),
        Index("ix_recurring_triggers_operation", "operation_type", "operation_id"),
        Index("ix_recurring_triggers_enabled", "enabled"),
        CheckConstraint(
            "operation_type IN ('agent_review', 'agent_evaluation_suite')",
            name="ck_recurring_triggers_operation_type",
        ),
        CheckConstraint(
            "overlap_policy IN ('skip')",
            name="ck_recurring_triggers_overlap_policy",
        ),
    )


class RecurringTriggerFire(Base):
    """Strict per-occurrence admission fence and audit receipt.

    Uniqueness on (trigger_id, scheduled_for) guarantees a due occurrence is
    admitted at most once, even across scheduler failover. Status/reason and
    IDs are audit navigation only; PlatformJob is authoritative status.
    """

    __tablename__ = "recurring_trigger_fires"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    trigger_id: Mapped[UUID] = mapped_column(
        ForeignKey("recurring_platform_job_triggers.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="claimed")
    reason: Mapped[str | None] = mapped_column(String(120), default=None)
    platform_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("platform_jobs.id", ondelete="SET NULL"), default=None
    )
    domain_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), default=None
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "trigger_id",
            "scheduled_for",
            name="uq_recurring_fires_trigger_scheduled_for",
        ),
        Index("ix_recurring_fires_trigger_id", "trigger_id"),
        Index("ix_recurring_fires_status", "status"),
        CheckConstraint(
            "status IN ('claimed', 'admitted', 'skipped', 'failed')",
            name="ck_recurring_fires_status",
        ),
        CheckConstraint(
            "attempt_count >= 1",
            name="ck_recurring_fires_attempt_count",
        ),
        CheckConstraint(
            "(status = 'admitted' AND platform_job_id IS NOT NULL)"
            " OR (status <> 'admitted')",
            name="ck_recurring_fires_admitted_has_job",
        ),
    )
