"""Durable intent to deliver one unit of domain work to an executor."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkDispatch(Base):
    """An accepted, not yet claimed unit of work (transactional outbox).

    Inserted in the same transaction as its domain record and deleted by the
    executor's claim, so the table only holds unclaimed work. The payload is
    the durable execution context; Redis is never its only copy.
    """

    __tablename__ = "work_dispatches"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    handler: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_version: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The executor that took the dispatch from PostgreSQL, until its claim.
    assigned_worker_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # Memory the work is expected to add (src.services.memory_requests); an
    # executor claims it only while it fits the executor's free budget.
    memory_request_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("67108864")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        UniqueConstraint("family", "subject_id", name="uq_work_dispatches_subject"),
        Index("ix_work_dispatches_created", "created_at"),
    )
