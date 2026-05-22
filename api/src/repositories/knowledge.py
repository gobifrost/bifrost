"""
Knowledge Repository

Data access layer for the knowledge store (RAG).
Handles vector storage, semantic search, and namespace management.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select

from src.models.orm import KnowledgeStore
from src.repositories.org_scoped import OrgScopedRepository
from src.services.embeddings import BaseEmbeddingClient
from src.services.knowledge.chunking import split_into_chunks


@dataclass
class KnowledgeDocument:
    """Document returned from knowledge store."""

    id: str
    namespace: str
    content: str
    metadata: dict[str, Any]
    score: float | None = None
    organization_id: str | None = None
    key: str | None = None
    created_at: datetime | None = None


@dataclass
class NamespaceInfo:
    """Information about a namespace."""

    namespace: str
    scopes: dict[str, int]  # {"global": count, "org": count, "total": count}


class KnowledgeRepository(OrgScopedRepository[KnowledgeStore]):
    """
    Repository for knowledge store operations.

    Supports:
    - Upsert by key for easy re-indexing
    - Org-scoped storage with global fallback
    - Vector similarity search
    - Metadata filtering

    Note: This repository has custom scoping logic for its methods since
    the organization_id on documents represents where data is stored,
    not access control. Pass org_id to constructor for consistency with
    OrgScopedRepository pattern; methods use self.org_id as default.
    """

    model = KnowledgeStore
    role_table = None  # No RBAC - SDK-only access

    async def store_chunked(
        self,
        content: str,
        namespace: str = "default",
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
        organization_id: UUID | None = None,
        created_by: UUID | None = None,
        embedder: BaseEmbeddingClient | None = None,
    ) -> list[str]:
        """
        Store a document as one or more embedded chunks.

        If key is provided, existing rows for that key are atomically replaced
        so upsert semantics are preserved across any number of chunks.

        Args:
            content: Text content
            namespace: Namespace for organization
            key: Optional user-provided key for upserts
            metadata: Optional metadata dict
            organization_id: Organization scope (None for global). Defaults to self.org_id.
            created_by: User who created the document
            embedder: Embedding client used to embed every chunk

        Returns:
            Inserted document IDs (UUID strings), in chunk_index order.
        """
        if embedder is None:
            raise ValueError("store_chunked requires an embedder")

        target_org_id = organization_id if organization_id is not None else self.org_id
        chunks = split_into_chunks(content)
        embeddings = await embedder.embed(chunks)

        if len(embeddings) != len(chunks):
            raise ValueError(
                f"Embedder returned {len(embeddings)} embeddings for {len(chunks)} chunks"
            )

        if key is not None:
            stmt = delete(KnowledgeStore).where(
                KnowledgeStore.key == key,
                KnowledgeStore.namespace == namespace,
            )
            if target_org_id is not None:
                stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
            else:
                stmt = stmt.where(KnowledgeStore.organization_id.is_(None))
            await self.session.execute(stmt)

        chunk_count = len(chunks)
        rows = [
            KnowledgeStore(
                namespace=namespace,
                organization_id=target_org_id,
                key=key,
                content=chunk,
                doc_metadata=metadata or {},
                embedding=embedding,
                created_by=created_by,
                chunk_index=index,
                chunk_count=chunk_count,
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return [str(row.id) for row in rows]

    async def search(
        self,
        query_embedding: list[float],
        namespace: str | list[str],
        organization_id: UUID | None = None,
        limit: int = 5,
        min_score: float | None = None,
        metadata_filter: dict[str, Any] | None = None,
        fallback: bool = True,
        group_by_key: bool = True,
    ) -> list[KnowledgeDocument]:
        """
        Search for similar documents using vector similarity.

        Args:
            query_embedding: Query vector
            namespace: Namespace(s) to search
            organization_id: Organization scope. Defaults to self.org_id.
            limit: Maximum results
            min_score: Minimum similarity score (0-1)
            metadata_filter: Filter by metadata fields
            fallback: If True, also search global scope
            group_by_key: If True, return at most one chunk per keyed document

        Returns:
            List of KnowledgeDocument sorted by similarity
        """
        # Use self.org_id as default if not explicitly provided
        target_org_id = organization_id if organization_id is not None else self.org_id
        namespaces = [namespace] if isinstance(namespace, str) else namespace

        # Build the query
        # We use cosine distance (1 - cosine_similarity), so lower is better
        # Convert to similarity score: 1 - distance
        distance_expr = KnowledgeStore.embedding.cosine_distance(query_embedding)
        score_expr = (1 - distance_expr).label("score")

        stmt = select(
            KnowledgeStore,
            score_expr,
        ).where(
            KnowledgeStore.namespace.in_(namespaces)
        )

        # Organization scoping with optional fallback
        if target_org_id and fallback:
            # Search both org and global
            stmt = stmt.where(
                (KnowledgeStore.organization_id == target_org_id) |
                (KnowledgeStore.organization_id.is_(None))
            )
        elif target_org_id:
            # Only org scope
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            # Only global scope
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        # Metadata filtering using JSONB containment
        if metadata_filter:
            for key, value in metadata_filter.items():
                # Use @> containment operator
                stmt = stmt.where(
                    KnowledgeStore.doc_metadata.contains({key: value})
                )

        stmt = stmt.order_by(score_expr.desc())
        raw_limit = limit * 4 if group_by_key else limit
        stmt = stmt.limit(raw_limit)

        result = await self.session.execute(stmt)
        rows = result.all()

        documents: list[KnowledgeDocument] = []
        seen_keys: set[tuple[str, str | None, str]] = set()
        for row in rows:
            doc = row[0]
            score = row[1]

            # Filter by min_score if specified
            if min_score is not None and score < min_score:
                continue

            if group_by_key and doc.key is not None:
                dedup_key = (
                    doc.namespace,
                    str(doc.organization_id) if doc.organization_id else None,
                    doc.key,
                )
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

            documents.append(
                KnowledgeDocument(
                    id=str(doc.id),
                    namespace=doc.namespace,
                    content=doc.content,
                    metadata=doc.doc_metadata,
                    score=float(score),
                    organization_id=str(doc.organization_id) if doc.organization_id else None,
                    key=doc.key,
                    created_at=doc.created_at,
                )
            )
            if len(documents) >= limit:
                break

        return documents

    async def delete_by_key(
        self,
        key: str,
        namespace: str,
        organization_id: UUID | None = None,
    ) -> bool:
        """
        Delete a document by key.

        Args:
            key: Document key
            namespace: Namespace
            organization_id: Organization scope (None for global). Defaults to self.org_id.

        Returns:
            True if deleted, False if not found
        """
        # Use self.org_id as default if not explicitly provided
        target_org_id = organization_id if organization_id is not None else self.org_id
        stmt = delete(KnowledgeStore).where(
            KnowledgeStore.key == key,
            KnowledgeStore.namespace == namespace,
        )

        if target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def delete_namespace(
        self,
        namespace: str,
        organization_id: UUID | None = None,
    ) -> int:
        """
        Delete all documents in a namespace.

        Args:
            namespace: Namespace to delete
            organization_id: Organization scope (None for global). Defaults to self.org_id.

        Returns:
            Number of documents deleted
        """
        # Use self.org_id as default if not explicitly provided
        target_org_id = organization_id if organization_id is not None else self.org_id
        stmt = delete(KnowledgeStore).where(
            KnowledgeStore.namespace == namespace,
        )

        if target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        result = await self.session.execute(stmt)
        return result.rowcount

    async def list_namespaces(
        self,
        organization_id: UUID | None = None,
        include_global: bool = True,
    ) -> list[NamespaceInfo]:
        """
        List all namespaces with document counts per scope.

        Args:
            organization_id: If provided, include org-scoped counts. Defaults to self.org_id.
            include_global: If True, include global namespaces

        Returns:
            List of NamespaceInfo with scope counts
        """
        # Use self.org_id as default if not explicitly provided
        target_org_id = organization_id if organization_id is not None else self.org_id
        # This is a bit complex - we need to get counts grouped by namespace and org_id
        stmt = select(
            KnowledgeStore.namespace,
            KnowledgeStore.organization_id,
            func.count(KnowledgeStore.id).label("count"),
        ).group_by(
            KnowledgeStore.namespace,
            KnowledgeStore.organization_id,
        )

        # Filter by what we want to see
        if target_org_id and include_global:
            stmt = stmt.where(
                (KnowledgeStore.organization_id == target_org_id) |
                (KnowledgeStore.organization_id.is_(None))
            )
        elif target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        elif include_global:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        result = await self.session.execute(stmt)
        rows = result.all()

        # Aggregate by namespace
        namespace_data: dict[str, dict[str, int]] = {}
        for row in rows:
            ns = row[0]
            org_id = row[1]
            count = row[2]

            if ns not in namespace_data:
                namespace_data[ns] = {"global": 0, "org": 0, "total": 0}

            if org_id is None:
                namespace_data[ns]["global"] = count
            else:
                namespace_data[ns]["org"] = count

            namespace_data[ns]["total"] += count

        return [
            NamespaceInfo(namespace=ns, scopes=scopes)
            for ns, scopes in sorted(namespace_data.items())
        ]

    async def get_by_key(
        self,
        key: str,
        namespace: str,
        organization_id: UUID | None = None,
    ) -> KnowledgeDocument | None:
        """
        Get a document by its key.

        Args:
            key: Document key
            namespace: Namespace
            organization_id: Organization scope (None for global). Defaults to self.org_id.

        Returns:
            KnowledgeDocument or None if not found
        """
        # Use self.org_id as default if not explicitly provided
        target_org_id = organization_id if organization_id is not None else self.org_id
        stmt = select(KnowledgeStore).where(
            KnowledgeStore.key == key,
            KnowledgeStore.namespace == namespace,
        )

        if target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        result = await self.session.execute(stmt)
        doc = result.scalar_one_or_none()

        if not doc:
            return None

        return KnowledgeDocument(
            id=str(doc.id),
            namespace=doc.namespace,
            content=doc.content,
            metadata=doc.doc_metadata,
            organization_id=str(doc.organization_id) if doc.organization_id else None,
            key=doc.key,
            created_at=doc.created_at,
        )

    async def get_all_by_namespace(
        self,
        namespace: str,
        organization_id: UUID | None = None,
    ) -> dict[str, KnowledgeDocument]:
        """
        Get all documents in a namespace, keyed by their key field.

        Used for batch operations like checking which documents need re-indexing.

        Args:
            namespace: Namespace to query
            organization_id: Organization scope (None for global). Defaults to self.org_id.

        Returns:
            Dict mapping key -> KnowledgeDocument (only docs with keys)
        """
        # Use self.org_id as default if not explicitly provided
        target_org_id = organization_id if organization_id is not None else self.org_id
        stmt = select(KnowledgeStore).where(
            KnowledgeStore.namespace == namespace,
            KnowledgeStore.key.isnot(None),
        )

        if target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        result = await self.session.execute(stmt)
        docs = result.scalars().all()

        return {
            doc.key: KnowledgeDocument(
                id=str(doc.id),
                namespace=doc.namespace,
                content=doc.content,
                metadata=doc.doc_metadata,
                organization_id=str(doc.organization_id) if doc.organization_id else None,
                key=doc.key,
                created_at=doc.created_at,
            )
            for doc in docs
            if doc.key is not None
        }

    async def get_by_id(
        self,
        doc_id: UUID,
    ) -> KnowledgeDocument | None:
        """Get a document by its UUID."""
        stmt = select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
        result = await self.session.execute(stmt)
        doc = result.scalar_one_or_none()

        if not doc:
            return None

        return KnowledgeDocument(
            id=str(doc.id),
            namespace=doc.namespace,
            content=doc.content,
            metadata=doc.doc_metadata,
            organization_id=str(doc.organization_id) if doc.organization_id else None,
            key=doc.key,
            created_at=doc.created_at,
        )

    async def list_documents_by_namespace(
        self,
        namespace: str | None = None,
        organization_id: UUID | None = None,
        include_global: bool = True,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> list[KnowledgeDocument]:
        """
        List documents with optional namespace and org scoping.

        Args:
            namespace: Namespace to filter by (None for all namespaces)
            organization_id: Organization scope. Defaults to self.org_id.
            include_global: If True, also include global docs
            limit: Max results
            offset: Pagination offset
            search: Optional text to filter by key or content (case-insensitive)

        Returns:
            List of KnowledgeDocument
        """
        target_org_id = organization_id if organization_id is not None else self.org_id
        stmt = select(KnowledgeStore)

        if namespace:
            stmt = stmt.where(KnowledgeStore.namespace == namespace)

        if search:
            stmt = stmt.where(
                KnowledgeStore.content.ilike(f"%{search}%")
                | KnowledgeStore.key.ilike(f"%{search}%")
            )

        if target_org_id and include_global:
            stmt = stmt.where(
                (KnowledgeStore.organization_id == target_org_id) |
                (KnowledgeStore.organization_id.is_(None))
            )
        elif target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        stmt = stmt.order_by(KnowledgeStore.created_at.desc())
        stmt = stmt.offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        docs = result.scalars().all()

        return [
            KnowledgeDocument(
                id=str(doc.id),
                namespace=doc.namespace,
                content=doc.content,
                metadata=doc.doc_metadata,
                organization_id=str(doc.organization_id) if doc.organization_id else None,
                key=doc.key,
                created_at=doc.created_at,
            )
            for doc in docs
        ]

    async def list_all_namespaces(self) -> list[NamespaceInfo]:
        """
        List ALL namespaces across all orgs (superuser/unfiltered view).

        Returns:
            List of NamespaceInfo with scope counts
        """
        stmt = select(
            KnowledgeStore.namespace,
            KnowledgeStore.organization_id,
            func.count(KnowledgeStore.id).label("count"),
        ).group_by(
            KnowledgeStore.namespace,
            KnowledgeStore.organization_id,
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        namespace_data: dict[str, dict[str, int]] = {}
        for row in rows:
            ns = row[0]
            org_id = row[1]
            count = row[2]

            if ns not in namespace_data:
                namespace_data[ns] = {"global": 0, "org": 0, "total": 0}

            if org_id is None:
                namespace_data[ns]["global"] = count
            else:
                namespace_data[ns]["org"] = count

            namespace_data[ns]["total"] += count

        return [
            NamespaceInfo(namespace=ns, scopes=scopes)
            for ns, scopes in sorted(namespace_data.items())
        ]

    async def delete_orphaned_docs(
        self,
        namespace: str,
        organization_id: UUID | None = None,
        valid_keys: set[str] | None = None,
    ) -> int:
        """
        Delete documents not in the valid_keys set.

        Used to clean up stale documents after re-indexing. Any document
        in the namespace that is NOT in valid_keys will be deleted.

        Args:
            namespace: Namespace to clean up
            organization_id: Organization scope (None for global). Defaults to self.org_id.
            valid_keys: Set of keys that should be kept

        Returns:
            Number of documents deleted
        """
        if not valid_keys:
            # Safety: don't delete everything if valid_keys is empty
            return 0

        # Use self.org_id as default if not explicitly provided
        target_org_id = organization_id if organization_id is not None else self.org_id
        stmt = delete(KnowledgeStore).where(
            KnowledgeStore.namespace == namespace,
            KnowledgeStore.key.notin_(valid_keys),
        )

        if target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        result = await self.session.execute(stmt)
        return result.rowcount
