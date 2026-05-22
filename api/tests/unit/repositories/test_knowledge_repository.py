"""Tests for KnowledgeRepository chunked storage and search."""

import pytest
from sqlalchemy import func, select

from src.models.orm.knowledge import KnowledgeStore
from src.repositories.knowledge import KnowledgeRepository


class _FakeEmbedder:
    """Stub embedder returning deterministic small vectors."""

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i % self.dim) for i in range(self.dim)] for _ in texts]

    async def embed_single(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


@pytest.mark.asyncio
async def test_store_short_content_produces_one_row(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()

    await repo.store_chunked(
        content="Short document.",
        namespace="test-ns",
        key="doc-1",
        embedder=embedder,
    )

    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.key == "doc-1")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].chunk_index == 0
    assert rows[0].chunk_count == 1
    assert rows[0].content == "Short document."


@pytest.mark.asyncio
async def test_store_long_content_produces_multiple_rows(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Sentence number X. " * 500).strip()

    await repo.store_chunked(
        content=long,
        namespace="test-ns",
        key="doc-2",
        metadata={"client_id": "acme"},
        embedder=embedder,
    )

    rows = (
        await db_session.execute(
            select(KnowledgeStore)
            .where(KnowledgeStore.key == "doc-2")
            .order_by(KnowledgeStore.chunk_index)
        )
    ).scalars().all()
    assert len(rows) >= 4
    assert [row.chunk_index for row in rows] == list(range(len(rows)))
    assert all(row.chunk_count == len(rows) for row in rows)
    assert all(row.doc_metadata == {"client_id": "acme"} for row in rows)
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == len(rows)


@pytest.mark.asyncio
async def test_store_replaces_existing_chunks_atomically(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long_v1 = ("Version one. " * 500).strip()
    long_v2 = ("Version two. " * 500).strip()

    await repo.store_chunked(content=long_v1, namespace="ns", key="k", embedder=embedder)
    count_v1 = (
        await db_session.execute(
            select(func.count()).select_from(KnowledgeStore).where(KnowledgeStore.key == "k")
        )
    ).scalar_one()
    assert count_v1 >= 4

    await repo.store_chunked(content=long_v2, namespace="ns", key="k", embedder=embedder)
    rows = (
        await db_session.execute(select(KnowledgeStore).where(KnowledgeStore.key == "k"))
    ).scalars().all()
    assert all("Version two" in row.content for row in rows)
    assert all("Version one" not in row.content for row in rows)


@pytest.mark.asyncio
async def test_store_without_key_inserts_chunk_rows(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Anonymous doc. " * 500).strip()

    await repo.store_chunked(content=long, namespace="ns-keyless", key=None, embedder=embedder)
    rows = (
        await db_session.execute(
            select(KnowledgeStore)
            .where(KnowledgeStore.namespace == "ns-keyless")
            .order_by(KnowledgeStore.chunk_index)
        )
    ).scalars().all()
    assert len(rows) >= 4
    assert all(row.key is None for row in rows)
    assert all(row.chunk_count == len(rows) for row in rows)


@pytest.mark.asyncio
async def test_search_dedups_by_key_by_default(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()

    long = ("Subject of interest. " * 500).strip()
    await repo.store_chunked(content=long, namespace="ns-search", key="big", embedder=embedder)
    for index in range(3):
        await repo.store_chunked(
            content=f"Short doc {index}.",
            namespace="ns-search",
            key=f"small-{index}",
            embedder=embedder,
        )
    await db_session.flush()

    query_embedding = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_embedding,
        namespace="ns-search",
        limit=5,
    )

    keys = [result.key for result in results]
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_search_group_by_key_false_returns_raw_chunks(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Subject of interest. " * 500).strip()
    await repo.store_chunked(content=long, namespace="ns-raw", key="big", embedder=embedder)
    await db_session.flush()

    query_embedding = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_embedding,
        namespace="ns-raw",
        limit=10,
        group_by_key=False,
    )

    assert len([result for result in results if result.key == "big"]) > 1


@pytest.mark.asyncio
async def test_search_metadata_filter_applies_to_chunks(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Body text. " * 500).strip()
    await repo.store_chunked(
        content=long,
        namespace="ns-filter",
        key="acme-doc",
        metadata={"client_id": "acme"},
        embedder=embedder,
    )
    await repo.store_chunked(
        content=long,
        namespace="ns-filter",
        key="other-doc",
        metadata={"client_id": "other"},
        embedder=embedder,
    )
    await db_session.flush()

    query_embedding = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_embedding,
        namespace="ns-filter",
        metadata_filter={"client_id": "acme"},
        limit=5,
    )

    assert all(result.metadata.get("client_id") == "acme" for result in results)
    assert any(result.key == "acme-doc" for result in results)
