"""
WorkflowRunDaily ORM model.

Per-day rollup of finished workflow runs deleted by run retention, so run
counts and resource peaks keep their history after the runs are gone.
"""

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Date, DateTime, Enum as SQLAlchemyEnum, Float, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.enums import ExecutionStatus
from src.models.orm.base import Base


# Identity entity — aggregated run telemetry, not name-cascade resolved.
# See api/src/repositories/README.md. organization_id and workflow_id carry no
# foreign keys: history must outlive the organization and the workflow.
class WorkflowRunDaily(Base):
    """One UTC day of deleted finished runs for one workflow, organization and status."""

    __tablename__ = "workflow_run_daily"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    day: Mapped[date] = mapped_column(Date)
    organization_id: Mapped[UUID | None] = mapped_column(default=None)
    workflow_id: Mapped[UUID | None] = mapped_column(default=None)
    workflow_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[ExecutionStatus] = mapped_column(
        SQLAlchemyEnum(
            ExecutionStatus,
            name="execution_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
    )
    run_count: Mapped[int] = mapped_column(Integer)
    total_duration_ms: Mapped[int] = mapped_column(BigInteger)
    total_cpu_seconds: Mapped[float] = mapped_column(Float)
    max_peak_cpu_cores: Mapped[float | None] = mapped_column(Float, default=None)
    max_peak_process_rss_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    max_peak_memory_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
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
        UniqueConstraint(
            "day",
            "organization_id",
            "workflow_id",
            "workflow_name",
            "status",
            name="uq_workflow_run_daily_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_workflow_run_daily_day", "day"),
    )
