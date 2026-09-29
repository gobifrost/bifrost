"""SolutionWorkflowPermissionRequest: what a Solution asks for a workflow.

The Solution manifest declares each workflow's requested mode and grants
(portable). What an admin approved lives here, on the install's record
(environment-specific): ``approved_digest`` is the digest of the approved
request. Data only until delegated execution reads it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionWorkflowPermissionRequest(Base):
    __tablename__ = "solution_workflow_permission_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    requested_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # List of {"permission": str, "boundary": str | None}
    requested_grants: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    approved_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    decided_by: Mapped[str | None] = mapped_column(String(255), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
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

    __table_args__ = (
        CheckConstraint(
            "requested_mode IN ('full', 'restricted')",
            name="ck_solution_wf_perm_requests_mode",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'declined')",
            name="ck_solution_wf_perm_requests_status",
        ),
        UniqueConstraint(
            "solution_id", "workflow_id", name="uq_solution_wf_perm_requests_solution_workflow"
        ),
        Index("ix_solution_wf_perm_requests_solution_id", "solution_id"),
    )
