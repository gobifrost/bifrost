"""Orchestration row for bulk agent-run summary backfills.

Created when an admin triggers ``POST /api/agent-runs/backfill-summaries``.
The summarize_worker increments ``succeeded`` / ``failed`` as it consumes
queued messages tagged with the job_id; when the total is reached the job
is marked ``complete``. UI surfaces progress via the
``summary-backfill:{id}`` websocket channel.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SummaryBackfillJob(Base):
    __tablename__ = "summary_backfill_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True, default=None
    )
    requested_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running"
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )
    actual_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )
    processed_run_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
