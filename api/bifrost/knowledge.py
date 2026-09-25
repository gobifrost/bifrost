"""
Knowledge Store SDK for Bifrost - API-only implementation.

Provides Python API for semantic search and RAG (Retrieval Augmented Generation).
Uses pgvector for vector similarity search with org-scoped namespaces.

Outside an engine child all operations go through HTTP API endpoints;
inside an engine child the fixed operations ride the dedicated local
transport to the parent (same shared services, same results) with no
HTTP requests and no database connection in the child. A local attempt
never falls back to HTTP.
All methods are async and must be awaited.

Usage:
    from bifrost import knowledge

    # Store a document
    await knowledge.store(
        content="Our refund policy allows returns within 30 days.",
        namespace="policies",
        key="refund-policy",
        metadata={"source": "handbook"}
    )

    # Search for similar documents
    results = await knowledge.search(
        "What is the return window?",
        namespace="policies",
        limit=5
    )
    for doc in results:
        print(doc.content, doc.score)

    # List namespaces
    namespaces = await knowledge.list_namespaces()
"""

from __future__ import annotations

from typing import Any

from .client import get_client, raise_for_status_with_detail
from .models import KnowledgeDocument, NamespaceInfo
from ._context import resolve_scope
from ._local_transport import get as _get_local_transport


class knowledge:
    """
    Knowledge store operations.

    Provides semantic search and storage for RAG.
    Documents are scoped to organizations with global fallback.

    Outside an engine child all operations go through HTTP API endpoints;
    inside an engine child the fixed operations resolve through the parent
    over the dedicated local transport (same shared services as the HTTP
    endpoints) with no HTTP requests and no database connection in the
    child. A local attempt never falls back to HTTP.
    """

    @staticmethod
    async def store(
        content: str,
        *,
        namespace: str = "default",
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
        scope: str | None = None,
    ) -> str:
        """
        Store a document in the knowledge store.

        If key is provided and exists, updates the existing document (upsert).

        Args:
            content: Text content to store and embed
            namespace: Namespace for organization (defaults to "default")
            key: Optional key for upserts (e.g., "ticket-123")
            metadata: Optional metadata dict
            scope: Organization scope override. Omit to use the execution
                context org (with automatic global fallback via cascade).
                Pass an org UUID to target a specific org (provider orgs only).
                Pass None explicitly for global scope.

        Returns:
            Document ID (UUID as string)

        Example:
            >>> from bifrost import knowledge
            >>> doc_id = await knowledge.store(
            ...     "Our refund policy allows returns within 30 days.",
            ...     namespace="policies",
            ...     key="refund-policy",
            ...     metadata={"source": "handbook"}
            ... )
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent validates the same
            # ``CLIKnowledgeStoreRequest`` DTO and runs the shared
            # knowledge service over the dedicated channel. A local
            # attempt never falls back to HTTP.
            result = await transport.call_knowledge_store(
                content, namespace, key, metadata, effective_scope,
            )
            return result["id"]
        client = get_client()
        response = await client.post(
            "/api/sdk/knowledge/store",
            json={
                "content": content,
                "namespace": namespace,
                "key": key,
                "metadata": metadata,
                "scope": effective_scope,
            }
        )
        raise_for_status_with_detail(response)
        return response.json()["id"]

    @staticmethod
    async def store_many(
        documents: list[dict[str, Any]],
        *,
        namespace: str = "default",
        scope: str | None = None,
        timeout: float | None = 300.0,
    ) -> list[str]:
        """
        Store multiple documents efficiently.

        Each document dict should have:
        - content (required): Text content
        - key (optional): Key for upserts
        - metadata (optional): Metadata dict

        Args:
            documents: List of document dicts
            namespace: Namespace for all documents
            scope: Organization scope override. Omit to use the execution
                context org (with automatic global fallback via cascade).
                Pass an org UUID to target a specific org (provider orgs only).
                Pass None explicitly for global scope.
            timeout: HTTP read-timeout in seconds. Default 300s accommodates
                large batches whose embedding generation exceeds the SDK's
                default 30s client timeout.

        Returns:
            List of document IDs

        Example:
            >>> ids = await knowledge.store_many([
            ...     {"content": "Doc 1", "key": "doc-1", "metadata": {"type": "faq"}},
            ...     {"content": "Doc 2", "key": "doc-2", "metadata": {"type": "faq"}},
            ... ], namespace="faq")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent validates the same
            # ``CLIKnowledgeStoreManyRequest`` DTO and runs the shared
            # knowledge service over the dedicated channel (embedding
            # happens off-connection in the parent, so the batch
            # timeout rides the local call). A local attempt never
            # falls back to HTTP.
            result = await transport.call_knowledge_store_many(
                documents, namespace, effective_scope,
                timeout=timeout if timeout is not None else 300.0,
            )
            return result["ids"]
        client = get_client()
        response = await client.post(
            "/api/sdk/knowledge/store-many",
            json={
                "documents": documents,
                "namespace": namespace,
                "scope": effective_scope,
            },
            timeout=timeout,
        )
        raise_for_status_with_detail(response)
        return response.json()["ids"]

    @staticmethod
    async def search(
        query: str,
        *,
        namespace: str | list[str] = "default",
        limit: int = 5,
        min_score: float | None = None,
        metadata_filter: dict[str, Any] | None = None,
        scope: str | None = None,
        fallback: bool = True,
    ) -> list[KnowledgeDocument]:
        """
        Search for similar documents.

        Uses semantic similarity (vector search) to find relevant documents.

        Args:
            query: Search query (will be embedded)
            namespace: Namespace(s) to search
            limit: Maximum results (default 5)
            min_score: Minimum similarity score (0-1)
            metadata_filter: Filter by metadata fields (e.g., {"status": "open"})
            scope: Organization scope - can be:
                - None: Use execution context default org
                - org UUID string: Target specific organization
                - "global": Search only global scope
            fallback: If True, also search global scope (default True)

        Returns:
            List of KnowledgeDocument sorted by similarity

        Example:
            >>> results = await knowledge.search(
            ...     "password reset",
            ...     namespace="tickets",
            ...     metadata_filter={"status": "open"},
            ...     limit=10
            ... )
            >>> for doc in results:
            ...     print(f"{doc.score:.2f}: {doc.content[:100]}")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent validates the same
            # ``CLIKnowledgeSearchRequest`` DTO and runs the shared
            # knowledge service over the dedicated channel. A local
            # attempt never falls back to HTTP.
            items = await transport.call_knowledge_search(
                query,
                namespace if isinstance(namespace, list) else [namespace],
                limit,
                min_score,
                metadata_filter,
                effective_scope,
                fallback,
            )
            return [KnowledgeDocument.model_validate(doc) for doc in items]
        client = get_client()
        response = await client.post(
            "/api/sdk/knowledge/search",
            json={
                "query": query,
                "namespace": namespace if isinstance(namespace, list) else [namespace],
                "limit": limit,
                "min_score": min_score,
                "metadata_filter": metadata_filter,
                "scope": effective_scope,
                "fallback": fallback,
            }
        )
        raise_for_status_with_detail(response)
        return [
            KnowledgeDocument.model_validate(doc)
            for doc in response.json()
        ]

    @staticmethod
    async def delete(
        key: str,
        *,
        namespace: str = "default",
        scope: str | None = None,
    ) -> bool:
        """
        Delete a document by key.

        Args:
            key: Document key
            namespace: Namespace
            scope: Organization scope - can be:
                - None: Use execution context default org
                - org UUID string: Target specific organization
                - "global": Delete from global scope

        Returns:
            True if deleted, False if not found

        Example:
            >>> deleted = await knowledge.delete("ticket-123", namespace="tickets")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent validates the same
            # ``CLIKnowledgeDeleteRequest`` DTO and runs the shared
            # knowledge service over the dedicated channel. A local
            # attempt never falls back to HTTP.
            result = await transport.call_knowledge_delete(
                key, namespace, effective_scope,
            )
            return result["deleted"]
        client = get_client()
        response = await client.post(
            "/api/sdk/knowledge/delete",
            json={
                "key": key,
                "namespace": namespace,
                "scope": effective_scope,
            }
        )
        raise_for_status_with_detail(response)
        return response.json()["deleted"]

    @staticmethod
    async def delete_namespace(
        namespace: str,
        *,
        scope: str | None = None,
    ) -> int:
        """
        Delete all documents in a namespace.

        Args:
            namespace: Namespace to delete
            scope: Organization scope - can be:
                - None: Use execution context default org
                - org UUID string: Target specific organization
                - "global": Delete from global scope

        Returns:
            Number of documents deleted

        Example:
            >>> count = await knowledge.delete_namespace("old-data")
            >>> print(f"Deleted {count} documents")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent runs the shared knowledge
            # service over the dedicated channel. A local attempt never
            # falls back to HTTP.
            result = await transport.call_knowledge_delete_namespace(
                namespace, effective_scope,
            )
            return result["deleted_count"]
        client = get_client()
        params = {}
        if effective_scope:
            params["scope"] = effective_scope
        response = await client.delete(
            f"/api/sdk/knowledge/namespace/{namespace}",
            params=params if params else None,
        )
        raise_for_status_with_detail(response)
        return response.json()["deleted_count"]

    @staticmethod
    async def list_namespaces(
        scope: str | None = None,
        include_global: bool = True,
    ) -> list[NamespaceInfo]:
        """
        List available namespaces with document counts per scope.

        Args:
            scope: Organization scope - can be:
                - None: Use execution context default org
                - org UUID string: Target specific organization
            include_global: If True, include global namespaces (default True)

        Returns:
            List of NamespaceInfo with scope counts

        Example:
            >>> namespaces = await knowledge.list_namespaces()
            >>> for ns in namespaces:
            ...     print(f"{ns.namespace}: {ns.scopes['total']} docs")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent runs the shared knowledge
            # service over the dedicated channel. A local attempt never
            # falls back to HTTP.
            items = await transport.call_knowledge_list_namespaces(
                effective_scope, include_global,
            )
            return [NamespaceInfo.model_validate(ns) for ns in items]
        client = get_client()
        params: dict[str, Any] = {"include_global": include_global}
        if effective_scope:
            params["scope"] = effective_scope
        response = await client.get(
            "/api/sdk/knowledge/namespaces",
            params=params,
        )
        raise_for_status_with_detail(response)
        return [
            NamespaceInfo.model_validate(ns)
            for ns in response.json()
        ]

    @staticmethod
    async def get(
        key: str,
        *,
        namespace: str = "default",
        scope: str | None = None,
    ) -> KnowledgeDocument | None:
        """
        Get a document by key.

        Args:
            key: Document key
            namespace: Namespace
            scope: Organization scope - can be:
                - None: Use execution context default org
                - org UUID string: Target specific organization
                - "global": Get from global scope

        Returns:
            KnowledgeDocument or None if not found

        Example:
            >>> doc = await knowledge.get("refund-policy", namespace="policies")
            >>> if doc:
            ...     print(doc.content)
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent runs the shared knowledge
            # service over the dedicated channel (a miss is a 404 error
            # frame mapped to None, like the HTTP path). A local
            # attempt never falls back to HTTP.
            from .client import BifrostAPIError

            try:
                result = await transport.call_knowledge_get(
                    key, namespace, effective_scope,
                )
            except BifrostAPIError as e:
                if e.response.status_code == 404:
                    return None
                raise
            return KnowledgeDocument.model_validate(result)
        client = get_client()
        params: dict[str, Any] = {
            "key": key,
            "namespace": namespace,
        }
        if effective_scope:
            params["scope"] = effective_scope
        response = await client.get(
            "/api/sdk/knowledge/get",
            params=params,
        )
        if response.status_code == 404:
            return None
        raise_for_status_with_detail(response)
        return KnowledgeDocument.model_validate(response.json())
