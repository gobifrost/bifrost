"""
Unit tests for the documentation indexer service.

Tests content hash-based caching to avoid unnecessary OpenAI API calls.
"""

import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from src.services.docs_indexer import (
    compute_content_hash,
    index_platform_docs,
    NAMESPACE,
)
from src.repositories.knowledge import KnowledgeDocument


class TestComputeContentHash:
    """Tests for the compute_content_hash function."""

    def test_same_content_same_hash(self):
        """Same content should produce the same hash."""
        content = "Hello, world!"
        hash1 = compute_content_hash(content)
        hash2 = compute_content_hash(content)
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """Different content should produce different hashes."""
        hash1 = compute_content_hash("Hello, world!")
        hash2 = compute_content_hash("Hello, world?")
        assert hash1 != hash2

    def test_hash_is_sha256_format(self):
        """Hash should be a 64-character hex string (SHA-256)."""
        content_hash = compute_content_hash("test content")
        assert len(content_hash) == 64
        assert all(c in "0123456789abcdef" for c in content_hash)

    def test_unicode_content(self):
        """Should handle unicode content correctly."""
        content = "Hello, \u4e16\u754c! \U0001f600"  # Hello, 世界! 😀
        content_hash = compute_content_hash(content)
        assert len(content_hash) == 64


class TestIndexPlatformDocs:
    """Tests for the index_platform_docs function."""

    @pytest.fixture
    def mock_docs_path(self, tmp_path: Path):
        """Create a temporary docs directory with test files."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        # Create test documentation files
        (docs_dir / "getting-started").mkdir()
        (docs_dir / "getting-started" / "installation.txt").write_text(
            "Installation instructions here."
        )
        (docs_dir / "core-concepts").mkdir()
        (docs_dir / "core-concepts" / "workflows.txt").write_text(
            "Workflow documentation here."
        )

        return docs_dir

    @pytest.fixture
    def mock_embedding_client(self):
        """Mock embedding client that returns fake embeddings."""
        client = AsyncMock()
        client.embed = AsyncMock(return_value=[[0.1] * 1536])
        return client

    @pytest.fixture
    def mock_knowledge_repo(self):
        """Mock knowledge repository."""
        repo = AsyncMock()
        repo.get_all_by_namespace = AsyncMock(return_value={})
        repo.store_chunked = AsyncMock(return_value=["doc-id-123"])
        repo.delete_orphaned_docs = AsyncMock(return_value=0)
        return repo

    @pytest.mark.asyncio
    async def test_skips_when_docs_dir_not_found(self):
        """Should skip gracefully when docs directory doesn't exist."""
        with patch(
            "src.services.docs_indexer.DOCS_PATH",
            Path("/nonexistent/path"),
        ):
            result = await index_platform_docs()

        assert result["status"] == "skipped"
        assert "not found" in result["reason"]

    @pytest.mark.asyncio
    async def test_skips_when_no_doc_files(self, tmp_path: Path):
        """Should skip when docs directory is empty."""
        empty_dir = tmp_path / "empty_docs"
        empty_dir.mkdir()

        with patch("src.services.docs_indexer.DOCS_PATH", empty_dir):
            result = await index_platform_docs()

        assert result["status"] == "skipped"
        assert "no documentation files" in result["reason"]

    @pytest.mark.asyncio
    async def test_indexes_new_docs(
        self,
        mock_docs_path: Path,
        mock_embedding_client: AsyncMock,
        mock_knowledge_repo: AsyncMock,
    ):
        """Should index all docs when none exist in database."""
        mock_db = AsyncMock()

        with (
            patch("src.services.docs_indexer.DOCS_PATH", mock_docs_path),
            patch(
                "src.services.docs_indexer.get_db_context",
                return_value=AsyncContextManager(mock_db),
            ),
            patch(
                "src.services.docs_indexer.get_embedding_client",
                return_value=mock_embedding_client,
            ),
            patch(
                "src.services.docs_indexer.KnowledgeRepository",
                return_value=mock_knowledge_repo,
            ),
        ):
            result = await index_platform_docs()

        assert result["status"] == "complete"
        assert result["indexed"] == 2  # Two test files
        assert result["skipped"] == 0
        assert result["deleted"] == 0
        assert mock_knowledge_repo.store_chunked.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_unchanged_docs(
        self,
        mock_docs_path: Path,
        mock_embedding_client: AsyncMock,
        mock_knowledge_repo: AsyncMock,
    ):
        """Should skip docs when content hash matches."""
        # Read actual file content to get correct hash
        installation_content = (
            mock_docs_path / "getting-started" / "installation.txt"
        ).read_text()
        workflows_content = (
            mock_docs_path / "core-concepts" / "workflows.txt"
        ).read_text()

        # Simulate existing docs with matching hashes
        mock_knowledge_repo.get_all_by_namespace = AsyncMock(
            return_value={
                "getting-started/installation": KnowledgeDocument(
                    id="doc-1",
                    namespace=NAMESPACE,
                    content=installation_content,
                    metadata={
                        "content_hash": compute_content_hash(installation_content)
                    },
                ),
                "core-concepts/workflows": KnowledgeDocument(
                    id="doc-2",
                    namespace=NAMESPACE,
                    content=workflows_content,
                    metadata={"content_hash": compute_content_hash(workflows_content)},
                ),
            }
        )

        mock_db = AsyncMock()

        with (
            patch("src.services.docs_indexer.DOCS_PATH", mock_docs_path),
            patch(
                "src.services.docs_indexer.get_db_context",
                return_value=AsyncContextManager(mock_db),
            ),
            patch(
                "src.services.docs_indexer.get_embedding_client",
                return_value=mock_embedding_client,
            ),
            patch(
                "src.services.docs_indexer.KnowledgeRepository",
                return_value=mock_knowledge_repo,
            ),
        ):
            result = await index_platform_docs()

        assert result["status"] == "complete"
        assert result["indexed"] == 0  # Nothing new
        assert result["skipped"] == 2  # Both skipped
        assert mock_knowledge_repo.store_chunked.call_count == 0

    @pytest.mark.asyncio
    async def test_reindexes_changed_docs(
        self,
        mock_docs_path: Path,
        mock_embedding_client: AsyncMock,
        mock_knowledge_repo: AsyncMock,
    ):
        """Should re-index docs when content has changed."""
        # Read actual file content
        installation_content = (
            mock_docs_path / "getting-started" / "installation.txt"
        ).read_text()

        # Simulate existing docs - one with matching hash, one with old hash
        mock_knowledge_repo.get_all_by_namespace = AsyncMock(
            return_value={
                "getting-started/installation": KnowledgeDocument(
                    id="doc-1",
                    namespace=NAMESPACE,
                    content=installation_content,
                    metadata={
                        "content_hash": compute_content_hash(installation_content)
                    },
                ),
                "core-concepts/workflows": KnowledgeDocument(
                    id="doc-2",
                    namespace=NAMESPACE,
                    content="old content",
                    metadata={"content_hash": compute_content_hash("old content")},
                ),
            }
        )

        mock_db = AsyncMock()

        with (
            patch("src.services.docs_indexer.DOCS_PATH", mock_docs_path),
            patch(
                "src.services.docs_indexer.get_db_context",
                return_value=AsyncContextManager(mock_db),
            ),
            patch(
                "src.services.docs_indexer.get_embedding_client",
                return_value=mock_embedding_client,
            ),
            patch(
                "src.services.docs_indexer.KnowledgeRepository",
                return_value=mock_knowledge_repo,
            ),
        ):
            result = await index_platform_docs()

        assert result["status"] == "complete"
        assert result["indexed"] == 1  # Only changed doc
        assert result["skipped"] == 1  # Unchanged doc
        assert mock_knowledge_repo.store_chunked.call_count == 1

    @pytest.mark.asyncio
    async def test_deletes_orphaned_docs(
        self,
        mock_docs_path: Path,
        mock_embedding_client: AsyncMock,
        mock_knowledge_repo: AsyncMock,
    ):
        """Should delete docs that no longer exist on disk."""
        # Simulate orphaned doc in database
        mock_knowledge_repo.get_all_by_namespace = AsyncMock(
            return_value={
                "deleted/doc": KnowledgeDocument(
                    id="orphan-1",
                    namespace=NAMESPACE,
                    content="This file was deleted",
                    metadata={"content_hash": "abc123"},
                ),
            }
        )
        mock_knowledge_repo.delete_orphaned_docs = AsyncMock(return_value=1)

        mock_db = AsyncMock()

        with (
            patch("src.services.docs_indexer.DOCS_PATH", mock_docs_path),
            patch(
                "src.services.docs_indexer.get_db_context",
                return_value=AsyncContextManager(mock_db),
            ),
            patch(
                "src.services.docs_indexer.get_embedding_client",
                return_value=mock_embedding_client,
            ),
            patch(
                "src.services.docs_indexer.KnowledgeRepository",
                return_value=mock_knowledge_repo,
            ),
        ):
            result = await index_platform_docs()

        assert result["status"] == "complete"
        assert result["deleted"] == 1
        mock_knowledge_repo.delete_orphaned_docs.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_content_hash_in_metadata(
        self,
        mock_docs_path: Path,
        mock_embedding_client: AsyncMock,
        mock_knowledge_repo: AsyncMock,
    ):
        """Should store content hash in document metadata."""
        mock_db = AsyncMock()

        with (
            patch("src.services.docs_indexer.DOCS_PATH", mock_docs_path),
            patch(
                "src.services.docs_indexer.get_db_context",
                return_value=AsyncContextManager(mock_db),
            ),
            patch(
                "src.services.docs_indexer.get_embedding_client",
                return_value=mock_embedding_client,
            ),
            patch(
                "src.services.docs_indexer.KnowledgeRepository",
                return_value=mock_knowledge_repo,
            ),
        ):
            await index_platform_docs()

        # Check that store_chunked was called with content_hash in metadata
        store_calls = mock_knowledge_repo.store_chunked.call_args_list
        assert len(store_calls) == 2

        for call in store_calls:
            metadata = call.kwargs["metadata"]
            assert "content_hash" in metadata
            assert len(metadata["content_hash"]) == 64  # SHA-256
            assert call.kwargs["embedder"] is mock_embedding_client

    @pytest.mark.asyncio
    async def test_skips_when_no_embedding_config(self, mock_docs_path: Path):
        """Should skip gracefully when embedding client isn't configured."""
        mock_db = AsyncMock()

        with (
            patch("src.services.docs_indexer.DOCS_PATH", mock_docs_path),
            patch(
                "src.services.docs_indexer.get_db_context",
                return_value=AsyncContextManager(mock_db),
            ),
            patch(
                "src.services.docs_indexer.get_embedding_client",
                side_effect=ValueError("No embedding configuration found"),
            ),
        ):
            result = await index_platform_docs()

        assert result["status"] == "skipped"
        assert "No embedding configuration" in result["reason"]


class AsyncContextManager:
    """Helper to create async context manager from a mock."""

    def __init__(self, mock_db: AsyncMock):
        self.mock_db = mock_db

    async def __aenter__(self):
        return self.mock_db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
