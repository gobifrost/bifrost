"""Custom Claims ORM — org-scoped, referenced by name from table policies."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization


class CustomClaim(Base):
    __tablename__ = "custom_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="list")
    query: Mapped[dict] = mapped_column(JSONB, nullable=False)
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

    # One-way ref to Organization (no back-populates on the other side —
    # see organizations.py). Kept for ORM-level navigation from a claim
    # row to its org without a second query; the import lives under
    # TYPE_CHECKING because the annotation is a string forward-reference.
    organization: Mapped["Organization"] = relationship("Organization")

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_custom_claims_org_name"),
        Index("ix_custom_claims_organization_id", "organization_id"),
    )
