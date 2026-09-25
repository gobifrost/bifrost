"""Shared business service for SDK knowledge operations.

Single implementation used by the HTTP handlers serving external SDK
callers (``POST/GET /api/sdk/knowledge/*`` in ``api/src/routers/cli.py``)
and — in a later stage — the engine-local dispatcher serving workflow
children through the parent-side local transport. There is no local
transport in this stage.

All inputs are already-authoritative scalars: the HTTP edge denies direct
external principals (``_deny_external_knowledge``) and resolves scope
through ``_resolve_sdk_org_id`` before calling in. A future local caller
passes the same resolved org UUID from parent-owned execution metadata.

All failures raise :class:`SDKKnowledgeError` (transport-neutral); the
HTTP adapter maps them to ``HTTPException`` preserving the exact
historical status and detail. The 404 on a repository miss is raised here
so the future dispatcher can send the same status in its response frame
(the Python facade maps that 404 to ``None``).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe

import src.repositories.knowledge as knowledge_repo_module
import src.services.embeddings.factory as embeddings_factory_module

logger = logging.getLogger(__name__)


class SDKKnowledgeError(Exception):
    """SDK knowledge failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the local dispatcher (``ok: false`` frames) can map the same
    failure to their own transport.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _search_result_dict(doc: Any) -> dict[str, Any]:
    """JSON-serializable ``CLIKnowledgeDocumentResponse`` shape for search."""
    return {
        "id": doc.id,
        "namespace": doc.namespace,
        "content": doc.content,
        "metadata": doc.metadata,
        "score": doc.score,
        "organization_id": doc.organization_id,
        "key": doc.key,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def _stored_result_dict(doc: Any) -> dict[str, Any]:
    """JSON-serializable ``CLIKnowledgeDocumentResponse`` shape for get."""
    return {
        "id": doc.id,
        "namespace": doc.namespace,
        "content": doc.content,
        "metadata": doc.metadata,
        "organization_id": doc.organization_id,
        "key": doc.key,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


async def store_knowledge_document(
    session: AsyncSession,
    *,
    content: str,
    namespace: str = "default",
    key: str | None = None,
    metadata: dict[str, Any] | None = None,
    org_id: UUID | None,
    created_by: UUID | None,
) -> dict[str, Any]:
    """Store one document: chunk, embed, upsert, commit.

    Args:
        session: Short-lived parent/HTTP database session.
        content: Text content to store and embed.
        namespace: Namespace for organization.
        key: Optional user-provided key for upserts.
        metadata: Optional metadata dict.
        org_id: Already-resolved effective scope (None for global).
        created_by: Parent-derived actor identity for audit.

    Returns:
        ``{"id": ...}`` carrying the first physical chunk ID.

    Raises:
        SDKKnowledgeError: 503 when embedding is unconfigured, 500
            otherwise. Details match the historical handler.
    """
    try:
        # NOTE: attribute access at call time (not a top-level
        # from-import) keeps both seams patchable for every entry point.
        embedder = await embeddings_factory_module.get_embedding_client(session)
        repo = knowledge_repo_module.KnowledgeRepository(session, org_id=org_id)
        doc_ids = await repo.store_chunked(
            content=content,
            namespace=namespace,
            key=key,
            metadata=metadata,
            created_by=created_by,
            embedder=embedder,
        )
        doc_id = doc_ids[0]

        await session.commit()

        logger.info(
            f"CLI knowledge store: namespace={log_safe(namespace)}, "
            f"key={log_safe(key)}, doc_id={doc_id}"
        )

        return {"id": doc_id}
    except ValueError as e:
        await session.rollback()
        raise SDKKnowledgeError(503, str(e)) from None
    except Exception as e:
        await session.rollback()
        logger.error(f"CLI knowledge store failed: {log_safe(e)}")
        raise SDKKnowledgeError(500, f"Knowledge store failed: {str(e)}") from None


async def store_many_knowledge_documents(
    session: AsyncSession,
    *,
    documents: list[dict[str, Any]],
    namespace: str = "default",
    org_id: UUID | None,
    created_by: UUID | None,
) -> dict[str, Any]:
    """Store many documents with one embedder, one repository, one commit.

    Documents are processed sequentially; the single commit lands after
    every document is flushed, so a failure before that commit rolls back
    all prior flushed rows. A document missing ``content`` fails with the
    historical generic 500 (no new DTO validation in this stage).

    Args:
        session: Short-lived parent/HTTP database session.
        documents: Document dicts with ``content`` plus optional ``key``
            and ``metadata``.
        namespace: Namespace shared by all documents.
        org_id: Already-resolved effective scope (None for global).
        created_by: Parent-derived actor identity for audit.

    Returns:
        ``{"ids": [...]}`` with one first-chunk ID per document.

    Raises:
        SDKKnowledgeError: 503 when embedding is unconfigured, 500
            otherwise. Details match the historical handler.
    """
    try:
        # One embedder and one repository for all documents — matches the
        # historical handler exactly.
        embedder = await embeddings_factory_module.get_embedding_client(session)
        repo = knowledge_repo_module.KnowledgeRepository(session, org_id=org_id)
        doc_ids = []
        for doc in documents:
            inserted_ids = await repo.store_chunked(
                content=doc["content"],
                namespace=namespace,
                key=doc.get("key"),
                metadata=doc.get("metadata"),
                created_by=created_by,
                embedder=embedder,
            )
            doc_ids.append(inserted_ids[0])

        await session.commit()

        logger.info(
            f"CLI knowledge store-many: namespace={log_safe(namespace)}, "
            f"count={len(doc_ids)}"
        )

        return {"ids": doc_ids}
    except ValueError as e:
        await session.rollback()
        raise SDKKnowledgeError(503, str(e)) from None
    except Exception as e:
        await session.rollback()
        logger.error(f"CLI knowledge store-many failed: {log_safe(e)}")
        raise SDKKnowledgeError(500, f"Knowledge store failed: {str(e)}") from None


async def search_knowledge_documents(
    session: AsyncSession,
    *,
    query: str,
    namespace: list[str],
    limit: int = 5,
    min_score: float | None = None,
    metadata_filter: dict[str, Any] | None = None,
    fallback: bool = True,
    org_id: UUID | None,
) -> list[dict[str, Any]]:
    """Hybrid-search knowledge documents (no commit).

    The query is embedded exactly once; the repository fuses lexical and
    vector rankings.

    Args:
        session: Short-lived parent/HTTP database session.
        query: Search query text (embedded once here).
        namespace: Namespace(s) to search.
        limit: Maximum results.
        min_score: Minimum similarity score (0-1).
        metadata_filter: Filter by metadata fields.
        fallback: If True, also search global scope.
        org_id: Already-resolved effective scope (None for global).

    Returns:
        ``CLIKnowledgeDocumentResponse`` shapes as plain dicts (the
        caller builds the DTOs).

    Raises:
        SDKKnowledgeError: 503 when embedding is unconfigured, 500
            otherwise. Details match the historical handler.
    """
    try:
        embedder = await embeddings_factory_module.get_embedding_client(session)
        query_embedding = await embedder.embed_single(query)

        repo = knowledge_repo_module.KnowledgeRepository(session, org_id=org_id)
        results = await repo.search(
            query_embedding=query_embedding,
            namespace=namespace,
            query_text=query,
            limit=limit,
            min_score=min_score,
            metadata_filter=metadata_filter,
            fallback=fallback,
        )

        logger.info(
            f"CLI knowledge search: query={log_safe(query[:50])}..., "
            f"results={len(results)}"
        )

        return [_search_result_dict(doc) for doc in results]
    except ValueError as e:
        raise SDKKnowledgeError(503, str(e)) from None
    except SDKKnowledgeError:
        raise
    except Exception as e:
        logger.error(f"CLI knowledge search failed: {log_safe(e)}")
        raise SDKKnowledgeError(500, f"Knowledge search failed: {str(e)}") from None


async def delete_knowledge_document(
    session: AsyncSession,
    *,
    key: str,
    namespace: str = "default",
    org_id: UUID | None,
) -> dict[str, Any]:
    """Delete one document by key in the exact resolved scope.

    Args:
        session: Short-lived parent/HTTP database session.
        key: Document key.
        namespace: Namespace.
        org_id: Already-resolved effective scope (None for global).

    Returns:
        ``{"deleted": bool}``.

    Raises:
        SDKKnowledgeError: 500 on failure. Details match the historical
            handler.
    """
    try:
        repo = knowledge_repo_module.KnowledgeRepository(session, org_id=org_id)
        deleted = await repo.delete_by_key(key=key, namespace=namespace)

        await session.commit()

        logger.info(
            f"CLI knowledge delete: namespace={log_safe(namespace)}, "
            f"key={log_safe(key)}, deleted={deleted}"
        )

        return {"deleted": deleted}
    except Exception as e:
        await session.rollback()
        logger.error(f"CLI knowledge delete failed: {log_safe(e)}")
        raise SDKKnowledgeError(500, f"Knowledge delete failed: {str(e)}") from None


async def delete_knowledge_namespace(
    session: AsyncSession,
    *,
    namespace: str,
    org_id: UUID | None,
) -> dict[str, Any]:
    """Delete every document in a namespace in the exact resolved scope.

    Args:
        session: Short-lived parent/HTTP database session.
        namespace: Namespace to delete.
        org_id: Already-resolved effective scope (None for global).

    Returns:
        ``{"deleted_count": int}``.

    Raises:
        SDKKnowledgeError: 500 on failure. Details match the historical
            handler.
    """
    try:
        repo = knowledge_repo_module.KnowledgeRepository(session, org_id=org_id)
        deleted_count = await repo.delete_namespace(namespace=namespace)

        await session.commit()

        logger.info(
            f"CLI knowledge delete namespace: namespace={log_safe(namespace)}, "
            f"deleted_count={deleted_count}"
        )

        return {"deleted_count": deleted_count}
    except Exception as e:
        await session.rollback()
        logger.error(f"CLI knowledge delete namespace failed: {log_safe(e)}")
        raise SDKKnowledgeError(
            500, f"Knowledge delete namespace failed: {str(e)}"
        ) from None


async def list_knowledge_namespaces(
    session: AsyncSession,
    *,
    org_id: UUID | None,
    include_global: bool = True,
) -> list[dict[str, Any]]:
    """List namespaces with document counts (no commit).

    Args:
        session: Short-lived parent/HTTP database session.
        org_id: Already-resolved effective scope (None for global).
        include_global: If True, include global namespaces.

    Returns:
        ``CLIKnowledgeNamespaceInfo`` shapes as plain dicts (the caller
        builds the DTOs).

    Raises:
        SDKKnowledgeError: 500 on failure. Details match the historical
            handler.
    """
    try:
        repo = knowledge_repo_module.KnowledgeRepository(session, org_id=org_id)
        results = await repo.list_namespaces(include_global=include_global)

        return [
            {"namespace": ns.namespace, "scopes": ns.scopes} for ns in results
        ]
    except Exception as e:
        logger.error(f"CLI knowledge list namespaces failed: {log_safe(e)}")
        raise SDKKnowledgeError(
            500, f"Knowledge list namespaces failed: {str(e)}"
        ) from None


async def get_knowledge_document(
    session: AsyncSession,
    *,
    key: str,
    namespace: str = "default",
    org_id: UUID | None,
) -> dict[str, Any]:
    """Get one document by key with full-content reassembly (no commit).

    Args:
        session: Short-lived parent/HTTP database session.
        key: Document key.
        namespace: Namespace.
        org_id: Already-resolved effective scope (None for global).

    Returns:
        The ``CLIKnowledgeDocumentResponse`` shape as a plain dict (the
        caller builds the DTO).

    Raises:
        SDKKnowledgeError: 404 when the repository misses (the HTTP
            handler surfaces this as ``HTTPException`` 404; the Python
            facade maps that 404 to ``None``), 500 otherwise. Details
            match the historical handler.
    """
    try:
        repo = knowledge_repo_module.KnowledgeRepository(session, org_id=org_id)
        result = await repo.get_by_key(key=key, namespace=namespace)

        if not result:
            raise SDKKnowledgeError(404, "Document not found")

        return _stored_result_dict(result)
    except SDKKnowledgeError:
        raise
    except Exception as e:
        logger.error(f"CLI knowledge get failed: {log_safe(e)}")
        raise SDKKnowledgeError(500, f"Knowledge get failed: {str(e)}") from None


__all__ = [
    "SDKKnowledgeError",
    "delete_knowledge_document",
    "delete_knowledge_namespace",
    "get_knowledge_document",
    "list_knowledge_namespaces",
    "search_knowledge_documents",
    "store_knowledge_document",
    "store_many_knowledge_documents",
]
