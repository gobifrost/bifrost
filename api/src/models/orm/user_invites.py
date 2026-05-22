"""UserInvite ORM model — single-use, time-bound invite tokens."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base


class UserInvite(Base):
    """Single-use invite token for completing registration.

    `token_hash` stores a SHA-256 hex digest of the raw token; the raw token
    is only ever returned to the inviter at creation time.

    The `unique=True` on `user_id` enforces "one active invite per user" at the
    DB level; resend revokes the old row and inserts a new one.
    """

    __tablename__ = "user_invites"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)

    user = relationship("User")
