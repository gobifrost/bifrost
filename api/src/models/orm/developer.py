"""
Developer context ORM model.

Represents developer configuration for SDK usage.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base



class DeveloperContext(Base):
    """
    Developer context for SDK configuration.

    Stores per-user development settings used by the Bifrost SDK
    for local development and debugging.
    """

    __tablename__ = "developer_contexts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    # Context configuration
    default_org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), default=None
    )
    default_parameters: Mapped[dict] = mapped_column(JSONB, default={})
    track_executions: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="developer_context")
    default_org: Mapped["Organization | None"] = relationship()

    __table_args__ = (
        Index("ix_developer_contexts_user_id", "user_id"),
    )
