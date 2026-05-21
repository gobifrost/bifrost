"""
WorkflowRole ORM model.

Junction table for workflow role-based access control,
following the same pattern as FormRole, AppRole, AgentRole.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base



class WorkflowRole(Base):
    """Workflow-Role association table.

    Links workflows to roles for role-based access control.
    Follows the same pattern as FormRole, AppRole, AgentRole for consistency.
    """

    __tablename__ = "workflow_roles"

    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[str | None] = mapped_column(String(255), default=None)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="workflow_roles")
    role: Mapped["Role"] = relationship()

    __table_args__ = (
        Index("ix_workflow_roles_role_id", "role_id"),
    )
