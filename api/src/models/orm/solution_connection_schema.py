"""SolutionConnectionSchema: a Solution-owned integration (connection) DECLARATION.

A Solution declares the integrations its code references (``integrations.get("X")``)
plus a secret-scrubbed TEMPLATE skeleton (config schema, OAuth provider shape, data
provider) so an install can pre-create an empty integration shell to fill in. Like
``SolutionConfigSchema`` it is portable and carries NO secrets by design.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class SolutionConnectionSchema(Base):
    __tablename__ = "solution_connection_schema"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False
    )
    integration_name: Mapped[str] = mapped_column(String(255), nullable=False)
    template: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
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

    __table_args__ = (
        Index("ix_solution_connection_schema_solution_id", "solution_id"),
        Index(
            "ix_solution_connection_schema_sol_name_unique",
            "solution_id",
            "integration_name",
            unique=True,
        ),
    )
