"""SolutionConfigSchema: a Solution-owned config DECLARATION.

A Solution declares the config it NEEDS (key/type/required/description/default);
the INSTALL holds the value as a plain instance-owned ``Config`` row. This table
is portable — it travels in the bundle and round-trips through the manifest. It
has NO ``value`` column by design, so a developer cannot commit a secret.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionConfigSchema(Base):
    __tablename__ = "solution_config_schema"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    description: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True)
    default: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    position: Mapped[int] = mapped_column(default=0)
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
