"""Persistent presentation receipts for immutable Product Updates entries."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class ProductUpdateReceipt(Base):
    """One acknowledgement presentation per admin and permanent entry UUID."""

    __tablename__ = "product_update_receipts"

    admin_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    entry_id: Mapped[UUID] = mapped_column(primary_key=True)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
