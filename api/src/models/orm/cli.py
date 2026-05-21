"""
CLI Session ORM model.

Represents CLI debugging sessions for local workflow execution.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base



class CLISession(Base):
    """CLI Session database table.

    Represents a local workflow debugging session started by the CLI.
    Each session can have multiple executions (re-runs).
    """

    __tablename__ = "cli_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    workflows: Mapped[dict] = mapped_column(JSONB, nullable=False)
    selected_workflow: Mapped[str | None] = mapped_column(Text, default=None)
    params: Mapped[dict | None] = mapped_column(JSONB, default=None)
    pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Relationships
    user: Mapped["User"] = relationship()
    executions: Mapped[list["Execution"]] = relationship(back_populates="cli_session")

    __table_args__ = (
        Index("ix_cli_sessions_user_id", "user_id"),
    )
