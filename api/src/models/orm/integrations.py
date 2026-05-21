"""
Integration and IntegrationMapping ORM models.

Represents integrations (OAuth providers, data providers, configurations)
and their mappings to organizations with external entities.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base



class Integration(Base):
    """Integration configuration and metadata.

    Represents an integration (e.g., "Microsoft Partner", "QuickBooks Online")
    with optional OAuth provider, data provider for entity listing, and
    configuration schema for available settings.
    """

    __tablename__ = "integrations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    list_entities_data_provider_id: Mapped[UUID | None] = mapped_column(
        default=None, nullable=True
    )
    entity_id: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True)
    entity_id_name: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True)
    default_entity_id: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
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
    oauth_provider: Mapped["OAuthProvider | None"] = relationship(
        back_populates="integration",
        lazy="joined",
    )
    mappings: Mapped[list["IntegrationMapping"]] = relationship(
        back_populates="integration",
        lazy="selectin",
    )
    config_schema: Mapped[list["IntegrationConfigSchema"]] = relationship(
        back_populates="integration",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="IntegrationConfigSchema.position",
    )

    __table_args__ = (
        Index("ix_integrations_name", "name"),
    )

    @property
    def has_oauth_config(self) -> bool:
        """Check if OAuth is configured for this integration."""
        return self.oauth_provider is not None


class IntegrationConfigSchema(Base):
    """Configuration schema item for an integration.

    Defines what configuration keys are available for an integration,
    their types, and validation rules. Normalized from JSONB for:
    - Referential integrity with cascade delete
    - Easier querying and updates
    - Foreign key support for config values
    """

    __tablename__ = "integration_config_schema"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # string, int, bool, json, secret
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True)
    options: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=None, nullable=True)
    position: Mapped[int] = mapped_column(default=0)  # For ordering
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
    integration: Mapped["Integration"] = relationship(
        back_populates="config_schema",
    )

    __table_args__ = (
        Index("ix_integration_config_schema_integration_id", "integration_id"),
        Index(
            "ix_integration_config_schema_unique_key",
            "integration_id",
            "key",
            unique=True,
        ),
    )


class IntegrationMapping(Base):
    """Integration mapping to an organization and external entity.

    Maps an integration to an organization with a specific external entity
    (e.g., tenant ID, company ID) and optional per-org OAuth token override.

    organization_id can be NULL for global/default mappings that apply when
    no org-specific mapping exists (cascade fallback pattern).
    """

    __tablename__ = "integration_mappings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, default=None
    )
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_name: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True)
    oauth_token_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("oauth_tokens.id"), default=None, nullable=True
    )
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
    integration: Mapped["Integration"] = relationship(
        back_populates="mappings",
        lazy="joined",
    )
    organization: Mapped["Organization | None"] = relationship(
        lazy="joined",
    )
    oauth_token: Mapped["OAuthToken | None"] = relationship(
        "OAuthToken",
        foreign_keys=[oauth_token_id],
        lazy="joined",
    )

    __table_args__ = (
        Index("ix_integration_mappings_integration_id", "integration_id"),
        Index("ix_integration_mappings_organization_id", "organization_id"),
        Index("ix_integration_mappings_oauth_token_id", "oauth_token_id"),
        Index(
            "ix_integration_mappings_unique_per_org",
            "integration_id",
            "organization_id",
            unique=True,
        ),
        # Partial unique index to enforce only ONE global mapping per integration.
        # The above index allows multiple NULL organization_id rows (NULL != NULL in SQL).
        # This ensures only one global mapping can exist per integration.
        Index(
            "ix_integration_mappings_unique_global",
            "integration_id",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
    )
