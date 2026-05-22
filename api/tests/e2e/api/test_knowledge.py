"""
Knowledge Store E2E Tests.

Tests the full knowledge store (RAG) functionality including:
- Store, search, get, delete operations
- Organization isolation (org1 can't see org2's data)
- Global vs org-scoped documents
- Namespace management
- Metadata filtering
- Semantic search with real embeddings

Requires EMBEDDINGS_AI_TEST_KEY environment variable.
"""

import logging
import os

import pytest

logger = logging.getLogger(__name__)

# Skip entire module if embeddings not configured
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("EMBEDDINGS_AI_TEST_KEY"),
        reason="EMBEDDINGS_AI_TEST_KEY not configured"
    ),
]


class TestKnowledgeStoreBasicOperations:
    """Test basic knowledge store CRUD operations."""

    def test_store_and_get_document(
        self,
        e2e_client,
        platform_admin,
        org1,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test storing and retrieving a document by key."""
        # Store a document
        store_response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": "Python is a programming language known for its simplicity.",
                "namespace": "e2e-test",
                "key": "python-intro",
                "metadata": {"topic": "programming", "language": "python"},
            },
        )
        assert store_response.status_code == 200, f"Store failed: {store_response.text}"
        stored = store_response.json()
        assert "id" in stored

        # Get the document by key (GET endpoint with query params)
        get_response = e2e_client.get(
            "/api/cli/knowledge/get",
            headers=platform_admin.headers,
            params={"namespace": "e2e-test", "key": "python-intro"},
        )
        assert get_response.status_code == 200, f"Get failed: {get_response.text}"
        doc = get_response.json()
        assert doc["content"] == "Python is a programming language known for its simplicity."
        assert doc["metadata"]["topic"] == "programming"

    def test_store_and_search_documents(
        self,
        e2e_client,
        platform_admin,
        org1,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test storing documents and searching by semantic similarity."""
        # Store multiple documents
        docs = [
            {
                "content": "Machine learning is a subset of artificial intelligence.",
                "namespace": "e2e-test",
                "key": "ml-intro",
                "metadata": {"topic": "ai"},
            },
            {
                "content": "Database systems store and retrieve data efficiently.",
                "namespace": "e2e-test",
                "key": "db-intro",
                "metadata": {"topic": "databases"},
            },
            {
                "content": "Neural networks are inspired by biological neurons.",
                "namespace": "e2e-test",
                "key": "nn-intro",
                "metadata": {"topic": "ai"},
            },
        ]

        for doc in docs:
            response = e2e_client.post(
                "/api/cli/knowledge/store",
                headers=platform_admin.headers,
                json=doc,
            )
            assert response.status_code == 200, f"Store failed: {response.text}"

        # Search for AI-related documents (namespace is a list)
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=platform_admin.headers,
            json={
                "query": "What is artificial intelligence?",
                "namespace": ["e2e-test"],
                "limit": 5,
            },
        )
        assert search_response.status_code == 200, f"Search failed: {search_response.text}"
        results = search_response.json()

        # Should find documents, with AI-related ones ranked higher
        assert len(results) >= 2
        # First result should be one of the AI documents (ml-intro or nn-intro)
        top_keys = [r["key"] for r in results[:2]]
        assert "ml-intro" in top_keys or "nn-intro" in top_keys

    def test_long_document_search_returns_chunked_content(
        self,
        e2e_client,
        platform_admin,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Long documents are chunked transparently and keep metadata filters."""
        long_content = (
            "# Resetting MFA\n\n"
            + ("Detailed MFA reset instructions step by step. " * 80)
            + "\n\n# Resetting Password\n\n"
            + ("Detailed password reset instructions. " * 80)
            + "\n\n# Unlocking AD Account\n\n"
            + ("Detailed AD unlock instructions. " * 80)
        )

        store_response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": long_content,
                "namespace": "e2e-chunking",
                "key": "article-1",
                "metadata": {"client_id": "acme", "doc_type": "runbook"},
            },
        )
        assert store_response.status_code == 200, f"Store failed: {store_response.text}"

        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=platform_admin.headers,
            json={
                "query": "how do I unlock an AD account",
                "namespace": ["e2e-chunking"],
                "limit": 5,
            },
        )
        assert search_response.status_code == 200, f"Search failed: {search_response.text}"
        results = search_response.json()
        assert len(results) >= 1
        assert all(len(result["content"]) < 4000 for result in results)

        top_content = results[0]["content"].lower()
        assert "unlock" in top_content or "ad" in top_content

        filtered_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=platform_admin.headers,
            json={
                "query": "anything",
                "namespace": ["e2e-chunking"],
                "limit": 5,
                "metadata_filter": {"client_id": "acme"},
            },
        )
        assert filtered_response.status_code == 200, (
            f"Filtered search failed: {filtered_response.text}"
        )
        filtered_results = filtered_response.json()
        assert len(filtered_results) >= 1
        assert all(
            result["metadata"]["client_id"] == "acme"
            for result in filtered_results
        )

    def test_search_dedupes_chunked_document_by_key(
        self,
        e2e_client,
        platform_admin,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """A multi-chunk document appears at most once in default search."""
        long_content = ("Body content sentence. " * 500).strip()
        store_response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": long_content,
                "namespace": "e2e-dedup",
                "key": "single-doc",
                "metadata": {},
            },
        )
        assert store_response.status_code == 200, f"Store failed: {store_response.text}"

        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=platform_admin.headers,
            json={
                "query": "body",
                "namespace": ["e2e-dedup"],
                "limit": 5,
            },
        )
        assert search_response.status_code == 200, f"Search failed: {search_response.text}"
        results = search_response.json()
        assert len([result for result in results if result.get("key") == "single-doc"]) <= 1

    def test_delete_document(
        self,
        e2e_client,
        platform_admin,
        org1,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test deleting a document by key."""
        # Store a document
        e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": "This document will be deleted.",
                "namespace": "e2e-test",
                "key": "to-delete",
            },
        )

        # Verify it exists
        get_response = e2e_client.get(
            "/api/cli/knowledge/get",
            headers=platform_admin.headers,
            params={"namespace": "e2e-test", "key": "to-delete"},
        )
        assert get_response.status_code == 200

        # Delete it
        delete_response = e2e_client.post(
            "/api/cli/knowledge/delete",
            headers=platform_admin.headers,
            json={"namespace": "e2e-test", "key": "to-delete"},
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["deleted"] is True

        # Verify it's gone
        get_response = e2e_client.get(
            "/api/cli/knowledge/get",
            headers=platform_admin.headers,
            params={"namespace": "e2e-test", "key": "to-delete"},
        )
        assert get_response.status_code == 404

    def test_delete_namespace(
        self,
        e2e_client,
        platform_admin,
        org1,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test deleting all documents in a namespace."""
        # Store multiple documents
        for i in range(3):
            response = e2e_client.post(
                "/api/cli/knowledge/store",
                headers=platform_admin.headers,
                json={
                    "content": f"Document {i} in namespace",
                    "namespace": "e2e-test",
                    "key": f"doc-{i}",
                },
            )
            assert response.status_code == 200, f"Store {i} failed: {response.text}"

        # List namespaces to verify
        list_response = e2e_client.get(
            "/api/cli/knowledge/namespaces",
            headers=platform_admin.headers,
        )
        assert list_response.status_code == 200, f"List failed: {list_response.text}"
        namespaces = list_response.json()
        e2e_ns = next((ns for ns in namespaces if ns["namespace"] == "e2e-test"), None)
        assert e2e_ns is not None, f"Namespace not found. Available: {[ns['namespace'] for ns in namespaces]}"
        assert e2e_ns["scopes"]["total"] == 3

        # Delete the namespace
        delete_response = e2e_client.delete(
            "/api/cli/knowledge/namespace/e2e-test",
            headers=platform_admin.headers,
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["deleted_count"] == 3

        # Verify namespace is empty
        list_response = e2e_client.get(
            "/api/cli/knowledge/namespaces",
            headers=platform_admin.headers,
        )
        namespaces = list_response.json()
        e2e_ns = next((ns for ns in namespaces if ns["namespace"] == "e2e-test"), None)
        assert e2e_ns is None

    def test_upsert_document(
        self,
        e2e_client,
        platform_admin,
        org1,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that storing with same key updates existing document."""
        # Store initial document
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": "Initial content",
                "namespace": "e2e-test",
                "key": "upsert-test",
                "metadata": {"version": 1},
            },
        )
        assert response.status_code == 200, f"Initial store failed: {response.text}"

        # Upsert with same key
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": "Updated content",
                "namespace": "e2e-test",
                "key": "upsert-test",
                "metadata": {"version": 2},
            },
        )
        assert response.status_code == 200, f"Upsert failed: {response.text}"

        # Verify content was updated
        get_response = e2e_client.get(
            "/api/cli/knowledge/get",
            headers=platform_admin.headers,
            params={"namespace": "e2e-test", "key": "upsert-test"},
        )
        doc = get_response.json()
        assert doc["content"] == "Updated content"
        assert doc["metadata"]["version"] == 2


class TestKnowledgeStoreIsolation:
    """Test organization isolation for knowledge store."""

    def test_org1_cannot_see_org2_documents(
        self,
        e2e_client,
        platform_admin,
        org1,
        org2,
        org1_user,
        org2_user,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that org1 users cannot access org2's knowledge documents."""
        # Org1 stores a document (via org1_user who is scoped to org1)
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=org1_user.headers,
            json={
                "content": "Org1 secret knowledge about their systems.",
                "namespace": "e2e-isolation",
                "key": "org1-secret",
                "metadata": {"confidential": True},
            },
        )
        assert response.status_code == 200, f"Org1 store failed: {response.text}"

        # Org2 stores a document
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=org2_user.headers,
            json={
                "content": "Org2 proprietary knowledge about their processes.",
                "namespace": "e2e-isolation",
                "key": "org2-secret",
                "metadata": {"confidential": True},
            },
        )
        assert response.status_code == 200, f"Org2 store failed: {response.text}"

        # Org1 searches - should only find org1's document
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=org1_user.headers,
            json={
                "query": "secret knowledge systems processes",
                "namespace": ["e2e-isolation"],
                "limit": 10,
            },
        )
        assert search_response.status_code == 200, f"Org1 search failed: {search_response.text}"
        results = search_response.json()
        keys = [r["key"] for r in results]
        assert "org1-secret" in keys
        assert "org2-secret" not in keys

        # Org2 searches - should only find org2's document
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=org2_user.headers,
            json={
                "query": "secret knowledge systems processes",
                "namespace": ["e2e-isolation"],
                "limit": 10,
            },
        )
        assert search_response.status_code == 200, f"Org2 search failed: {search_response.text}"
        results = search_response.json()
        keys = [r["key"] for r in results]
        assert "org2-secret" in keys
        assert "org1-secret" not in keys

    def test_org1_cannot_get_org2_document_by_key(
        self,
        e2e_client,
        org1_user,
        org2_user,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that org1 cannot retrieve org2's document by key."""
        # Org2 stores a document
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=org2_user.headers,
            json={
                "content": "Org2 only document",
                "namespace": "e2e-isolation",
                "key": "org2-only",
            },
        )
        assert response.status_code == 200, f"Org2 store failed: {response.text}"

        # Org1 tries to get it - should fail
        get_response = e2e_client.get(
            "/api/cli/knowledge/get",
            headers=org1_user.headers,
            params={"namespace": "e2e-isolation", "key": "org2-only"},
        )
        assert get_response.status_code == 404

    def test_org1_cannot_delete_org2_document(
        self,
        e2e_client,
        org1_user,
        org2_user,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that org1 cannot delete org2's document."""
        # Org2 stores a document
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=org2_user.headers,
            json={
                "content": "Org2 protected document",
                "namespace": "e2e-isolation",
                "key": "org2-protected",
            },
        )
        assert response.status_code == 200, f"Org2 store failed: {response.text}"

        # Org1 tries to delete it - should return deleted=False
        delete_response = e2e_client.post(
            "/api/cli/knowledge/delete",
            headers=org1_user.headers,
            json={"namespace": "e2e-isolation", "key": "org2-protected"},
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["deleted"] is False

        # Verify org2's document still exists
        get_response = e2e_client.get(
            "/api/cli/knowledge/get",
            headers=org2_user.headers,
            params={"namespace": "e2e-isolation", "key": "org2-protected"},
        )
        assert get_response.status_code == 200


class TestKnowledgeStoreGlobalScope:
    """Test global scope functionality (platform admin only)."""

    def test_platform_admin_can_store_global_documents(
        self,
        e2e_client,
        platform_admin,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that platform admin can store globally scoped documents."""
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": "Global company policy on data handling.",
                "namespace": "e2e-global",
                "key": "data-policy",
                "scope": "global",
            },
        )
        assert response.status_code == 200, f"Store failed: {response.text}"
        # Store returns {"id": ...}, verify via get
        get_response = e2e_client.get(
            "/api/cli/knowledge/get",
            headers=platform_admin.headers,
            params={"namespace": "e2e-global", "key": "data-policy", "scope": "global"},
        )
        assert get_response.status_code == 200
        doc = get_response.json()
        assert doc["organization_id"] is None  # Global = no org

    def test_global_documents_visible_to_all_orgs(
        self,
        e2e_client,
        platform_admin,
        org1_user,
        org2_user,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that global documents are visible via search fallback."""
        # Store a global document
        response = e2e_client.post(
            "/api/cli/knowledge/store",
            headers=platform_admin.headers,
            json={
                "content": "Global FAQ about password reset procedures.",
                "namespace": "e2e-global",
                "key": "password-faq",
                "scope": "global",
            },
        )
        assert response.status_code == 200, f"Store failed: {response.text}"

        # Org1 can find it via search (fallback=True by default)
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=org1_user.headers,
            json={
                "query": "How do I reset my password?",
                "namespace": ["e2e-global"],
                "limit": 5,
            },
        )
        assert search_response.status_code == 200, f"Org1 search failed: {search_response.text}"
        results = search_response.json()
        keys = [r["key"] for r in results]
        assert "password-faq" in keys

        # Org2 can also find it
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=org2_user.headers,
            json={
                "query": "How do I reset my password?",
                "namespace": ["e2e-global"],
                "limit": 5,
            },
        )
        assert search_response.status_code == 200, f"Org2 search failed: {search_response.text}"
        results = search_response.json()
        keys = [r["key"] for r in results]
        assert "password-faq" in keys


class TestKnowledgeStoreMetadataFiltering:
    """Test metadata filtering in searches."""

    def test_search_with_metadata_filter(
        self,
        e2e_client,
        platform_admin,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test searching with metadata filters."""
        # Store documents with different metadata
        docs = [
            {
                "content": "Ticket about network connectivity issues.",
                "namespace": "e2e-test",
                "key": "ticket-1",
                "metadata": {"status": "open", "priority": "high", "user_id": "user-123"},
            },
            {
                "content": "Ticket about network performance problems.",
                "namespace": "e2e-test",
                "key": "ticket-2",
                "metadata": {"status": "closed", "priority": "high", "user_id": "user-456"},
            },
            {
                "content": "Ticket about network security concerns.",
                "namespace": "e2e-test",
                "key": "ticket-3",
                "metadata": {"status": "open", "priority": "low", "user_id": "user-123"},
            },
        ]

        for doc in docs:
            response = e2e_client.post(
                "/api/cli/knowledge/store",
                headers=platform_admin.headers,
                json=doc,
            )
            assert response.status_code == 200, f"Store failed: {response.text}"

        # Search for open tickets only
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=platform_admin.headers,
            json={
                "query": "network issues",
                "namespace": ["e2e-test"],
                "metadata_filter": {"status": "open"},
                "limit": 10,
            },
        )
        assert search_response.status_code == 200, f"Search failed: {search_response.text}"
        results = search_response.json()
        assert len(results) == 2
        for r in results:
            assert r["metadata"]["status"] == "open"

        # Search for specific user's tickets
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=platform_admin.headers,
            json={
                "query": "network",
                "namespace": ["e2e-test"],
                "metadata_filter": {"user_id": "user-123"},
                "limit": 10,
            },
        )
        assert search_response.status_code == 200
        results = search_response.json()
        assert len(results) == 2
        for r in results:
            assert r["metadata"]["user_id"] == "user-123"


class TestKnowledgeStoreNamespaces:
    """Test namespace listing functionality."""

    def test_list_namespaces_shows_counts(
        self,
        e2e_client,
        platform_admin,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that list_namespaces returns correct counts."""
        # Store documents in different namespaces
        for i in range(3):
            response = e2e_client.post(
                "/api/cli/knowledge/store",
                headers=platform_admin.headers,
                json={
                    "content": f"Document {i} in e2e-org1",
                    "namespace": "e2e-org1",
                    "key": f"doc-{i}",
                },
            )
            assert response.status_code == 200, f"Store failed: {response.text}"

        for i in range(2):
            response = e2e_client.post(
                "/api/cli/knowledge/store",
                headers=platform_admin.headers,
                json={
                    "content": f"Document {i} in e2e-org2",
                    "namespace": "e2e-org2",
                    "key": f"doc-{i}",
                },
            )
            assert response.status_code == 200, f"Store failed: {response.text}"

        # List namespaces
        list_response = e2e_client.get(
            "/api/cli/knowledge/namespaces",
            headers=platform_admin.headers,
        )
        assert list_response.status_code == 200, f"List failed: {list_response.text}"
        namespaces = list_response.json()

        # Find our test namespaces
        org1_ns = next((ns for ns in namespaces if ns["namespace"] == "e2e-org1"), None)
        org2_ns = next((ns for ns in namespaces if ns["namespace"] == "e2e-org2"), None)

        assert org1_ns is not None
        assert org1_ns["scopes"]["total"] == 3

        assert org2_ns is not None
        assert org2_ns["scopes"]["total"] == 2


class TestKnowledgeStoreBatchOperations:
    """Test batch store operations."""

    def test_store_many_documents(
        self,
        e2e_client,
        platform_admin,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test storing multiple documents in a single request."""
        response = e2e_client.post(
            "/api/cli/knowledge/store-many",
            headers=platform_admin.headers,
            json={
                "namespace": "e2e-test",
                "documents": [
                    {
                        "content": "First batch document about Python.",
                        "key": "batch-1",
                        "metadata": {"type": "tutorial"},
                    },
                    {
                        "content": "Second batch document about JavaScript.",
                        "key": "batch-2",
                        "metadata": {"type": "tutorial"},
                    },
                    {
                        "content": "Third batch document about TypeScript.",
                        "key": "batch-3",
                        "metadata": {"type": "tutorial"},
                    },
                ],
            },
        )
        assert response.status_code == 200, f"Store-many failed: {response.text}"
        result = response.json()
        assert len(result["ids"]) == 3

        # Verify all were stored
        for key in ["batch-1", "batch-2", "batch-3"]:
            get_response = e2e_client.get(
                "/api/cli/knowledge/get",
                headers=platform_admin.headers,
                params={"namespace": "e2e-test", "key": key},
            )
            assert get_response.status_code == 200


class TestKnowledgeStoreSemanticSearch:
    """Test semantic search quality."""

    def test_semantic_similarity_ranking(
        self,
        e2e_client,
        platform_admin,
        embedding_config_setup,
        knowledge_cleanup,
    ):
        """Test that semantically similar documents rank higher."""
        # Store documents with varying semantic similarity
        docs = [
            {
                "content": "The quick brown fox jumps over the lazy dog.",
                "namespace": "e2e-test",
                "key": "pangram",
            },
            {
                "content": "Artificial intelligence and machine learning are transforming industries.",
                "namespace": "e2e-test",
                "key": "ai-ml",
            },
            {
                "content": "Deep learning neural networks can recognize patterns in data.",
                "namespace": "e2e-test",
                "key": "deep-learning",
            },
            {
                "content": "Natural language processing enables computers to understand text.",
                "namespace": "e2e-test",
                "key": "nlp",
            },
        ]

        for doc in docs:
            response = e2e_client.post(
                "/api/cli/knowledge/store",
                headers=platform_admin.headers,
                json=doc,
            )
            assert response.status_code == 200, f"Store failed: {response.text}"

        # Search for AI-related content
        search_response = e2e_client.post(
            "/api/cli/knowledge/search",
            headers=platform_admin.headers,
            json={
                "query": "How does AI work?",
                "namespace": ["e2e-test"],
                "limit": 4,
            },
        )
        assert search_response.status_code == 200, f"Search failed: {search_response.text}"
        results = search_response.json()

        # The AI-related documents should rank higher than the pangram
        ai_keys = {"ai-ml", "deep-learning", "nlp"}
        top_3_keys = {r["key"] for r in results[:3]}
        # At least 2 of the top 3 should be AI-related
        assert len(ai_keys & top_3_keys) >= 2

        # Pangram should have lowest score (if it appears)
        pangram_result = next((r for r in results if r["key"] == "pangram"), None)
        if pangram_result:
            # Pangram should be last or have lowest score
            assert results[-1]["key"] == "pangram" or pangram_result["score"] == min(r["score"] for r in results)
