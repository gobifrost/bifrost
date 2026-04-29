"""
Table and Document ORM models.

Provides a flexible document store for app builder data storage.
Tables are scoped like configs: organization_id = NULL for global, UUID for org-specific.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.applications import Application
    from src.models.orm.organizations import Organization


class Table(Base):
    """Table metadata for document collections.

    Tables are flexible document stores similar to Dataverse/Airtable.
    - organization_id = NULL: Global table (platform-wide)
    - organization_id = UUID: Organization-scoped table
    - application_id = UUID: Optional app association

    The schema field is optional and provides hints for validation/UI,
    but is not enforced at the database level.
    """

    __tablename__ = "tables"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )
    application_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL", onupdate="CASCADE"), default=None
    )
    schema: Mapped[dict | None] = mapped_column(JSONB, default=None)
    access: Mapped[dict | None] = mapped_column(JSONB, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
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
        "Organization", back_populates="tables"
    )
    application: Mapped["Application | None"] = relationship(
        "Application", back_populates="tables"
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="table", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_tables_organization_id", "organization_id"),
        # Unique constraints handled via partial indexes in migration
    )


class Document(Base):
    """Document (row) within a Table.

    Documents store arbitrary JSONB data. The data field is indexed
    with a GIN index for efficient querying.

    The id field is a user-provided string key (like email, employee_id)
    or an auto-generated UUID string if not provided.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=lambda: str(uuid4())
    )
    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tables.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
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
    updated_by: Mapped[str | None] = mapped_column(String(255), default=None)

    # Relationships
    table: Mapped["Table"] = relationship("Table", back_populates="documents")

    __table_args__ = (
        Index("ix_documents_table_id", "table_id"),
        # Unique constraint on (table_id, id) handled in migration
        # GIN index on data handled in migration
    )
