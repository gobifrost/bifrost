"""
Knowledge Repository

Data access layer for the knowledge store (RAG).
Handles vector storage, hybrid search, and namespace management.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select

from src.models.orm import KnowledgeStore
from src.repositories.org_scoped import OrgScopedRepository
from src.services.embeddings import BaseEmbeddingClient
from src.services.knowledge.chunking import reassemble_chunks, split_into_chunks


HYBRID_CANDIDATE_LIMIT = 20
RRF_K = 60
VECTOR_RRF_WEIGHT = 0.5
LEXICAL_RRF_WEIGHT = 0.5

_LEXICAL_TERM_RE = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_LEXICAL_TERMS = 24


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


@dataclass
class _HybridCandidate:
    """One physical chunk participating in reciprocal-rank fusion."""

    row: KnowledgeStore
    rrf_score: float = 0.0
    vector_score: float | None = None
    lexical_score: float | None = None


def _lexical_websearch_query(query: str) -> str:
    """Build a safe OR query for broad full-text candidate retrieval.

    ``websearch_to_tsquery`` handles stemming and stop words. Joining parsed
    word tokens with OR avoids requiring every conversational filler word to
    appear in a chunk, while vector rank fusion keeps broad lexical matches
    from dominating the final result.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for match in _LEXICAL_TERM_RE.finditer(query.casefold()):
        term = match.group(0)
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= _MAX_LEXICAL_TERMS:
            break
    return " OR ".join(terms)


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

    @staticmethod
    def _identity_clauses(
        namespace: str,
        key: str | None,
        organization_id: UUID | None,
    ) -> list:
        """WHERE clauses selecting every chunk row of one logical document.

        NULL-aware on both key and org: under the NULLS NOT DISTINCT unique
        constraint a NULL key (keyless doc) and NULL org (global scope) are
        real identities, not wildcards — a namespace/org pair holds at most
        one keyless document.
        """
        return [
            KnowledgeStore.namespace == namespace,
            KnowledgeStore.key == key
            if key is not None
            else KnowledgeStore.key.is_(None),
            KnowledgeStore.organization_id == organization_id
            if organization_id is not None
            else KnowledgeStore.organization_id.is_(None),
        ]

    @staticmethod
    def _as_document(rows: list[KnowledgeStore]) -> KnowledgeDocument:
        """Collapse one document's chunk rows (in chunk_index order) into a
        KnowledgeDocument carrying the reassembled original content. The
        first chunk row provides the document's public id and audit fields.
        """
        first = rows[0]
        content = (
            first.content
            if len(rows) == 1
            else reassemble_chunks([row.content for row in rows])
        )
        return KnowledgeDocument(
            id=str(first.id),
            namespace=first.namespace,
            content=content,
            metadata=first.doc_metadata,
            organization_id=str(first.organization_id)
            if first.organization_id
            else None,
            key=first.key,
            created_at=first.created_at,
        )

    async def _embed_chunks(
        self, content: str, embedder: BaseEmbeddingClient
    ) -> tuple[list[str], list[list[float]]]:
        """Split content and embed every chunk, validating batch shape."""
        chunks = split_into_chunks(content)
        embeddings = await embedder.embed(chunks)
        if len(embeddings) != len(chunks):
            raise ValueError(
                f"Embedder returned {len(embeddings)} embeddings for {len(chunks)} chunks"
            )
        return chunks, embeddings

    def _build_chunk_rows(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        *,
        namespace: str,
        key: str | None,
        metadata: dict[str, Any] | None,
        organization_id: UUID | None,
        created_by: UUID | None,
        start_index: int = 0,
        chunk_count: int | None = None,
    ) -> list[KnowledgeStore]:
        total_chunk_count = chunk_count if chunk_count is not None else len(chunks)
        return [
            KnowledgeStore(
                namespace=namespace,
                organization_id=organization_id,
                key=key,
                content=chunk,
                doc_metadata=metadata or {},
                embedding=embedding,
                created_by=created_by,
                chunk_index=index,
                chunk_count=total_chunk_count,
            )
            for index, (chunk, embedding) in enumerate(
                zip(chunks, embeddings), start=start_index
            )
        ]

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
        chunks, embeddings = await self._embed_chunks(content, embedder)

        if key is not None:
            await self.session.execute(
                delete(KnowledgeStore).where(
                    *self._identity_clauses(namespace, key, target_org_id)
                )
            )

        rows = self._build_chunk_rows(
            chunks,
            embeddings,
            namespace=namespace,
            key=key,
            metadata=metadata,
            organization_id=target_org_id,
            created_by=created_by,
        )
        self.session.add_all(rows)
        await self.session.flush()
        return [str(row.id) for row in rows]

    async def replace_chunked(
        self,
        doc_id: UUID,
        content: str,
        namespace: str,
        key: str | None,
        current_organization_id: UUID | None,
        organization_id: UUID | None,
        metadata: dict[str, Any] | None,
        created_by: UUID | None,
        created_at: datetime,
        embedder: BaseEmbeddingClient,
    ) -> list[str]:
        """
        Replace one logical document with freshly chunked-and-embedded rows,
        keeping its public identity: the canonical chunk-zero row is updated
        in place while its sibling rows are replaced. Keeping that row alive
        lets the route use it as the common lock for every chunk alias, and
        preserves stored references across edits. Every row carries the
        original audit fields so edits don't reorder created-at-sorted lists
        or change creator attribution. ``updated_at`` resets to now.

        Old rows are deleted by document identity in the *current* scope and
        new rows are written to ``organization_id`` — when the two differ a
        scope change is a move, not a copy. Everything is flushed, never
        committed; the caller's transaction boundary decides. The embed runs
        before the delete, so an embedding failure leaves the document
        completely untouched.

        Returns:
            Inserted row IDs (UUID strings) in chunk_index order;
            the first is always ``str(doc_id)``.
        """
        chunks, embeddings = await self._embed_chunks(content, embedder)

        canonical_result = await self.session.execute(
            select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
        )
        canonical = canonical_result.scalar_one()

        # The canonical row is the stable public identity and serialization
        # lock. Delete only its siblings, then update it in place.
        await self.session.execute(
            delete(KnowledgeStore).where(
                *self._identity_clauses(namespace, key, current_organization_id),
                KnowledgeStore.id != doc_id,
            )
        )
        await self.session.flush()

        sibling_rows = self._build_chunk_rows(
            chunks[1:],
            embeddings[1:],
            namespace=namespace,
            key=key,
            metadata=metadata,
            organization_id=organization_id,
            created_by=created_by,
            start_index=1,
            chunk_count=len(chunks),
        )

        canonical.namespace = namespace
        canonical.organization_id = organization_id
        canonical.key = key
        canonical.content = chunks[0]
        canonical.doc_metadata = metadata or {}
        canonical.embedding = embeddings[0]
        canonical.created_by = created_by
        canonical.created_at = created_at
        canonical.chunk_index = 0
        canonical.chunk_count = len(chunks)

        for row in sibling_rows:
            row.created_at = created_at
        self.session.add_all(sibling_rows)
        await self.session.flush()
        return [str(canonical.id), *(str(row.id) for row in sibling_rows)]

    async def find_document_id(
        self,
        namespace: str,
        key: str | None,
        organization_id: UUID | None,
    ) -> UUID | None:
        """
        Id of the first-chunk row of the document with this exact identity,
        or None. ``chunk_index == 0`` makes this at-most-one row under the
        unique constraint — including keyless documents, whose NULL keys
        collide with each other under NULLS NOT DISTINCT.
        """
        result = await self.session.execute(
            select(KnowledgeStore.id).where(
                *self._identity_clauses(namespace, key, organization_id),
                KnowledgeStore.chunk_index == 0,
            )
        )
        return result.scalar_one_or_none()

    async def lock_document(
        self,
        namespace: str,
        key: str | None,
        organization_id: UUID | None,
    ) -> KnowledgeStore | None:
        """Resolve a logical document to chunk zero and lock that stable row.

        Requests may address any physical chunk id. Resolving first makes
        every alias acquire the same row lock, preventing cross-chunk update
        deadlocks while preserving the canonical public id.
        """
        canonical_id = await self.find_document_id(namespace, key, organization_id)
        if canonical_id is None:
            return None

        result = await self.session.execute(
            select(KnowledgeStore)
            .where(KnowledgeStore.id == canonical_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def delete_document(
        self,
        namespace: str,
        key: str | None,
        organization_id: UUID | None,
    ) -> None:
        """Delete every chunk row of the document with this exact identity
        (NULL-aware on key and org — no defaulting to ``self.org_id``)."""
        await self.session.execute(
            delete(KnowledgeStore).where(
                *self._identity_clauses(namespace, key, organization_id)
            )
        )
        await self.session.flush()

    async def search(
        self,
        query_embedding: list[float],
        namespace: str | list[str],
        query_text: str | None = None,
        organization_id: UUID | None = None,
        limit: int = 5,
        min_score: float | None = None,
        metadata_filter: dict[str, Any] | None = None,
        fallback: bool = True,
        group_by_key: bool = True,
    ) -> list[KnowledgeDocument]:
        """
        Search for relevant chunks.

        When ``query_text`` is provided, retrieve a broad candidate set from
        both vector similarity and PostgreSQL full-text search, then combine
        their ranks with reciprocal-rank fusion (RRF). Without query text the
        method preserves the original vector-only behavior for SDK callers
        that already supply a precomputed embedding.

        Args:
            query_embedding: Query vector
            namespace: Namespace(s) to search
            query_text: Original query text. Enables hybrid lexical/vector search.
            organization_id: Organization scope. Defaults to self.org_id.
            limit: Maximum results
            min_score: Minimum vector similarity or normalized fused score (0-1)
            metadata_filter: Filter by metadata fields
            fallback: If True, also search global scope
            group_by_key: If True, return at most one chunk per keyed document

        Returns:
            List of KnowledgeDocument sorted by relevance
        """
        target_org_id = organization_id if organization_id is not None else self.org_id
        namespaces = [namespace] if isinstance(namespace, str) else namespace

        filters = [KnowledgeStore.namespace.in_(namespaces)]

        # Organization scoping with optional fallback.
        if target_org_id and fallback:
            filters.append(
                (KnowledgeStore.organization_id == target_org_id)
                | (KnowledgeStore.organization_id.is_(None))
            )
        elif target_org_id:
            filters.append(KnowledgeStore.organization_id == target_org_id)
        else:
            filters.append(KnowledgeStore.organization_id.is_(None))

        if metadata_filter:
            filters.extend(
                KnowledgeStore.doc_metadata.contains({key: value})
                for key, value in metadata_filter.items()
            )

        # Cosine distance is lower-is-better; expose similarity as 0..1.
        distance_expr = KnowledgeStore.embedding.cosine_distance(query_embedding)
        vector_score_expr = (1 - distance_expr).label("vector_score")
        candidate_limit = max(HYBRID_CANDIDATE_LIMIT, limit * 4)
        vector_stmt = (
            select(KnowledgeStore, vector_score_expr)
            .where(*filters)
            .order_by(vector_score_expr.desc())
            .limit(candidate_limit)
        )
        vector_rows = (await self.session.execute(vector_stmt)).all()

        lexical_query = _lexical_websearch_query(query_text or "")
        if not lexical_query:
            ranked_rows = [
                (row[0], float(row[1]))
                for row in vector_rows
                if min_score is None or row[1] >= min_score
            ]
        else:
            ts_query = func.websearch_to_tsquery("english", lexical_query)
            lexical_score_expr = func.ts_rank_cd(
                KnowledgeStore.search_tsv,
                ts_query,
                32,
            ).label("lexical_score")
            lexical_stmt = (
                select(KnowledgeStore, lexical_score_expr)
                .where(
                    *filters,
                    KnowledgeStore.search_tsv.op("@@")(ts_query),
                )
                .order_by(lexical_score_expr.desc())
                .limit(candidate_limit)
            )
            lexical_rows = (await self.session.execute(lexical_stmt)).all()

            candidates: dict[UUID, _HybridCandidate] = {}
            for rank, row in enumerate(vector_rows, start=1):
                chunk = row[0]
                candidate = candidates.setdefault(
                    chunk.id,
                    _HybridCandidate(row=chunk),
                )
                candidate.vector_score = float(row[1])
                candidate.rrf_score += VECTOR_RRF_WEIGHT / (RRF_K + rank)

            for rank, row in enumerate(lexical_rows, start=1):
                chunk = row[0]
                candidate = candidates.setdefault(
                    chunk.id,
                    _HybridCandidate(row=chunk),
                )
                candidate.lexical_score = float(row[1])
                candidate.rrf_score += LEXICAL_RRF_WEIGHT / (RRF_K + rank)

            # Normalize against the theoretical score for rank 1 in both
            # retrievers so the public score remains an intuitive 0..1 value.
            max_rrf_score = (
                VECTOR_RRF_WEIGHT + LEXICAL_RRF_WEIGHT
            ) / (RRF_K + 1)
            ranked_candidates = sorted(
                candidates.values(),
                key=lambda item: (
                    item.rrf_score,
                    item.lexical_score if item.lexical_score is not None else -1.0,
                    item.vector_score if item.vector_score is not None else -1.0,
                    str(item.row.id),
                ),
                reverse=True,
            )
            ranked_rows = [
                (candidate.row, candidate.rrf_score / max_rrf_score)
                for candidate in ranked_candidates
                if min_score is None
                or candidate.rrf_score / max_rrf_score >= min_score
            ]

        documents: list[KnowledgeDocument] = []
        seen_keys: set[tuple[str, str | None, str]] = set()
        for doc, score in ranked_rows:
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
                    score=score,
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
        stmt = (
            select(KnowledgeStore)
            .where(
                KnowledgeStore.key == key,
                KnowledgeStore.namespace == namespace,
            )
            .order_by(KnowledgeStore.chunk_index)
        )

        if target_org_id:
            stmt = stmt.where(KnowledgeStore.organization_id == target_org_id)
        else:
            stmt = stmt.where(KnowledgeStore.organization_id.is_(None))

        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        if not rows:
            return None

        # A keyed doc may span multiple chunk rows — reassemble the full
        # content (the old single-row read raised MultipleResultsFound here).
        return self._as_document(rows)

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
        """Get a full logical document by any of its chunk-row UUIDs.

        Multi-chunk documents are reassembled into their original content;
        the returned id/created_at are the first chunk row's (the document's
        stable public identity).
        """
        stmt = select(KnowledgeStore).where(KnowledgeStore.id == doc_id)
        result = await self.session.execute(stmt)
        doc = result.scalar_one_or_none()

        if not doc:
            return None

        rows = [doc]
        if doc.chunk_count > 1:
            rows = list(
                (
                    await self.session.execute(
                        select(KnowledgeStore)
                        .where(
                            *self._identity_clauses(
                                doc.namespace, doc.key, doc.organization_id
                            )
                        )
                        .order_by(KnowledgeStore.chunk_index)
                    )
                )
                .scalars()
                .all()
            )
        return self._as_document(rows)

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
