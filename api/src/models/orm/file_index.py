"""
Source search index ORM models.

``FileIndex`` mirrors the instance ``_repo/`` workspace and
``SolutionFileIndex`` mirrors each Solution install's ``_solutions/{id}/``
source. ``FileIndexService`` is their only writer: every tracked object has a
row, and ``content`` is NULL when the bytes are binary, invalid UTF-8, or over
the size cap. No entity routing, no polymorphic references.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class FileIndex(Base):
    """Search index for workspace files in _repo/."""

    __tablename__ = "file_index"

    path: Mapped[str] = mapped_column(String(1000), primary_key=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SolutionFileIndex(Base):
    """Search index for Solution install source in _solutions/{solution_id}/."""

    __tablename__ = "solution_file_index"

    solution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("solutions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    path: Mapped[str] = mapped_column(String(1000), primary_key=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
