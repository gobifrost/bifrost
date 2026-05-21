"""AgentRun and AgentRunStep ORM models for autonomous agent execution tracking."""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base



class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger_source: Mapped[str | None] = mapped_column(String(500), default=None)
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), default=None)
    event_delivery_id: Mapped[UUID | None] = mapped_column(ForeignKey("event_deliveries.id", ondelete="SET NULL"), default=None)
    input: Mapped[dict | None] = mapped_column(JSONB, default=None)
    output: Mapped[dict | None] = mapped_column(JSONB, default=None)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, default=None)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    org_id: Mapped[UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), default=None)
    caller_user_id: Mapped[str | None] = mapped_column(String(255), default=None)
    caller_email: Mapped[str | None] = mapped_column(String(255), default=None)
    caller_name: Mapped[str | None] = mapped_column(String(255), default=None)
    iterations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_max_iterations: Mapped[int | None] = mapped_column(Integer, default=None)
    budget_max_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    llm_model: Mapped[str | None] = mapped_column(String(100), default=None)
    # Summary / metadata / confidence fields (populated by the run summarizer).
    # NOTE: the DB column is named ``metadata`` but the Python attribute is
    # ``run_metadata`` because ``DeclarativeBase.metadata`` is reserved by
    # SQLAlchemy. Use ``run.run_metadata`` in Python; ``agent_runs.metadata``
    # in raw SQL / Alembic.
    asked: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    did: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Short user-facing answer/outcome — distinct from `did` (the work). The
    # summarizer prompt v3 produces this as a separate field; v1/v2 left it
    # unset, so the column is nullable and existing rows backfill on rerun.
    answered: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    run_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    confidence_reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    summary_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    summary_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Version of SUMMARIZE_SYSTEM_PROMPT that produced the current asked/did/
    # metadata. NULL for unsummarized/failed runs. Bumped manually in
    # src/services/execution/run_summarizer.py when the prompt changes; the
    # backfill endpoint accepts ``prompt_version_below`` so admins can
    # re-summarize runs tagged with an older version.
    summary_prompt_version: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )
    # Reviewer verdict (thumbs up/down) — see migration 20260421b_verdicts.
    # ``verdict`` is constrained to ('up', 'down', NULL) at the DB layer.
    verdict: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)
    verdict_note: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    verdict_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    verdict_set_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    parent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), default=None
    )

    # Relationships
    agent = relationship("Agent", lazy="joined")
    steps: Mapped[list["AgentRunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentRunStep.step_number"
    )
    ai_usages: Mapped[list["AIUsage"]] = relationship(back_populates="agent_run")
    conversation = relationship("Conversation", lazy="select")
    child_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="parent_run",
        foreign_keys="AgentRun.parent_run_id",
    )
    parent_run: Mapped["AgentRun | None"] = relationship(
        back_populates="child_runs",
        remote_side="AgentRun.id",
        foreign_keys="AgentRun.parent_run_id",
    )

    __table_args__ = (
        Index("ix_agent_runs_agent_id", "agent_id"),
        Index("ix_agent_runs_org_id", "org_id"),
        Index("ix_agent_runs_status", "status"),
        Index("ix_agent_runs_trigger_type", "trigger_type"),
        Index("ix_agent_runs_created_at", "created_at"),
        Index("ix_agent_runs_parent_run_id", "parent_run_id"),
        Index("ix_agent_runs_agent_verdict_status", "agent_id", "verdict", "status"),
    )


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[dict | None] = mapped_column(JSONB, default=None)
    tokens_used: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    run: Mapped["AgentRun"] = relationship(back_populates="steps")

    __table_args__ = (
        Index("ix_agent_run_steps_run_id", "run_id"),
    )
