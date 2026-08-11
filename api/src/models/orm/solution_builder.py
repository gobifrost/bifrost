"""Private Solution builder state: project pointers, immutable source revisions,
chat sessions, and agent turns.

Object storage remains the source of truth for revision content; these rows carry
identity, lineage, and status only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionBuilderProject(Base):
    """Per-Solution builder pointers: latest source and last deployed preview."""

    __tablename__ = "solution_builder_projects"
    __table_args__ = (
        Index(
            "ix_solution_builder_projects_global_target_unique",
            "target_kind",
            unique=True,
            postgresql_where=text("target_kind = 'global_repo'"),
        ),
        CheckConstraint(
            "target_kind IN ('solution', 'global_repo')",
            name="ck_solution_builder_projects_target_kind",
        ),
    )

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
    target_kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="solution",
        server_default="solution",
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


class SolutionBuilderCollaborator(Base):
    """An explicit user grant on one private Builder Solution.

    Provider support access is role/scope driven and deliberately does not
    create rows here. These rows represent people the Solution owner chose to
    add to the working team, so they remain visible in the ordinary ``My work``
    view and portable support staff do not clutter it.
    """

    __tablename__ = "solution_builder_collaborators"
    __table_args__ = (
        UniqueConstraint(
            "solution_id",
            "user_id",
            name="uq_solution_builder_collaborator_user",
        ),
        Index("ix_solution_builder_collaborators_user", "user_id"),
        CheckConstraint(
            "access IN ('view', 'edit')",
            name="ck_solution_builder_collaborators_access",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    access: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="edit",
        server_default="edit",
    )
    invited_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
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


class SolutionBuilderRelease(Base):
    """A shared install published from one private Builder source project.

    The source Solution remains private and owns revisions, sessions, and
    previews. This row records the stable shared install for one target scope so
    later approvals update that release without converting or discarding the
    source workbench.
    """

    __tablename__ = "solution_builder_releases"
    __table_args__ = (
        Index(
            "ix_solution_builder_releases_source_org_unique",
            "source_solution_id",
            "target_organization_id",
            unique=True,
            postgresql_where=text("target_organization_id IS NOT NULL"),
        ),
        Index(
            "ix_solution_builder_releases_source_global_unique",
            "source_solution_id",
            unique=True,
            postgresql_where=text("target_organization_id IS NULL"),
        ),
        CheckConstraint(
            "source_solution_id <> published_solution_id",
            name="ck_solution_builder_releases_distinct_solutions",
        ),
        CheckConstraint(
            "runtime_mode IN ('isolated', 'trusted')",
            name="ck_solution_builder_releases_runtime_mode",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    published_solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    target_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
        index=True,
    )
    published_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    runtime_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="isolated",
        server_default="isolated",
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
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


class SolutionGlobalWorkspaceApply(Base):
    """One reviewed application of a global-workspace proposal to ``_repo``."""

    __tablename__ = "solution_global_workspace_applies"
    __table_args__ = (
        Index(
            "ix_solution_global_workspace_applies_solution_applied",
            "solution_id",
            "applied_at",
        ),
        CheckConstraint(
            "state IN ('applied', 'superseded', 'rolled_back')",
            name="ck_solution_global_workspace_applies_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("solution_source_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="applied",
        server_default="applied",
    )
    applied_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    rolled_back_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
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
    resume_from_turn_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_builder_turns.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
    )
    checkpoint_sha256: Mapped[str | None] = mapped_column(
        String(64),
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

    @property
    def checkpoint_available(self) -> bool:
        return self.checkpoint_sha256 is not None
