"""Role grants on Solutions."""

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
    from src.models.orm.solutions import Solution
    from src.models.orm.users import Role, User


class SolutionRoleGrant(Base):
    """A direct view/edit grant on one Solution for one Role."""

    __tablename__ = "solution_role_grants"
    __table_args__ = (
        UniqueConstraint(
            "solution_id",
            "role_id",
            name="uq_solution_role_grants_solution_role",
        ),
        Index("ix_solution_role_grants_solution", "solution_id"),
        Index("ix_solution_role_grants_role", "role_id"),
        CheckConstraint(
            "access IN ('view', 'edit')",
            name="ck_solution_role_grants_access",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    access: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="edit",
        server_default="edit",
    )
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
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

    solution: Mapped["Solution"] = relationship(back_populates="role_grants")
    role: Mapped["Role"] = relationship(back_populates="solution_grants")
    granted_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[granted_by_user_id]
    )
