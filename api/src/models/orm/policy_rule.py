"""Reusable named policy rule, referenced by {"$ref": name} from file/table policies."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class PolicyRule(Base):
    """A named, reusable policy rule body ({actions, when}). Cascade org→global; per-domain."""

    __tablename__ = "policy_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(ForeignKey("organizations.id"), default=None)
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), default=None
    )  # Codex R2/C1: solution-shippable rules
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(8), nullable=False)  # 'file' | 'table'
    description: Mapped[str | None] = mapped_column(Text, default=None)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None)

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("is_builtin", False)
        super().__init__(**kwargs)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Partial unique indexes per scope tier — NULLs don't compare equal, so a plain
        # UNIQUE would allow duplicate globals and break scalar_one_or_none() (correction #4).
        # Three mutually-exclusive tiers: global (both NULL), org (org set, sol NULL),
        # solution (sol set). Codex R2/C1 adds the solution tier.
        Index(
            "uq_policy_rules_global_name_domain",
            "name",
            "domain",
            unique=True,
            postgresql_where=text("organization_id IS NULL AND solution_id IS NULL"),
        ),
        Index(
            "uq_policy_rules_org_name_domain",
            "organization_id",
            "name",
            "domain",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL AND solution_id IS NULL"),
        ),
        Index(
            "uq_policy_rules_solution_name_domain",
            "solution_id",
            "name",
            "domain",
            unique=True,
            postgresql_where=text("solution_id IS NOT NULL"),
        ),
    )
