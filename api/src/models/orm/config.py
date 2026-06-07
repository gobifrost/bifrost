"""
Config and SystemConfig ORM models.

Represents configuration key-value storage for organizations and system settings.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum as SQLAlchemyEnum, ForeignKey, Index, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import ConfigType
from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization


# Execution-resolution entity — access via ConfigRepository (OrgScopedRepository).
# Cache lives on the repository as a transparent layer.
# See api/src/repositories/README.md.
class Config(Base):
    """Configuration key-value store.

    Stores actual config values for integrations. Each config entry references:
    - integration_id: The integration this config belongs to
    - organization_id: The org (NULL for integration-level defaults)
    - config_schema_id: The schema item defining this key (for cascade delete)
    - key: The config key (denormalized for query convenience)
    """

    __tablename__ = "configs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(255))
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    config_type: Mapped[ConfigType] = mapped_column(
        SQLAlchemyEnum(
            ConfigType,
            name="config_type",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ConfigType.STRING,
    )
    description: Mapped[str | None] = mapped_column(Text, default=None)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
    integration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE", onupdate="CASCADE"), default=None
    )
    config_schema_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("integration_config_schema.id", ondelete="CASCADE"), default=None
    )
    # Orphan provenance — set when a Solution install is deleted non-
    # destructively. Records which Solution this config value came from so a
    # reinstall can reattach it. origin_solution_id is informational (NOT a FK
    # — the Solution row is gone); origin_solution_slug is the stable reattach
    # key. orphaned_at non-null ⇔ currently orphaned.
    origin_solution_slug: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True)
    origin_solution_id: Mapped[UUID | None] = mapped_column(default=None, nullable=True)
    orphaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    updated_by: Mapped[str] = mapped_column(String(255))

    # Relationships
    organization: Mapped["Organization | None"] = relationship(back_populates="configs")

    __table_args__ = (
        Index("ix_configs_integration_org_key", "integration_id", "organization_id", "key", unique=True),
        Index("ix_configs_schema_id", "config_schema_id"),
    )


# Execution-resolution entity — system settings with per-org overrides
# (category+key). Access via SystemConfigRepository (OrgScopedRepository).
# See api/src/repositories/README.md.
class SystemConfig(Base):
    """
    System-level configuration storage.

    Stores system settings like GitHub integration, branding assets, etc.
    Uses category+key for organization:
    - GitHub: category='github', key='integration'
    - Branding: category='branding', key='logo'

    value_json: For JSON config data
    value_bytes: For binary data (logos, files, etc.)

    Services handle their own encryption as needed.
    """

    __tablename__ = "system_configs"
    __table_args__ = (
        Index("ix_system_configs_category", "category"),
        Index("ix_system_configs_category_key", "category", "key"),
        Index("ix_system_configs_org_id", "organization_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    value_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
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
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    organization: Mapped["Organization | None"] = relationship(
        "Organization", back_populates="system_configs"
    )
