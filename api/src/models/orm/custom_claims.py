"""Custom Claims ORM — scoped claim definitions referenced by table policies."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution


# Execution-resolution entity — referenced by name from table policies and
# resolved during policy evaluation. Loose claims live in the _repo namespace
# (global or org); solution-managed claims live inside one install namespace.
class CustomClaim(Base):
    __tablename__ = "custom_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
        index=True,
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
    organization: Mapped["Organization | None"] = relationship("Organization")
    solution: Mapped["Solution | None"] = relationship("Solution")

    __table_args__ = (
        Index(
            "uq_custom_claims_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL AND solution_id IS NULL"),
        ),
        Index(
            "uq_custom_claims_global_name",
            "name",
            unique=True,
            postgresql_where=text("organization_id IS NULL AND solution_id IS NULL"),
        ),
        Index(
            "uq_custom_claims_solution_name",
            "solution_id",
            "name",
            unique=True,
            postgresql_where=text("solution_id IS NOT NULL"),
        ),
        Index("ix_custom_claims_organization_id", "organization_id"),
    )
