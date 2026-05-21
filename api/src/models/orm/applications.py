"""
Application ORM model.

Represents applications with:
- applications: metadata, access control, published_snapshot
- Files stored in S3 via file_index (not in database tables)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import AppAccessLevel
from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.app_embed_secrets import AppEmbedSecret
    from src.models.orm.app_roles import AppRole
    from src.models.orm.organizations import Organization


class Application(Base):
    """Application entity for App Builder.

    Applications hold app metadata. Files are stored in S3 at
    _repo/{repo_path}/ paths, indexed in file_index table.

    - organization_id = NULL: Global application (platform-wide)
    - organization_id = UUID: Organization-scoped application
    """

    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_path: Mapped[str] = mapped_column(String(500), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )

    # Publish history
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Published snapshot: {path: content_hash} mapping from file_index
    # NULL = never published, empty dict = published with no files
    published_snapshot: Mapped[dict | None] = mapped_column(
        JSON, default=None, nullable=True
    )

    # Access control (follows same pattern as forms)
    access_level: Mapped[str] = mapped_column(
        String(20), default=AppAccessLevel.AUTHENTICATED, server_default="'authenticated'"
    )

    # Metadata
    description: Mapped[str | None] = mapped_column(Text, default=None)
    dependencies: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), default=None)
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    logo_content_type: Mapped[str | None] = mapped_column(String(50), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)

    # Relationships
    organization: Mapped["Organization | None"] = relationship(
        "Organization", back_populates="applications"
    )
    roles: Mapped[list["AppRole"]] = relationship(
        "AppRole", cascade="all, delete-orphan", passive_deletes=True
    )
    embed_secrets: Mapped[list["AppEmbedSecret"]] = relationship(
        "AppEmbedSecret", back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_applications_organization_id", "organization_id"),
        # Global unique index on slug handled in migration
    )

    @property
    def is_published(self) -> bool:
        """Check if the application has been published at least once."""
        return self.published_snapshot is not None

    @property
    def has_unpublished_changes(self) -> bool:
        """Check if there are unpublished changes in the draft.

        TODO: Compare current file_index state vs published_snapshot to detect
        actual changes. For now, always return True if published (conservative).
        """
        if self.published_snapshot is None:
            return True  # Never published, so there are "unpublished" changes
        # Conservative: assume changes exist. A more precise check would
        # compare file_index entries for apps/{slug}/ against the snapshot.
        return True

    @property
    def repo_prefix(self) -> str:
        """Return the repo path prefix for this app, with trailing slash."""
        return f"{self.repo_path.rstrip('/')}/"
