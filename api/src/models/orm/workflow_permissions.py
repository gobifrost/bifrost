"""Workflow capability grants for restricted workflows."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkflowPermissionGrant(Base):
    __tablename__ = "workflow_permission_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    permission: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)

    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "permission",
            name="uq_workflow_permission_grants_grant",
        ),
        Index("ix_workflow_permission_grants_workflow_id", "workflow_id"),
    )
