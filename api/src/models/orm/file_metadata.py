"""File metadata and policy prefix ORM models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class FileMetadata(Base):
    """Metadata for files stored through the Bifrost file backends."""

    __tablename__ = "file_metadata"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(2000), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(2500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    origin_solution_slug: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None
    )
    origin_solution_id: Mapped[UUID | None] = mapped_column(nullable=True, default=None)
    orphaned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    __table_args__ = (
        Index("ix_file_metadata_organization_id", "organization_id"),
        Index("ix_file_metadata_lookup", "organization_id", "location", "path"),
        # H4: existing org/global uniques now exclude solution rows
        Index(
            "uq_file_metadata_org_location_path",
            "organization_id",
            "location",
            "path",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL AND solution_id IS NULL"),
        ),
        Index(
            "uq_file_metadata_global_location_path",
            "location",
            "path",
            unique=True,
            postgresql_where=text("organization_id IS NULL AND solution_id IS NULL"),
        ),
        # Solution tier: rows belonging to an installed solution instance
        Index(
            "uq_file_metadata_solution_location_path",
            "solution_id",
            "location",
            "path",
            unique=True,
            postgresql_where=text("solution_id IS NOT NULL"),
        ),
        Index("ix_file_metadata_solution_id", "solution_id"),
    )


class FilePolicy(Base):
    """Policy document attached to a location/path prefix."""

    __tablename__ = "file_policies"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(2000), nullable=False)
    policies: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    origin_solution_slug: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None
    )
    origin_solution_id: Mapped[UUID | None] = mapped_column(nullable=True, default=None)
    orphaned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    __table_args__ = (
        Index("ix_file_policies_organization_id", "organization_id"),
        Index("ix_file_policies_lookup", "organization_id", "location", "path"),
        # H4: existing org/global uniques now exclude solution rows
        Index(
            "uq_file_policies_org_location_path",
            "organization_id",
            "location",
            "path",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL AND solution_id IS NULL"),
        ),
        Index(
            "uq_file_policies_global_location_path",
            "location",
            "path",
            unique=True,
            postgresql_where=text("organization_id IS NULL AND solution_id IS NULL"),
        ),
        # Solution tier: rows belonging to an installed solution instance
        Index(
            "uq_file_policies_solution_location_path",
            "solution_id",
            "location",
            "path",
            unique=True,
            postgresql_where=text("solution_id IS NOT NULL"),
        ),
        Index("ix_file_policies_solution_id", "solution_id"),
    )
