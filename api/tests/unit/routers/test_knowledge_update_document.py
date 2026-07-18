"""
Regression tests for the knowledge document UPDATE path
(``update_document`` in routers/knowledge_sources.py).

The previous implementation called ``client.embed(str)`` and assigned the
resulting ``list[list[float]]`` straight to a single row's ``embedding``
column, which crashed at flush with:

    TypeError: float() argument must be a string or a real number, not 'list'

It also never chunked (unlike the create path). These tests pin the fixed
behaviour: update re-chunks and re-embeds via ``store_chunked``, storing a
flat per-chunk vector per row, and upserts (replaces) the document's prior
rows.
"""

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from src.models.contracts.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentUpdate,
)
from src.models.orm.knowledge import KnowledgeStore
from src.models.orm.users import User
from src.repositories.knowledge import KnowledgeRepository
from src.routers.knowledge_sources import (
    create_document,
    get_document,
    update_document,
)


class _FakeEmbedder:
    """Deterministic stub returning one flat vector per input text."""

    def __init__(self, dim: int = 8):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(i % self.dim) for i in range(self.dim)] for _ in texts]

    async def embed_single(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class _User:
    def __init__(self):
        # created_by is FK → users; leave it NULL (nullable column) so the
        # test doesn't need to seed a user row. Identity is incidental to what
        # these tests assert (vector shape / chunking / upsert).
        self.user_id = None
        self.organization_id = None
        self.is_superuser = True
        self.is_provider_org = False


class _FailingEmbedder:
    """Embedder that raises — simulates the embedding provider being down."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc or RuntimeError("embedding provider unavailable")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise self.exc

    async def embed_single(self, text: str) -> list[float]:
        raise self.exc


def _patch_embedder(embedder=None):
    """Patch the factory so update/create use a fake embedder, no network."""
    return patch(
        "src.services.embeddings.factory.get_embedding_client",
        AsyncMock(return_value=embedder or _FakeEmbedder()),
    )


async def _count_rows(db, namespace: str) -> int:
    result = await db.execute(
        select(func.count(KnowledgeStore.id)).where(
            KnowledgeStore.namespace == namespace
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_update_keyed_doc_stores_flat_vectors(db_session):
    """The core regression: update must not crash and must store flat vectors."""
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku",
            data=KnowledgeDocumentCreate(content="original", key="k1"),
            db=db_session,
            user=user,
            scope=None,
        )
        updated = await update_document(
            namespace="ku",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="updated body"),
            db=db_session,
            user=user,
            scope=None,
        )

    # Load every row for the key and assert each embedding is a flat list[float].
    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.key == "k1")
        )
    ).scalars().all()
    assert rows, "expected at least one stored row"
    for row in rows:
        assert isinstance(row.embedding, list)
        assert all(isinstance(v, float) for v in row.embedding)
    assert updated.content == "updated body"


@pytest.mark.asyncio
async def test_update_upserts_and_does_not_duplicate(db_session):
    """Updating a keyed doc replaces its rows rather than accumulating them."""
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku2",
            data=KnowledgeDocumentCreate(content="v1", key="k"),
            db=db_session,
            user=user,
            scope=None,
        )
        before = await _count_rows(db_session, "ku2")
        await update_document(
            namespace="ku2",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="v2 short"),
            db=db_session,
            user=user,
            scope=None,
        )
        after = await _count_rows(db_session, "ku2")

    # Both are short (single-chunk) → exactly one row before and after.
    assert before == 1
    assert after == 1


@pytest.mark.asyncio
async def test_update_rechunks_long_content(db_session):
    """Long updated content is re-chunked into multiple rows (parity with create)."""
    user = _User()
    long_content = "sentence. " * 4000  # well past the chunk threshold
    with _patch_embedder():
        created = await create_document(
            namespace="ku3",
            data=KnowledgeDocumentCreate(content="short original", key="big"),
            db=db_session,
            user=user,
            scope=None,
        )
        assert await _count_rows(db_session, "ku3") == 1

        await update_document(
            namespace="ku3",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content=long_content),
            db=db_session,
            user=user,
            scope=None,
        )

    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.key == "big")
        )
    ).scalars().all()
    assert len(rows) > 1, "long content should produce multiple chunk rows"
    assert {r.chunk_count for r in rows} == {len(rows)}
    for r in rows:
        assert all(isinstance(v, float) for v in r.embedding)


@pytest.mark.asyncio
async def test_update_keyless_doc(db_session):
    """A keyless doc updates in place without crashing or duplicating."""
    user = _User()
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    with _patch_embedder():
        ids = await repo.store_chunked(
            content="keyless original",
            namespace="ku4",
            key=None,
            embedder=_FakeEmbedder(),
        )
        await db_session.flush()

        await update_document(
            namespace="ku4",
            doc_id=UUID(ids[0]),
            data=KnowledgeDocumentUpdate(content="keyless updated"),
            db=db_session,
            user=user,
            scope=None,
        )

    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.namespace == "ku4")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "keyless updated"
    assert all(isinstance(v, float) for v in rows[0].embedding)


@pytest.mark.asyncio
async def test_update_preserves_created_at(db_session):
    """Editing a document must NOT reset created_at (docs are listed by it)."""
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku5",
            data=KnowledgeDocumentCreate(content="v1", key="k"),
            db=db_session,
            user=user,
            scope=None,
        )
        original_created_at = (
            await db_session.execute(
                select(KnowledgeStore.created_at).where(
                    KnowledgeStore.id == UUID(created.id)
                )
            )
        ).scalar_one()

        updated = await update_document(
            namespace="ku5",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="v2 edited"),
            db=db_session,
            user=user,
            scope=None,
        )

    assert updated.created_at == original_created_at


@pytest.mark.asyncio
async def test_update_with_scope_change_moves_not_copies(db_session):
    """Changing scope on update is a MOVE: the source-org rows must be gone,
    not left behind as a duplicate."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from src.models.orm.organizations import Organization

    # Seed a real target org (organization_id is an FK).
    target_org = Organization(
        id=uuid4(),
        name=f"ku-move-org-{uuid4().hex[:8]}",
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(target_org)
    await db_session.flush()

    user = _User()
    with _patch_embedder():
        # Create globally (organization_id = None).
        created = await create_document(
            namespace="ku6",
            data=KnowledgeDocumentCreate(content="movable", key="mk"),
            db=db_session,
            user=user,
            scope=None,
        )
        assert created.organization_id is None

        moved = await update_document(
            namespace="ku6",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="movable v2"),
            db=db_session,
            user=user,
            scope=str(target_org.id),
        )

    assert moved.organization_id == str(target_org.id)

    all_rows = (
        await db_session.execute(
            select(KnowledgeStore).where(
                KnowledgeStore.namespace == "ku6",
                KnowledgeStore.key == "mk",
            )
        )
    ).scalars().all()
    # Exactly one logical doc, and it lives in the target org — no global copy left.
    assert len(all_rows) == 1
    assert all_rows[0].organization_id == target_org.id


@pytest.mark.asyncio
async def test_update_embed_failure_does_not_lose_document(db_session):
    """If re-embedding fails mid-update, the original document must survive.

    replace_chunked embeds BEFORE deleting anything, and only ever flushes —
    the commit happens in get_db AFTER the handler returns. A failed embed
    raises out of the handler, so get_db rolls the transaction back and no
    delete was even attempted. This test simulates that: commit the original
    doc (as a prior request would), force the update's embed to fail, then
    roll back like get_db does, and assert the original doc is intact.
    """
    user = _User()

    # Create + COMMIT the original doc, so it exists independently of the
    # update transaction (mirrors a prior successful request).
    with _patch_embedder():
        created = await create_document(
            namespace="ku7",
            data=KnowledgeDocumentCreate(content="precious original", key="p"),
            db=db_session,
            user=user,
            scope=None,
        )
    await db_session.commit()
    original_id = UUID(created.id)

    # The update's embed blows up.
    with _patch_embedder(_FailingEmbedder()):
        with pytest.raises(RuntimeError, match="embedding provider unavailable"):
            await update_document(
                namespace="ku7",
                doc_id=original_id,
                data=KnowledgeDocumentUpdate(content="new content that never embeds"),
                db=db_session,
                user=user,
                scope=None,
            )

    # get_db's except branch rolls the failed transaction back.
    await db_session.rollback()

    surviving = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.key == "p")
        )
    ).scalars().all()
    assert len(surviving) == 1
    assert surviving[0].id == original_id
    assert surviving[0].content == "precious original"

    # Clean up the committed row so it doesn't leak past this test.
    await db_session.execute(delete(KnowledgeStore).where(KnowledgeStore.key == "p"))
    await db_session.commit()


async def _seed_org(db) -> "object":
    """Insert a real Organization row (organization_id is an FK)."""
    from src.models.orm.organizations import Organization

    org = Organization(
        id=uuid4(),
        name=f"ku-org-{uuid4().hex[:8]}",
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(org)
    await db.flush()
    return org


@pytest.mark.asyncio
async def test_update_multichunk_keyed_full_content_roundtrip(db_session):
    """GET → edit → PUT on a multi-chunk doc must not truncate it.

    GET reassembles the full content from the chunk rows, and PUT replaces
    every chunk row — so a round-trip through the read surface preserves the
    whole document instead of silently dropping everything past chunk 0.
    """
    user = _User()
    original = "First version sentence. " * 1000   # multi-chunk
    edited = "Second version sentence. " * 1200    # multi-chunk, different size
    with _patch_embedder():
        created = await create_document(
            namespace="ku8",
            data=KnowledgeDocumentCreate(content=original, key="full"),
            db=db_session,
            user=user,
            scope=None,
        )
        # Create echoes the full submitted content, not chunk 0's slice.
        assert created.content == original

        fetched = await get_document(
            namespace="ku8", doc_id=UUID(created.id), db=db_session, user=user
        )
        assert fetched.content == original

        updated = await update_document(
            namespace="ku8",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content=edited),
            db=db_session,
            user=user,
            scope=None,
        )
        assert updated.content == edited

        refetched = await get_document(
            namespace="ku8", doc_id=UUID(created.id), db=db_session, user=user
        )
        assert refetched.content == edited

    # No stale rows: every remaining row belongs to the new version.
    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.namespace == "ku8")
        )
    ).scalars().all()
    assert len(rows) > 1
    assert {r.chunk_count for r in rows} == {len(rows)}


@pytest.mark.asyncio
async def test_update_preserves_document_id(db_session):
    """The document keeps its public id across edits — stored references
    (UI rows, sync jobs, SDK callers) must not 404 after an update."""
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku9",
            data=KnowledgeDocumentCreate(content="v1", key="stable"),
            db=db_session,
            user=user,
            scope=None,
        )
        updated = await update_document(
            namespace="ku9",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="v2 " * 2000),  # goes multi-chunk
            db=db_session,
            user=user,
            scope=None,
        )
        assert updated.id == created.id

        again = await update_document(
            namespace="ku9",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="v3 back to short"),
            db=db_session,
            user=user,
            scope=None,
        )
        assert again.id == created.id

        fetched = await get_document(
            namespace="ku9", doc_id=UUID(created.id), db=db_session, user=user
        )
        assert fetched.id == created.id
        assert fetched.content == "v3 back to short"


@pytest.mark.asyncio
async def test_update_keyless_multichunk_replaces_all_rows(db_session):
    """A keyless doc can span multiple chunk rows (chunk_index disambiguates
    the NULL key in the unique constraint). Updating it must replace ALL of
    its rows — not just the addressed one — or the re-store collides on
    chunk_index / strands stale chunks."""
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku10",
            data=KnowledgeDocumentCreate(content="keyless long. " * 1000),
            db=db_session,
            user=user,
            scope=None,
        )
        assert await _count_rows(db_session, "ku10") > 1

        # Still-long replacement: would raise IntegrityError on chunk_index
        # collision if the old sibling rows survived.
        await update_document(
            namespace="ku10",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="keyless edited. " * 1000),
            db=db_session,
            user=user,
            scope=None,
        )
        rows = (
            await db_session.execute(
                select(KnowledgeStore).where(KnowledgeStore.namespace == "ku10")
            )
        ).scalars().all()
        assert {r.chunk_count for r in rows} == {len(rows)}
        assert all("edited" in r.content for r in rows)

        # Shrinking replacement: no orphan chunks left behind.
        await update_document(
            namespace="ku10",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="keyless short"),
            db=db_session,
            user=user,
            scope=None,
        )
        assert await _count_rows(db_session, "ku10") == 1


@pytest.mark.asyncio
async def test_update_scope_change_conflict_multichunk_409_and_replace(db_session):
    """A scope move onto a MULTI-chunk conflicting doc must 409 cleanly (the
    old single-row lookup raised MultipleResultsFound → 500), and
    replace=true must remove every chunk row of the conflicting doc."""
    user = _User()
    org = await _seed_org(db_session)
    with _patch_embedder():
        # Conflicting doc in the target org — long enough to chunk.
        await create_document(
            namespace="ku11",
            data=KnowledgeDocumentCreate(content="occupant. " * 1000, key="ck"),
            db=db_session,
            user=user,
            scope=str(org.id),
        )
        # Global doc with the same key.
        source = await create_document(
            namespace="ku11",
            data=KnowledgeDocumentCreate(content="mover", key="ck"),
            db=db_session,
            user=user,
            scope=None,
        )

        # replace=False must be explicit here: calling the handler directly
        # (not through FastAPI) would otherwise bind the Query(...) FieldInfo
        # default, which is truthy.
        with pytest.raises(HTTPException) as exc:
            await update_document(
                namespace="ku11",
                doc_id=UUID(source.id),
                data=KnowledgeDocumentUpdate(content="mover v2"),
                db=db_session,
                user=user,
                scope=str(org.id),
                replace=False,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["conflicting_id"]

        moved = await update_document(
            namespace="ku11",
            doc_id=UUID(source.id),
            data=KnowledgeDocumentUpdate(content="mover v2"),
            db=db_session,
            user=user,
            scope=str(org.id),
            replace=True,
        )
        assert moved.organization_id == str(org.id)

    # Only the moved doc remains under the key — every chunk row of the
    # conflicting occupant is gone.
    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(
                KnowledgeStore.namespace == "ku11",
                KnowledgeStore.key == "ck",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "mover v2"
    assert rows[0].organization_id == org.id


@pytest.mark.asyncio
async def test_update_keyless_scope_move_conflict_409_and_replace(db_session):
    """Keyless docs collide too (NULL key == NULL key under NULLS NOT
    DISTINCT) — a keyless scope move onto an occupied namespace/org must 409
    instead of dying on the unique constraint, and replace=true resolves it."""
    user = _User()
    org = await _seed_org(db_session)
    with _patch_embedder():
        await create_document(
            namespace="ku12",
            data=KnowledgeDocumentCreate(content="org keyless occupant"),
            db=db_session,
            user=user,
            scope=str(org.id),
        )
        source = await create_document(
            namespace="ku12",
            data=KnowledgeDocumentCreate(content="global keyless"),
            db=db_session,
            user=user,
            scope=None,
        )

        # Explicit replace=False — see the keyed conflict test above.
        with pytest.raises(HTTPException) as exc:
            await update_document(
                namespace="ku12",
                doc_id=UUID(source.id),
                data=KnowledgeDocumentUpdate(content="global keyless v2"),
                db=db_session,
                user=user,
                scope=str(org.id),
                replace=False,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["key"] is None

        moved = await update_document(
            namespace="ku12",
            doc_id=UUID(source.id),
            data=KnowledgeDocumentUpdate(content="global keyless v2"),
            db=db_session,
            user=user,
            scope=str(org.id),
            replace=True,
        )
        assert moved.organization_id == str(org.id)

    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.namespace == "ku12")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "global keyless v2"


@pytest.mark.asyncio
async def test_update_embed_valueerror_maps_to_503(db_session):
    """Embed-time ValueError keeps the old contract: 503 'Embedding service
    unavailable', not an opaque 500."""
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku13",
            data=KnowledgeDocumentCreate(content="v1", key="e503"),
            db=db_session,
            user=user,
            scope=None,
        )

    with _patch_embedder(_FailingEmbedder(ValueError("provider rejected request"))):
        with pytest.raises(HTTPException) as exc:
            await update_document(
                namespace="ku13",
                doc_id=UUID(created.id),
                data=KnowledgeDocumentUpdate(content="v2"),
                db=db_session,
                user=user,
                scope=None,
            )
    assert exc.value.status_code == 503
    assert "Embedding service unavailable" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_via_sibling_chunk_preserves_canonical_document_id(db_session):
    """Every chunk id addresses one logical document, but chunk zero owns its
    stable public id. Updating through a sibling must not promote that sibling
    and invalidate references to the canonical id.
    """
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku14",
            data=KnowledgeDocumentCreate(
                content="multi chunk original. " * 1000,
                key="canonical",
            ),
            db=db_session,
            user=user,
            scope=None,
        )
        rows = (
            await db_session.execute(
                select(KnowledgeStore)
                .where(KnowledgeStore.namespace == "ku14")
                .order_by(KnowledgeStore.chunk_index)
            )
        ).scalars().all()
        assert len(rows) > 1

        updated = await update_document(
            namespace="ku14",
            doc_id=rows[1].id,
            data=KnowledgeDocumentUpdate(content="replacement"),
            db=db_session,
            user=user,
            scope=None,
        )

        assert updated.id == created.id
        fetched = await get_document(
            namespace="ku14",
            doc_id=UUID(created.id),
            db=db_session,
            user=user,
        )
        assert fetched.content == "replacement"


@pytest.mark.asyncio
async def test_update_preserves_original_creator(db_session):
    """Replacing chunk rows during an edit must retain creator attribution."""
    creator_id = uuid4()
    editor_id = uuid4()
    db_session.add_all(
        [
            User(
                id=creator_id,
                email=f"creator-{uuid4()}@example.com",
                is_superuser=True,
            ),
            User(
                id=editor_id,
                email=f"editor-{uuid4()}@example.com",
                is_superuser=True,
            ),
        ]
    )
    await db_session.flush()

    creator = _User()
    creator.user_id = creator_id
    editor = _User()
    editor.user_id = editor_id

    with _patch_embedder():
        created = await create_document(
            namespace="ku15",
            data=KnowledgeDocumentCreate(content="original", key="audit"),
            db=db_session,
            user=creator,
            scope=None,
        )
        await update_document(
            namespace="ku15",
            doc_id=UUID(created.id),
            data=KnowledgeDocumentUpdate(content="edited"),
            db=db_session,
            user=editor,
            scope=None,
        )

    stored_creator = (
        await db_session.execute(
            select(KnowledgeStore.created_by).where(
                KnowledgeStore.id == UUID(created.id)
            )
        )
    ).scalar_one()
    assert stored_creator == creator_id


@pytest.mark.asyncio
async def test_concurrent_updates_via_different_chunks_serialize(
    db_session, async_session_factory
):
    """Chunk aliases must converge on one lock; locking the addressed rows
    separately lets the two group-wide replacements deadlock each other.
    """
    user = _User()
    with _patch_embedder():
        created = await create_document(
            namespace="ku16",
            data=KnowledgeDocumentCreate(
                content="concurrent original. " * 1000,
                key="serialized",
            ),
            db=db_session,
            user=user,
            scope=None,
        )
    rows = (
        await db_session.execute(
            select(KnowledgeStore)
            .where(KnowledgeStore.namespace == "ku16")
            .order_by(KnowledgeStore.chunk_index)
        )
    ).scalars().all()
    assert len(rows) > 1
    canonical_id = UUID(created.id)
    sibling_id = rows[1].id
    await db_session.commit()

    async def edit(addressed_id: UUID, content: str):
        async with async_session_factory() as session:
            updated = await update_document(
                namespace="ku16",
                doc_id=addressed_id,
                data=KnowledgeDocumentUpdate(content=content),
                db=session,
                user=user,
                scope=None,
            )
            await session.commit()
            return updated

    original_lock = KnowledgeRepository.lock_document
    lock_barrier = asyncio.Barrier(2)

    async def synchronized_lock(repo, namespace, key, organization_id):
        # Make both requests resolve their addressed chunk before either gets
        # the canonical lock. They must then serialize on that one row.
        await lock_barrier.wait()
        return await original_lock(repo, namespace, key, organization_id)

    with (
        patch.object(KnowledgeRepository, "lock_document", synchronized_lock),
        _patch_embedder(),
    ):
        results = await asyncio.wait_for(
            asyncio.gather(
                edit(canonical_id, "first concurrent edit"),
                edit(sibling_id, "second concurrent edit"),
                return_exceptions=True,
            ),
            timeout=10,
        )

    failures = [result for result in results if isinstance(result, BaseException)]
    assert not failures, failures
    assert {result.id for result in results} == {str(canonical_id)}
