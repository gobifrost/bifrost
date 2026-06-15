"""Queue of entities captured into a Solution but not yet pulled into source.

A capture (UI or CLI) sets solution_id on the entity server-side AND inserts a
row here. ``bifrost solution pull`` materializes the entity into the workspace
``.bifrost/`` manifest and clears its row. Deploy 409-blocks while any row for the
install is absent from the incoming manifest — so a captured-but-unpulled entity
is never silently deleted by deploy's full-replace reconcile.

This is NOT a solution-managed entity table — its own rows are bookkeeping for the
round-trip and are written outside the read-only ``before_flush`` guard.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class PendingCaptureORM(Base):
    __tablename__ = "pending_captures"
    __table_args__ = (
        UniqueConstraint(
            "solution_id",
            "entity_type",
            "entity_id",
            name="uq_pending_capture_entity",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32))  # table|form|agent|config|event|claim
    entity_id: Mapped[str] = mapped_column(String(255))  # entity id; for config, its key
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    captured_by: Mapped[UUID | None] = mapped_column(default=None, nullable=True)
