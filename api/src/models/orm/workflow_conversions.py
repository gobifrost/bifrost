"""Compact, durable projections of workflow permission-conversion analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkflowConversionAnalysisCache(Base):
    """A reusable compact static summary; never stores source or an AST."""

    __tablename__ = "workflow_conversion_analysis_cache"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Hash the namespace/path identity; source paths must not become an
    # unbounded cache key or leak a workspace layout through this projection.
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
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
        UniqueConstraint(
            "source_key",
            "content_hash",
            "analyzer_version",
            name="uq_workflow_conversion_summary",
        ),
        Index(
            "ix_workflow_conversion_analysis_cache_hash",
            "content_hash",
            "analyzer_version",
        ),
    )


class WorkflowConversionReport(Base):
    """One workflow's durable review projection for one analysis platform job."""

    __tablename__ = "workflow_conversion_reports"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    review: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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
        UniqueConstraint(
            "job_id", "workflow_id", name="uq_workflow_conversion_report_job_workflow"
        ),
        Index("ix_workflow_conversion_reports_job_status", "job_id", "status"),
    )
