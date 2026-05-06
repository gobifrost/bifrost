"""
KnowledgeStore ORM model.

Represents the vector knowledge store for RAG (Retrieval Augmented Generation).
Uses pgvector for semantic search with org-scoped data and global fallback.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User


class KnowledgeStore(Base):
    """
    Vector knowledge store for semantic search.

    Supports:
    - Namespace-based organization (like folders)
    - Org-scoped data with global fallback (org_id=NULL for global)
    - User-provided keys for upsert/re-indexing
    - Rich metadata for filtering
    - Vector embeddings for semantic search

    Usage:
        # Store with key for easy updates
        await knowledge.store(
            content="...",
            namespace="tickets",
            key="ticket-123",
            metadata={"status": "open"}
        )

        # Search with org->global fallback
        results = await knowledge.search("query", namespace="tickets")
    """

    __tablename__ = "knowledge_store"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Scoping
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Content
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Note: Using 'doc_metadata' as column name because 'metadata' is reserved in SQLAlchemy
    doc_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    # Vector embedding — unconstrained dim so any embedding model works.
    # Search relies on the *currently configured* embedder being the one used
    # at store time (otherwise stored vectors live in a different vector space
    # than the query, and similarity is meaningless even at matching dims).
    # No fixed-dim ANN index because pgvector's HNSW/IVFFlat both require a
    # known dimension; sequential scan is fine at our scale.
    embedding: Mapped[list] = mapped_column(Vector(), nullable=False)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    organization: Mapped["Organization | None"] = relationship(
        "Organization", back_populates="knowledge_entries"
    )
    creator: Mapped["User | None"] = relationship("User")

    __table_args__ = (
        # Unique constraint on namespace + org + key (when key is provided)
        # This enables upsert behavior for documents with keys
        UniqueConstraint(
            "namespace", "organization_id", "key",
            name="uq_knowledge_ns_org_key",
            postgresql_nulls_not_distinct=True,  # Treat NULL org_id as equal for uniqueness
        ),
        # Namespace + org lookup (for listing and searching)
        Index("ix_knowledge_ns_org", "namespace", "organization_id"),
        # Metadata filtering (GIN index for JSONB) - uses column name "metadata" not attribute name
        Index("ix_knowledge_metadata", "metadata", postgresql_using="gin"),
        # Note: Vector index (IVFFlat) is created in migration since it requires
        # special syntax not easily expressed in SQLAlchemy
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeStore(namespace={self.namespace!r}, "
            f"key={self.key!r}, org_id={self.organization_id})>"
        )
