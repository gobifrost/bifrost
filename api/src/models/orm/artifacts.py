"""Canonical binary artifacts shared by workflows, Chat, and MCP."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.agents import MessageAttachment


class Artifact(Base):
    """One stored file with an opaque identity independent of its presentation."""

    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    workspace_id: Mapped[UUID | None] = mapped_column(nullable=True)
    logical_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    chat_bindings: Mapped[list["MessageAttachment"]] = relationship(
        back_populates="artifact",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_artifacts_created_by_user_id", "created_by_user_id"),
        Index("ix_artifacts_organization_id", "organization_id"),
        Index("ix_artifacts_workspace_path", "workspace_id", "logical_path"),
        Index("ix_artifacts_created_at", "created_at"),
    )
