"""Agent review definition and run ORM models.

Review jobs use PlatformJob for lifecycle. These rows hold durable admission,
evidence, result, and accounting linkage facts only.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base

AGENT_REVIEW_STATUSES = ("active", "disabled")
MAX_REVIEW_INPUT_BYTES = 4 * 1024 * 1024
MAX_REVIEW_RUNS = 20


class AgentReviewDefinition(Base):
    """Versioned review definition for an agent."""

    __tablename__ = "agent_review_definitions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=text("'active'")
    )
    latest_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
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
        Index("ix_agent_review_definitions_agent_id", "agent_id"),
        Index("ix_agent_review_definitions_org_id", "org_id"),
        Index("ix_agent_review_definitions_status", "status"),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_agent_review_definitions_status",
        ),
        CheckConstraint(
            "latest_version > 0",
            name="ck_agent_review_definitions_latest_version_positive",
        ),
        CheckConstraint(
            "length(btrim(name)) > 0",
            name="ck_agent_review_definitions_name_nonblank",
        ),
    )


class AgentReviewVersion(Base):
    """Immutable review statement version."""

    __tablename__ = "agent_review_versions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_review_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    review_statement: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_format_instructions: Mapped[str | None] = mapped_column(Text, default=None)
    model_profile_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), default=None
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "review_id", "version", name="uq_agent_review_versions_review_version"
        ),
        Index("ix_agent_review_versions_review_id", "review_id"),
        CheckConstraint(
            "version > 0", name="ck_agent_review_versions_version_positive"
        ),
        CheckConstraint(
            "length(btrim(review_statement)) > 0",
            name="ck_agent_review_versions_statement_nonblank",
        ),
        CheckConstraint(
            "length(review_statement) <= 8000",
            name="ck_agent_review_versions_statement_length",
        ),
        CheckConstraint(
            "evidence_format_instructions IS NULL OR "
            "length(evidence_format_instructions) <= 4000",
            name="ck_agent_review_versions_format_instructions_length",
        ),
    )


class AgentReviewRun(Base):
    """Immutable review admission/evidence/result row linked to PlatformJob lifecycle."""

    __tablename__ = "agent_review_runs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_review_definitions.id", ondelete="CASCADE"), nullable=False
    )
    review_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_review_versions.id", ondelete="CASCADE"), nullable=False
    )
    review_version: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    platform_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("platform_jobs.id", ondelete="SET NULL"), default=None, unique=True
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    requested_run_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default=text("'{}'::uuid[]"),
    )
    selected_run_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default=text("'{}'::uuid[]"),
    )
    source_evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    profile_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    profile_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    input_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("ix_agent_review_runs_review_id", "review_id"),
        Index("ix_agent_review_runs_agent_id", "agent_id"),
        Index("ix_agent_review_runs_org_id", "org_id"),
        Index("ix_agent_review_runs_platform_job_id", "platform_job_id"),
        CheckConstraint(
            "review_version > 0", name="ck_agent_review_runs_version_positive"
        ),
        CheckConstraint(
            "cardinality(requested_run_ids) BETWEEN 1 AND 20",
            name="ck_agent_review_runs_requested_run_count",
        ),
        CheckConstraint(
            "cardinality(selected_run_ids) BETWEEN 1 AND 20",
            name="ck_agent_review_runs_selected_run_count",
        ),
        CheckConstraint(
            "input_bytes > 0 AND input_bytes <= 4194304",
            name="ck_agent_review_runs_input_bytes_bounds",
        ),
        CheckConstraint(
            "length(btrim(profile_fingerprint)) > 0",
            name="ck_agent_review_runs_profile_fingerprint_nonblank",
        ),
        CheckConstraint(
            "length(btrim(request_fingerprint)) > 0",
            name="ck_agent_review_runs_request_fingerprint_nonblank",
        ),
    )
