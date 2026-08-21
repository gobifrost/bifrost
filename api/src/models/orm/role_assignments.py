"""Role assignment persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization
    from src.models.orm.organization_groups import OrganizationGroup
    from src.models.orm.users import Role, User


ROLE_ASSIGNMENT_BOUNDARY_KINDS = (
    "organization",
    "organization_group",
    "managed_organizations",
    "platform",
)


class RoleAssignment(Base):
    """One durable assignment of a role to a user."""

    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_role_assignments_user_role"),
        Index("ix_role_assignments_user", "user_id"),
        Index("ix_role_assignments_role", "role_id"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    user: Mapped["User"] = relationship(
        back_populates="role_assignments",
        foreign_keys=[user_id],
    )
    role: Mapped["Role"] = relationship(back_populates="role_assignments")
    boundaries: Mapped[list["RoleAssignmentBoundary"]] = relationship(
        back_populates="role_assignment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RoleAssignmentBoundary.id",
    )


class RoleAssignmentBoundary(Base):
    """One boundary selection attached to a role assignment."""

    __tablename__ = "role_assignment_boundaries"
    __table_args__ = (
        CheckConstraint(
            "boundary_kind IN ('organization', 'organization_group', 'managed_organizations', 'platform')",
            name="ck_role_assignment_boundaries_kind",
        ),
        CheckConstraint(
            "(boundary_kind = 'organization' AND organization_id IS NOT NULL AND organization_group_id IS NULL) "
            "OR (boundary_kind = 'organization_group' AND organization_group_id IS NOT NULL AND organization_id IS NULL) "
            "OR (boundary_kind IN ('managed_organizations', 'platform') AND organization_id IS NULL AND organization_group_id IS NULL)",
            name="ck_role_assignment_boundaries_shape",
        ),
        Index(
            "uq_role_assignment_boundaries_organization",
            "role_assignment_id",
            "organization_id",
            unique=True,
            postgresql_where=text("boundary_kind = 'organization'"),
        ),
        Index(
            "uq_role_assignment_boundaries_organization_group",
            "role_assignment_id",
            "organization_group_id",
            unique=True,
            postgresql_where=text("boundary_kind = 'organization_group'"),
        ),
        Index(
            "uq_role_assignment_boundaries_managed_organizations",
            "role_assignment_id",
            unique=True,
            postgresql_where=text("boundary_kind = 'managed_organizations'"),
        ),
        Index(
            "uq_role_assignment_boundaries_platform",
            "role_assignment_id",
            unique=True,
            postgresql_where=text("boundary_kind = 'platform'"),
        ),
        Index("ix_role_assignment_boundaries_assignment", "role_assignment_id"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    role_assignment_id: Mapped[UUID] = mapped_column(
        ForeignKey("role_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    boundary_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    organization_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_groups.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )

    role_assignment: Mapped["RoleAssignment"] = relationship(
        back_populates="boundaries"
    )
    organization: Mapped["Organization | None"] = relationship(
        foreign_keys=[organization_id]
    )
    organization_group: Mapped["OrganizationGroup | None"] = relationship(
        foreign_keys=[organization_group_id]
    )
