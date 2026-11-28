"""External JWT trust mappings for non-human Bifrost identities."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class FederatedCredential(Base):
    """One exact external JWT assertion profile bound to an identity.

    ``revision`` is carried by Bifrost access tokens and checked on every
    authenticated request. It makes credential edits and revocation take
    effect immediately even before an expired token naturally disappears.
    """

    __tablename__ = "federated_credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    discovery_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(1024), nullable=False)
    claims: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    subject_token_type: Mapped[str] = mapped_column(String(96), nullable=False)
    token_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("300"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_federated_credentials_exchange", "enabled", "subject_token_type"),
    )
