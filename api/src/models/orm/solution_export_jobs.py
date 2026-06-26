"""Durable async solution backup export job rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution
    from src.models.orm.users import User


class SolutionExportJob(Base):
    __tablename__ = "solution_export_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    requested_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )
    # Notifications are contractually optional, but this branch has no
    # notifications table/ORM yet. Keep the reference column ready without an FK.
    notification_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    encrypted_options: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    artifact_storage_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        index=True,
        default=None,
    )
    artifact_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )
    artifact_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        default=None,
    )
    artifact_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        default=None,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    solution: Mapped["Solution"] = relationship("Solution")
    organization: Mapped["Organization | None"] = relationship("Organization")
    requested_by: Mapped["User | None"] = relationship("User")
