"""Recorded agent evaluation projections over durable production runs."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base


class AgentRecordedEvaluation(Base):
    """Feature projection for one exact recorded-evaluation PlatformJob."""

    __tablename__ = "agent_recorded_evaluations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_by_email: Mapped[str] = mapped_column(String(255), nullable=False)
    platform_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("platform_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    frozen_input: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    aggregate: Mapped[dict | None] = mapped_column(JSONB, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    results: Mapped[list["AgentRecordedEvaluationResult"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_agent_recorded_evaluations_org_id", "org_id"),
        Index("ix_agent_recorded_evaluations_agent_id", "agent_id"),
        Index(
            "ix_agent_recorded_evaluations_requester",
            "requested_by_user_id",
        ),
    )


class AgentRecordedEvaluationResult(Base):
    """One frozen case/run pair outcome for a recorded evaluation."""

    __tablename__ = "agent_recorded_evaluation_results"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    evaluation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_recorded_evaluations.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    applicability: Mapped[str] = mapped_column(String(20), nullable=False)
    applicability_source: Mapped[str] = mapped_column(String(30), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    complete: Mapped[bool] = mapped_column(nullable=False)
    assertion_outcomes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evidence_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    limitations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    evaluation: Mapped[AgentRecordedEvaluation] = relationship(
        back_populates="results"
    )

    __table_args__ = (
        UniqueConstraint(
            "evaluation_id",
            "case_id",
            "run_id",
            name="uq_recorded_eval_result_pair",
        ),
        Index("ix_recorded_eval_results_evaluation_id", "evaluation_id"),
        Index("ix_recorded_eval_results_run_id", "run_id"),
    )
