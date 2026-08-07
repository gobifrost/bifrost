"""Private Solution builder state: project pointers, immutable source revisions,
chat sessions, and agent turns.

Object storage remains the source of truth for revision content; these rows carry
identity, lineage, and status only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionBuilderProject(Base):
    """Per-Solution builder pointers: latest source and last deployed preview."""

    __tablename__ = "solution_builder_projects"

    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "solution_source_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_solution_builder_projects_current_revision_id",
        ),
        nullable=True,
        default=None,
    )
    deployed_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "solution_source_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_solution_builder_projects_deployed_revision_id",
        ),
        nullable=True,
        default=None,
    )
    promotion_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="none",
        server_default="none",
    )
    promotion_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "solution_source_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_solution_builder_projects_promotion_revision_id",
        ),
        nullable=True,
        default=None,
    )
    promotion_requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    promotion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
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


class SolutionSourceRevision(Base):
    """One immutable source snapshot. Content lives at
    ``_solution_builder/{solution_id}/revisions/{id}/source.zip``.
    """

    __tablename__ = "solution_source_revisions"
    __table_args__ = (
        Index("ix_solution_source_revisions_solution_created", "solution_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    restored_from_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )


class SolutionBuilderSession(Base):
    """Typed link between a Conversation and the Solution it is building."""

    __tablename__ = "solution_builder_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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


class SolutionBuilderTurn(Base):
    """One agent turn: base revision in, output revision out, plus build/deploy status."""

    __tablename__ = "solution_builder_turns"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("solution_builder_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    base_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    output_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # Build and deploy job tables land in WP3; keep the reference columns without FKs.
    build_job_id: Mapped[UUID | None] = mapped_column(nullable=True, default=None)
    deploy_job_id: Mapped[UUID | None] = mapped_column(nullable=True, default=None)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
