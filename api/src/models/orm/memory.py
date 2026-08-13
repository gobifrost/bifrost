"""Private and organization-ready memory storage models."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base
from src.models.orm.vector_type import Vector

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User


class MemoryStore(Base):
    """A memory boundary owned by a user or, later, an organization."""

    __tablename__ = "memory_stores"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
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

    organization: Mapped["Organization | None"] = relationship("Organization")
    user: Mapped["User | None"] = relationship("User")
    entries: Mapped[list["MemoryEntry"]] = relationship(
        back_populates="store",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "organization_id IS NOT NULL OR user_id IS NOT NULL",
            name="ck_memory_store_has_owner",
        ),
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_memory_store_org_user",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_memory_stores_organization_id", "organization_id"),
        Index("ix_memory_stores_user_id", "user_id"),
    )


class MemoryEntry(Base):
    """One durable memory inside an ownership-scoped store."""

    __tablename__ = "memory_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    store_id: Mapped[UUID] = mapped_column(
        ForeignKey("memory_stores.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    doc_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[list] = mapped_column(Vector(), nullable=False)
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

    store: Mapped[MemoryStore] = relationship(back_populates="entries")

    __table_args__ = (Index("ix_memory_entries_store_id", "store_id"),)
