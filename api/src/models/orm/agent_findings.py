"""First-class agent findings.

A finding records an observed problem, the expected behavior, its source,
and links to regression cases intended to reproduce it. Identity is separate
from runs and cases: a manually reported problem may have no run, several
cases may address one finding, and a passing test never auto-resolves it.

Evidence access follows existing tenant and resource authorization. Linking
an external source never authorizes fetching it. Case linkage is held on the
case side (``AgentEvaluationCase.finding_id``); this table stores no
duplicated link list.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base

FINDING_STATUSES = ("open", "dismissed")
FINDING_SOURCE_KINDS = ("run", "manual", "external")
FINDING_KINDS = ("problem", "opportunity")


class AgentFinding(Base):
    __tablename__ = "agent_findings"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default=text("'open'")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str | None] = mapped_column(Text, default=None)
    source_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default=text("'manual'")
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), default=None
    )
    source_sequence: Mapped[int | None] = mapped_column(default=None)
    external_ref: Mapped[str | None] = mapped_column(String(500), default=None)
    finding_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="problem", server_default=text("'problem'")
    )
    evidence_markdown: Mapped[str | None] = mapped_column(Text, default=None)
    source_review_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), default=None
    )
    source_review_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), default=None
    )
    source_review_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), default=None
    )
    source_review_version: Mapped[int | None] = mapped_column(default=None)
    source_run_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    source_ordinal: Mapped[int | None] = mapped_column(default=None)
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
        Index("ix_agent_findings_agent_id", "agent_id"),
        Index("ix_agent_findings_org_id", "org_id"),
        Index("ix_agent_findings_status", "status"),
        Index("ix_agent_findings_source_review_id", "source_review_id"),
        Index("ix_agent_findings_source_review_run_id", "source_review_run_id"),
        Index("ix_agent_findings_finding_kind", "finding_kind"),
        Index(
            "uq_agent_findings_review_run_ordinal",
            "source_review_run_id",
            "source_ordinal",
            unique=True,
            postgresql_where=text(
                "source_review_run_id IS NOT NULL AND source_ordinal IS NOT NULL"
            ),
        ),
        CheckConstraint(
            "status IN ('open', 'dismissed')",
            name="ck_agent_findings_status",
        ),
        CheckConstraint(
            "source_kind IN ('run', 'manual', 'external')",
            name="ck_agent_findings_source_kind",
        ),
        CheckConstraint(
            "finding_kind IN ('problem', 'opportunity')",
            name="ck_agent_findings_finding_kind",
        ),
        CheckConstraint(
            "evidence_markdown IS NULL OR length(evidence_markdown) <= 20000",
            name="ck_agent_findings_evidence_markdown_length",
        ),
        CheckConstraint(
            "("
            "source_review_id IS NULL "
            "AND source_review_version_id IS NULL "
            "AND source_review_run_id IS NULL "
            "AND source_review_version IS NULL "
            "AND source_ordinal IS NULL"
            ") OR ("
            "source_review_id IS NOT NULL "
            "AND source_review_version_id IS NOT NULL "
            "AND source_review_run_id IS NOT NULL "
            "AND source_review_version IS NOT NULL "
            "AND source_review_version > 0 "
            "AND source_ordinal IS NOT NULL "
            "AND source_ordinal >= 0 "
            "AND jsonb_typeof(source_run_refs) = 'array' "
            "AND jsonb_array_length(source_run_refs) > 0"
            ")",
            name="ck_agent_findings_review_provenance_complete",
        ),
    )
