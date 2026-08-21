"""Organization groups and membership persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization


class OrganizationGroup(Base):
    """A provider-owned organization group / pod."""

    __tablename__ = "organization_groups"
    __table_args__ = (
        UniqueConstraint(
            "owner_organization_id",
            "name",
            name="uq_organization_groups_owner_name",
        ),
        Index("ix_organization_groups_owner", "owner_organization_id"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    owner_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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

    owner_organization: Mapped["Organization"] = relationship(
        back_populates="organization_groups",
        foreign_keys=[owner_organization_id],
    )
    memberships: Mapped[list["OrganizationGroupMembership"]] = relationship(
        back_populates="organization_group",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OrganizationGroupMembership.organization_id",
    )


class OrganizationGroupMembership(Base):
    """Membership of one organization in one provider-owned group."""

    __tablename__ = "organization_group_members"
    __table_args__ = (
        UniqueConstraint(
            "organization_group_id",
            "organization_id",
            name="uq_organization_group_members_group_org",
        ),
        Index("ix_organization_group_members_organization", "organization_id"),
    )

    organization_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("organization_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    organization_group: Mapped["OrganizationGroup"] = relationship(
        back_populates="memberships"
    )
    organization: Mapped["Organization"] = relationship(foreign_keys=[organization_id])
