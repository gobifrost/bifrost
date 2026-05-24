"""
OAuthProvider and OAuthToken ORM models.

Represents OAuth provider configurations and user OAuth tokens for integrations.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base



class OAuthProvider(Base):
    """OAuth provider configuration."""

    __tablename__ = "oauth_providers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_name: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    oauth_flow_type: Mapped[str] = mapped_column(
        String(50), default="authorization_code"
    )
    client_id: Mapped[str] = mapped_column(String(255))
    encrypted_client_secret: Mapped[bytes] = mapped_column(LargeBinary)
    authorization_url: Mapped[str | None] = mapped_column(String(500), default=None)
    token_url: Mapped[str | None] = mapped_column(String(500), default=None)
    audience: Mapped[str | None] = mapped_column(String(500), default=None)
    token_url_defaults: Mapped[dict] = mapped_column(
        JSONB,
        default={},
        comment="Default values for URL template placeholders (e.g., {'entity_id': 'common'})"
    )
    entity_id_source: Mapped[dict | None] = mapped_column(
        JSONB,
        default=None,
        comment="Where to extract entity_id from OAuth callback artifacts; shape: {type: 'url_param'|'id_token_claim'|'token_response_field', key: '...'}",
    )
    scopes: Mapped[list] = mapped_column(JSONB, default=[])
    redirect_uri: Mapped[str | None] = mapped_column(String(500), default=None)
    status: Mapped[str] = mapped_column(String(50), default="not_connected")
    status_message: Mapped[str | None] = mapped_column(Text, default=None)
    last_token_refresh: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    provider_metadata: Mapped[dict] = mapped_column(JSONB, default={})
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
    integration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="SET NULL", onupdate="CASCADE"), default=None
    )
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    tokens: Mapped[list["OAuthToken"]] = relationship(back_populates="provider")
    integration: Mapped["Integration"] = relationship(
        "Integration", back_populates="oauth_provider"
    )

    __table_args__ = (
        Index(
            "ix_oauth_providers_org_name",
            "organization_id",
            "provider_name",
            unique=True,
        ),
    )


class OAuthToken(Base):
    """OAuth tokens for integration connections."""

    __tablename__ = "oauth_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("oauth_providers.id"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), default=None)
    encrypted_access_token: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(
        LargeBinary, default=None
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    scopes: Mapped[list] = mapped_column(JSONB, default=[])
    status: Mapped[str] = mapped_column(String(50), default="not_connected", server_default=text("'not_connected'"))
    status_message: Mapped[str | None] = mapped_column(Text, default=None)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    provider: Mapped["OAuthProvider"] = relationship(back_populates="tokens")
