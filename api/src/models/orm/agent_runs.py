"""AgentRun and AgentRunStep ORM models for autonomous agent execution tracking."""
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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

if TYPE_CHECKING:
    from src.models.orm.ai_usage import AIUsage


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_id: Mapped[UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=True)
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
    # Root of the delegation tree. Top-level runs store their own ID; every
    # descendant copies it. Backfilled to id for historical rows.
    root_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        default=None,
    )
    # Immutable execution snapshot taken at enqueue: prompt, model/profile
    # settings, tool and delegated-agent references, system-tool grants,
    # resolved limits. A resumed run never re-reads live Agent configuration.
    execution_snapshot: Mapped[dict | None] = mapped_column(JSONB, default=None)
    # Caller-supplied durable locators (e.g. ticket ID) for coordinators.
    caller_context: Mapped[dict | None] = mapped_column(JSONB, default=None)
    # Latest committed checkpoint number. Backfilled to 0.
    checkpoint_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Crash-detection lease. Exactly one worker owns a live run at a time;
    # writes require the current lease_token (fencing).
    lease_owner: Mapped[str | None] = mapped_column(String(255), default=None)
    lease_token: Mapped[str | None] = mapped_column(String(64), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Worker-claim attempt count, not model-turn count. Backfilled to 0.
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Populated only while sleeping; the scheduler wakes the run when due.
    wake_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Bounded JSON metadata used by coordinators and event filters.
    correlation: Mapped[dict | None] = mapped_column(JSONB, default=None)
    # At-least-once terminal event delivery. Terminalization marks the run
    # pending in the same transaction; a scheduler outbox pass emits it.
    completion_event_pending_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    completion_event_emitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    completion_event_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    completion_event_last_error: Mapped[str | None] = mapped_column(
        Text, default=None
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
        Index("ix_agent_runs_root_run_id", "root_run_id"),
        Index("ix_agent_runs_wake_at", "wake_at"),
        Index("ix_agent_runs_lease_expires_at", "lease_expires_at"),
        Index(
            "ix_agent_runs_completion_pending",
            "completion_event_pending_at",
            postgresql_where=text(
                "completion_event_pending_at IS NOT NULL "
                "AND completion_event_emitted_at IS NULL"
            ),
        ),
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


class AgentRunCheckpoint(Base):
    """Append-only durable checkpoint: runtime state for the next safe step.

    Holds the versioned codec payload (normalized conversation/model items,
    active agent, budgets, pending tool calls/join/timer references) produced
    by ``checkpoint_codec.encode_messages`` plus engine bookkeeping.
    """

    __tablename__ = "agent_run_checkpoints"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    format_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lease_token: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_checkpoints_run_sequence"),
        Index("ix_agent_run_checkpoints_run_id", "run_id"),
    )


class AgentRunJournalEntry(Base):
    """Append-only execution journal: model/tool/delegation/timer/recovery/
    validation/completion events in stable sequence. Powers restart recovery
    and the debugger. ``AgentRunStep`` remains the compatibility projection.
    """

    __tablename__ = "agent_run_journal_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    provider_invocation_id: Mapped[str | None] = mapped_column(
        String(255), default=None
    )
    checkpoint_sequence: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id", "sequence", name="uq_agent_run_journal_run_sequence"
        ),
        Index("ix_agent_run_journal_run_id", "run_id"),
        Index("ix_agent_run_journal_run_kind", "run_id", "kind"),
    )


class AgentToolInvocation(Base):
    """Durable tool invocation keyed by engine operation ID.

    ``planned`` -> ``running`` -> ``completed``/``failed``, or ``uncertain``
    when a lease expired mid-flight. Uncertain invocations reconcile via a
    registered hook or move the run to ``recovery_required``; they are never
    blindly replayed.
    """

    __tablename__ = "agent_tool_invocations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    provider_tool_call_id: Mapped[str | None] = mapped_column(
        String(255), default=None
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(64), default=None)
    tool_schema: Mapped[dict | None] = mapped_column(JSONB, default=None)
    arguments: Mapped[dict | None] = mapped_column(JSONB, default=None)
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="planned", server_default=text("'planned'")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reconciliation: Mapped[dict | None] = mapped_column(JSONB, default=None)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "provider_tool_call_id",
            name="uq_agent_tool_invocations_run_tool_call",
        ),
        Index("ix_agent_tool_invocations_run_id", "run_id"),
        Index("ix_agent_tool_invocations_run_state", "run_id", "state"),
        CheckConstraint(
            "state IN ('planned', 'running', 'completed', 'failed', 'uncertain')",
            name="ck_agent_tool_invocations_state",
        ),
    )


class AgentRunJoin(Base):
    """Durable fan-out join. V1 supports ``mode='all'`` only; the column is
    extensible to ``any``/``quorum`` without a migration once their
    cancellation and partial-result semantics are tested.
    """

    __tablename__ = "agent_run_joins"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    parent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    provider_tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="all", server_default=text("'all'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    members: Mapped[list["AgentRunJoinMember"]] = relationship(
        back_populates="join", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "parent_run_id",
            "provider_tool_call_id",
            name="uq_agent_run_joins_parent_tool_call",
        ),
        Index("ix_agent_run_joins_parent_run_id", "parent_run_id"),
        CheckConstraint("mode = 'all'", name="ck_agent_run_joins_mode_v1"),
        CheckConstraint(
            "status IN ('pending', 'complete', 'failed', 'cancelled')",
            name="ck_agent_run_joins_status",
        ),
    )


class AgentRunJoinMember(Base):
    """One child participant in a durable fan-out join."""

    __tablename__ = "agent_run_join_members"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    join_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_run_joins.id", ondelete="CASCADE"), nullable=False
    )
    child_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    output: Mapped[dict | None] = mapped_column(JSONB, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    join: Mapped["AgentRunJoin"] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint(
            "join_id", "child_run_id", name="uq_agent_run_join_members_join_child"
        ),
        Index("ix_agent_run_join_members_join_id", "join_id"),
        Index("ix_agent_run_join_members_child_run_id", "child_run_id"),
    )
