"""Deployment-owned GitHub App credentials and explicitly claimed installations."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class GitHubAppRegistration(Base):
    __tablename__ = "github_app_registrations"
    __table_args__ = (CheckConstraint("id = 1", name="github_app_single_registration"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    app_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_private_key: Mapped[str | None] = mapped_column(Text)
    encrypted_webhook_secret: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    recovery_cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_gap: Mapped[bool] = mapped_column(Boolean, default=False)
    redelivery_requests: Mapped[dict] = mapped_column(JSONB, default=dict)


class GitHubAppInstallation(Base):
    __tablename__ = "github_app_installations"
    __table_args__ = (UniqueConstraint("installation_id", name="github_app_installation_claim"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    app_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    event_source_id: Mapped[UUID] = mapped_column(ForeignKey("event_sources.id", ondelete="CASCADE"), unique=True)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict)
    repositories: Mapped[list] = mapped_column(JSONB, default=list)
    generation: Mapped[UUID] = mapped_column(default=uuid4)
    status: Mapped[str] = mapped_column(String(30), default="active")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(255))


class GitHubAppDelivery(Base):
    """A provider identity tombstone, not a second event inbox or execution queue."""

    __tablename__ = "github_app_deliveries"

    app_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"), index=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
