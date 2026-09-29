"""WorkflowPermissionGrant ORM model.

A permission (with optional cross-org boundary) that a restricted workflow
always runs with. Data only until delegated execution reads it.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkflowPermissionGrant(Base):
    __tablename__ = "workflow_permission_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    permission: Mapped[str] = mapped_column(String(100), nullable=False)
    boundary_kind: Mapped[str | None] = mapped_column(String(32), default=None)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)

    __table_args__ = (
        CheckConstraint(
            "boundary_kind IN ('organization', 'managed_organizations', 'platform')",
            name="ck_workflow_permission_grants_boundary_kind",
        ),
        CheckConstraint(
            "(boundary_kind = 'organization' AND organization_id IS NOT NULL) "
            "OR (boundary_kind IS DISTINCT FROM 'organization' AND organization_id IS NULL)",
            name="ck_workflow_permission_grants_org_boundary",
        ),
        UniqueConstraint(
            "workflow_id",
            "permission",
            "boundary_kind",
            "organization_id",
            name="uq_workflow_permission_grants_grant",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_workflow_permission_grants_workflow_id", "workflow_id"),
    )
