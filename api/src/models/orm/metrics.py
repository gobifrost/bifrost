"""
Metrics ORM models.

Represents execution metrics and platform metrics snapshots.
"""

from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base


# Identity entity — aggregated execution telemetry, not name-cascade resolved.
# See api/src/repositories/README.md.
class ExecutionMetricsDaily(Base):
    """
    Daily aggregated execution metrics.

    Populated by the consumer on each execution completion.
    Used for trend charts and organization usage reports.
    """

    __tablename__ = "execution_metrics_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )

    # Execution counts
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    timeout_count: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_count: Mapped[int] = mapped_column(Integer, default=0)

    # Duration metrics (milliseconds)
    total_duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    avg_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    max_duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Resource metrics
    total_memory_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    peak_memory_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    total_cpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    peak_cpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    # Economics aggregates
    total_time_saved: Mapped[int] = mapped_column(BigInteger, default=0)
    total_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("date", "organization_id", name="uq_metrics_daily_date_org"),
        Index("ix_metrics_daily_date", "date"),
        Index("ix_metrics_daily_org", "organization_id"),
        # Partial unique index for global metrics (org_id IS NULL)
        # Enforces single global row per date
        Index(
            "uq_metrics_daily_date_global",
            "date",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
    )


class PlatformMetricsSnapshot(Base):
    """
    Current platform metrics snapshot.

    Refreshed periodically by the scheduler (every 1-5 minutes).
    Used for instant dashboard loads without expensive queries.
    Single row table - always id=1.
    """

    __tablename__ = "platform_metrics_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Entity counts
    workflow_count: Mapped[int] = mapped_column(Integer, default=0)
    form_count: Mapped[int] = mapped_column(Integer, default=0)
    data_provider_count: Mapped[int] = mapped_column(Integer, default=0)
    organization_count: Mapped[int] = mapped_column(Integer, default=0)
    user_count: Mapped[int] = mapped_column(Integer, default=0)

    # Execution stats (all time)
    total_executions: Mapped[int] = mapped_column(Integer, default=0)
    total_success: Mapped[int] = mapped_column(Integer, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, default=0)

    # Execution stats (last 24 hours)
    executions_24h: Mapped[int] = mapped_column(Integer, default=0)
    success_24h: Mapped[int] = mapped_column(Integer, default=0)
    failed_24h: Mapped[int] = mapped_column(Integer, default=0)

    # Current state
    running_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)

    # Performance (last 24 hours)
    avg_duration_ms_24h: Mapped[int] = mapped_column(Integer, default=0)
    total_memory_bytes_24h: Mapped[int] = mapped_column(BigInteger, default=0)
    total_cpu_seconds_24h: Mapped[float] = mapped_column(Float, default=0.0)

    # Success rate
    success_rate_all_time: Mapped[float] = mapped_column(Float, default=0.0)
    success_rate_24h: Mapped[float] = mapped_column(Float, default=0.0)

    # Economics (last 24 hours)
    time_saved_24h: Mapped[int] = mapped_column(BigInteger, default=0)
    value_24h: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    # Economics (all time)
    time_saved_all_time: Mapped[int] = mapped_column(BigInteger, default=0)
    value_all_time: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    # AI usage (last 24 hours) - nullable for existing records
    ai_cost_24h: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    ai_calls_24h: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamp
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )


# Identity entity — aggregated ROI telemetry, not name-cascade resolved.
# See api/src/repositories/README.md.
class WorkflowROIDaily(Base):
    """
    Daily aggregated ROI metrics per workflow per organization.

    Populated by the consumer on each execution completion.
    Used for per-workflow value reporting.
    """

    __tablename__ = "workflow_roi_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )

    # Execution counts
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)

    # Economics aggregates
    total_time_saved: Mapped[int] = mapped_column(BigInteger, default=0)
    total_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("date", "workflow_id", "organization_id", name="uq_workflow_roi_daily"),
        Index("ix_workflow_roi_daily_date", "date"),
        Index("ix_workflow_roi_daily_workflow", "workflow_id"),
        Index("ix_workflow_roi_daily_org", "organization_id"),
    )


# Identity entity — aggregated storage telemetry, not name-cascade resolved.
# See api/src/repositories/README.md.
class KnowledgeStorageDaily(Base):
    """
    Daily snapshot of knowledge storage usage per organization and namespace.

    Populated by a scheduled job that runs daily.
    Used for storage usage reporting and trend analysis.
    """

    __tablename__ = "knowledge_storage_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    # Relationship to organization (optional for global entries)
    organization: Mapped["Organization"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Organization", back_populates="knowledge_storage_snapshots"
    )

    __table_args__ = (
        UniqueConstraint(
            "snapshot_date", "organization_id", "namespace", name="uq_storage_daily_date_org_ns"
        ),
        Index("ix_storage_daily_date", "snapshot_date"),
        Index("ix_storage_daily_org", "organization_id"),
    )
