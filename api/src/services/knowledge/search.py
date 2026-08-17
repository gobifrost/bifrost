"""Canonical knowledge-search service shared by REST and agent runtimes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.repositories.knowledge import KnowledgeDocument


async def search_knowledge_documents(
    session: AsyncSession,
    *,
    query: str,
    namespaces: str | list[str],
    organization_id: UUID | None,
    limit: int,
    min_score: float | None = None,
    metadata_filter: dict[str, Any] | None = None,
    fallback: bool = True,
) -> list[KnowledgeDocument]:
    """Embed ``query`` and execute one organization-scoped hybrid search."""
    from src.repositories.knowledge import KnowledgeRepository
    from src.services.embeddings import get_embedding_client

    embedding_client = await get_embedding_client(session)
    query_embedding = await embedding_client.embed_single(query)
    repository = KnowledgeRepository(
        session,
        org_id=organization_id,
        is_superuser=True,
    )
    return await repository.search(
        query_embedding=query_embedding,
        namespace=namespaces,
        query_text=query,
        limit=limit,
        min_score=min_score,
        metadata_filter=metadata_filter,
        fallback=fallback,
    )


__all__ = ["search_knowledge_documents"]
