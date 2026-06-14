"""
Solution ORM model.

A Solution is an *installable surface* — the deployable unit installed onto a
Bifrost instance (success-criteria doc §3.1). Each row here is one **install**,
identified by ``id`` (the ``solution_id`` stamped on managed entities). One
Solution *definition* (same ``slug``) can be installed multiple times — once per
scope — producing multiple rows with the same slug and distinct ids/scopes
(§3.4).

Scope (§3.3) is expressed with the platform's existing scoping system via
``organization_id``: a UUID = org scope (visible to that one org), ``NULL`` =
global scope (visible across the tenant). There is no per-entity scope binding —
the install's scope is inherited by everything it deploys.

Source mode (§3.9) keeps the one-writer invariant: a *disconnected* install is
written only by ``bifrost deploy``; a *git-connected* install
(``git_connected=True``) is written only by auto-pull from its repo, and deploy
is refused.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.solution_connection_schema import SolutionConnectionSchema


# Identity entity — a Solution install. Managed entities reference it by
# solution_id. It is NOT itself resolved by name with cascade (it is not an
# execution-resolution entity), so it does not go through OrgScopedRepository.
class Solution(Base):
    """One installed Solution (an *install*), keyed by ``id`` == solution_id."""

    __tablename__ = "solutions"

    # A Solution installs AT MOST ONCE per scope (one org, or global). Two
    # installs of the same slug in one org would let a v2 app's path::fn workflow
    # ref resolve a sibling install's workflow (Codex #8 P1); the constraint makes
    # that state unreachable. organization_id is nullable and NULLs don't compare
    # equal in a plain unique index, so global installs need a slug-only partial
    # index of their own. Mirrors migration 20260605_solution_unique_scope.
    __table_args__ = (
        Index(
            "ix_solutions_slug_org_unique",
            "slug",
            "organization_id",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        Index(
            "ix_solutions_slug_global_unique",
            "slug",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Definition identity (shared across installs of the same Solution).
    slug: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255))

    # Scope (§3.3): UUID = org scope, NULL = global scope.
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
        index=True,
    )

    # Whether this Solution's code may import shared modules from _repo/ (§3.5).
    # Orthogonal to scope. Off by default — Solutions are self-contained worlds.
    global_repo_access: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    # Version bookkeeping (Task 20). ``version`` is the deployed bundle's
    # declared version (bifrost.solution.yaml ``version:``), recorded by deploy;
    # ``upgraded_from_version`` is what the last version-changing deploy
    # replaced. Free-form strings — PEP 440 ordering is attempted only by the
    # downgrade gate; unordered versions are never blocked.
    version: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    upgraded_from_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )

    # Setup completeness (Task 5/6). A solution is "incomplete" when it declares
    # required configs that have no value set. Default True = nothing unset (a
    # freshly installed solution with no required configs is immediately complete).
    # Task 6/7 compute and flip this flag after each deploy/config change.
    setup_complete: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    # Source mode (§3.9). Disconnected (default): deploy is the only writer.
    # Connected: auto-pull from git_repo_url is the only writer; deploy refused.
    git_connected: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    git_repo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True, default=None)

    # Solution-level icon shown on the /solutions catalog (mirrors the app-logo
    # plumbing): declared by ``logo:`` in bifrost.solution.yaml, validated and
    # stamped by deploy (present => set, absent => cleared).
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    logo_content_type: Mapped[str | None] = mapped_column(String(100), default=None)

    # Long-form README markdown (Task 6). Rendered on the solution's README tab.
    # Synced from the bundle's README file by deploy; portable, carries no secrets.
    readme: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)

    # Declared integration (connection) requirements the install must satisfy
    # (Task 2/4/5). Portable, secret-scrubbed templates — see
    # SolutionConnectionSchema.
    connection_schema: Mapped[list["SolutionConnectionSchema"]] = relationship(
        "SolutionConnectionSchema",
        cascade="all, delete-orphan",
        order_by="SolutionConnectionSchema.position",
        lazy="selectin",
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
