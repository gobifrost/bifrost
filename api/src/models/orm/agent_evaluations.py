"""Agent Evaluation Studio ORM models.

Durable feature records for reproducible synthetic Agent evaluation.
Suite execution itself is owned by ``PlatformJob`` (``agent.evaluation_suite``);
``AgentEvaluationExecution`` is a projection that references the authoritative
``platform_job_id`` and carries no lease/retry state of its own.

Tables:
- ``agent_evaluation_suites``: tenant-scoped, versioned (draft/published).
- ``agent_evaluation_cases``: ordered, immutable per ``(suite_id, name,
  version)``; fixtures are frozen at acceptance time.
- ``agent_candidate_snapshots``: immutable evaluation-only overlays derived
  from a published Agent. Never mutated, never attached to the live Agent.
- ``agent_evaluation_executions``: projection over one PlatformJob run.
- ``agent_evaluation_results``: per-case, per-repetition outcome with
  assertion results, baseline/candidate comparison, usage, and debugger links.
- ``agent_simulation_sessions`` + ``agent_simulation_tool_records``:
  per-case locked fixture state and append-only synthetic tool history.
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

EVALUATION_SUITE_STATUSES = ("draft", "published", "archived")
EVALUATION_CASE_PROVENANCE = ("manual", "generated", "historical_inspiration")
EVALUATION_EXECUTION_STATUSES = (
    "queued",
    "running",
    "waiting",
    "succeeded",
    "failed",
    "cancelled",
)
EVALUATION_RESULT_STATUSES = ("pending", "running", "passed", "failed", "error")


class AgentEvaluationSuite(Base):
    __tablename__ = "agent_evaluation_suites"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), default=None
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=text("'draft'")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)
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

    cases: Mapped[list["AgentEvaluationCase"]] = relationship(
        back_populates="suite", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "org_id", "name", "version", name="uq_eval_suites_org_name_version"
        ),
        Index("ix_eval_suites_org_id", "org_id"),
        Index("ix_eval_suites_agent_id", "agent_id"),
        Index("ix_eval_suites_status", "status"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_eval_suites_status",
        ),
    )


class AgentEvaluationCase(Base):
    __tablename__ = "agent_evaluation_cases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    suite_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_evaluation_suites.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # Immutable versioned invocation input.
    input: Mapped[dict | None] = mapped_column(JSONB, default=None)
    # Frozen fixture: entity collections, deterministic ID/time seeds,
    # allowed tools, tool rules. Redacted at generation/acceptance time.
    fixture: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    simulator_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    assertions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    expected_tools: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    forbidden_tools: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    output_schema: Mapped[dict | None] = mapped_column(JSONB, default=None)
    repetitions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    scoring_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    provenance: Mapped[str] = mapped_column(
        String(30), nullable=False, default="manual", server_default=text("'manual'")
    )
    provenance_run_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Draft designer output stays editable until explicitly accepted; an
    # accepted case version is frozen and reruns never regenerate it.
    accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
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

    suite: Mapped["AgentEvaluationSuite"] = relationship(back_populates="cases")

    __table_args__ = (
        UniqueConstraint(
            "suite_id", "name", "version", name="uq_eval_cases_suite_name_version"
        ),
        Index("ix_eval_cases_suite_id", "suite_id"),
        Index("ix_eval_cases_suite_position", "suite_id", "position"),
        CheckConstraint(
            "provenance IN ('manual', 'generated', 'historical_inspiration')",
            name="ck_eval_cases_provenance",
        ),
    )


class AgentCandidateSnapshot(Base):
    __tablename__ = "agent_candidate_snapshots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    base_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), default=None
    )
    base_agent_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    # Caller-supplied overlays (prompt/model/profile/tools/delegates/limits).
    overlays: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Complete frozen snapshot resolved at creation; later Agent/tool edits
    # never change it. Evaluation-only: production enqueue paths reject it.
    snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    evaluation_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("ix_eval_candidates_org_id", "org_id"),
        Index("ix_eval_candidates_base_agent_id", "base_agent_id"),
        Index("ix_eval_candidates_snapshot_hash", "snapshot_hash"),
    )


class AgentEvaluationExecution(Base):
    __tablename__ = "agent_evaluation_executions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    suite_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_evaluation_suites.id", ondelete="CASCADE"), nullable=False
    )
    suite_version: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_candidate_snapshots.id", ondelete="SET NULL"), default=None
    )
    baseline_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), default=None
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default=text("'queued'")
    )
    total_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    completed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    passed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failed_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Authoritative owner: one PlatformJob of type ``agent.evaluation_suite``.
    platform_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("platform_jobs.id", ondelete="SET NULL"),
        default=None,
        unique=True,
    )
    # Stable active-operation deduplication key
    # (suite/version/candidate); enforced while the execution is active.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    results: Mapped[list["AgentEvaluationResult"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_eval_executions_suite_id", "suite_id"),
        Index("ix_eval_executions_status", "status"),
        Index("ix_eval_executions_platform_job_id", "platform_job_id"),
        Index(
            "uq_eval_executions_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "dedupe_key IS NOT NULL AND status IN ('queued', 'running', 'waiting')"
            ),
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
            name="ck_eval_executions_status",
        ),
    )


class AgentEvaluationResult(Base):
    __tablename__ = "agent_evaluation_results"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_evaluation_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_evaluation_cases.id", ondelete="CASCADE"), nullable=False
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    repetition_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    baseline_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        default=None,
    )
    candidate_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        default=None,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    # Per-assertion outcomes: stable code, pass/fail, redacted expected/actual,
    # evidence journal sequence IDs.
    assertion_results: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    comparison: Mapped[dict | None] = mapped_column(JSONB, default=None)
    tokens_used: Mapped[int | None] = mapped_column(Integer, default=None)
    turns_used: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    cost_usd: Mapped[str | None] = mapped_column(String(32), default=None)
    simulator_state_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    execution: Mapped["AgentEvaluationExecution"] = relationship(
        back_populates="results"
    )

    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "case_id",
            "repetition_index",
            name="uq_eval_results_execution_case_repetition",
        ),
        Index("ix_eval_results_execution_id", "execution_id"),
        Index("ix_eval_results_case_id", "case_id"),
        Index("ix_eval_results_status", "status"),
        CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'failed', 'error')",
            name="ck_eval_results_status",
        ),
    )


class AgentSimulationSession(Base):
    __tablename__ = "agent_simulation_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_evaluation_cases.id", ondelete="CASCADE"), nullable=False
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        default=None,
    )
    initial_state_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    final_state_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    # Current locked fixture state. Fixture secrets stay redacted; the raw
    # payload policy matches durable job payloads (bounded JSON, no creds).
    state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
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

    tool_records: Mapped[list["AgentSimulationToolRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_sim_sessions_case_id", "case_id"),
        Index("ix_sim_sessions_run_id", "run_id"),
    )


class AgentSimulationToolRecord(Base):
    __tablename__ = "agent_simulation_tool_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_simulation_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments: Mapped[dict | None] = mapped_column(JSONB, default=None)
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    state_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    session: Mapped["AgentSimulationSession"] = relationship(
        back_populates="tool_records"
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id", "sequence", name="uq_sim_tool_records_session_sequence"
        ),
        Index("ix_sim_tool_records_session_id", "session_id"),
    )
