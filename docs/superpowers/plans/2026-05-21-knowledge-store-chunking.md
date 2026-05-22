# Knowledge Store Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Progress (as of 2026-05-22)

- ✅ **Task 1 complete** — commit `0cdf4c27`. Schema migration `api/alembic/versions/20260521_knowledge_chunking_columns.py` applied to dev DB. `knowledge_store` has `chunk_index INTEGER NOT NULL DEFAULT 0`, `chunk_count INTEGER NOT NULL DEFAULT 1`, unique constraint `uq_knowledge_ns_org_key_chunk` over `(namespace, organization_id, key, chunk_index)` with `NULLS NOT DISTINCT`, and partial index `ix_knowledge_ns_org_key` on `(namespace, organization_id, key) WHERE key IS NOT NULL`. ORM at `api/src/models/orm/knowledge.py` mirrors it.
- ✅ **Task 2 complete** — commits `deeb9188` (impl) + `6c0fedb3` (test tightening). `api/src/services/knowledge/chunking.py` exports `split_into_chunks(text, target_chars=2000, overlap_chars=200) -> list[str]`. 7 unit tests pass.
- ✅ **Task 3 complete** — working tree changes pending commit. `KnowledgeRepository.store_chunked()` now splits and embeds content, replaces keyed docs atomically, and callers in docs indexing, knowledge sources, and CLI storage use it.
- ✅ **Task 4 complete** — working tree changes pending commit. `KnowledgeRepository.search()` over-fetches and deduplicates by `(namespace, organization_id, key)` by default, with `group_by_key=False` for raw chunk access.
- ✅ **Task 5 complete** — working tree changes pending commit. Embedding reindex now re-chunks keyed document groups through `store_chunked()`, preserves cancellation/progress failure semantics, and keeps keyless rows on in-place re-embedding.
- ✅ **Task 6 complete** — working tree changes pending commit. Existing knowledge E2E suite covers long-doc chunked search, metadata filters, and dedup; local run collected the tests but skipped them because `EMBEDDINGS_AI_TEST_KEY` is not configured.
- ⏭️ **Resume at Task 7** below.

**One deviation from this plan that landed in Task 2** (for awareness, no action needed): in `test_long_text_splits_at_paragraph_boundaries`, the paragraph multiplier was `* 40` (1684 chars, under the 2000 threshold → would return one chunk and fail the >=2 assertion). Bumped to `* 80` (~3372 chars). Also tightened that test's size assertion from `<= 2000 + 200` to `<= 2000`, and removed an unfalsifiable `or last_sentence in tail` clause from the overlap test.

## Resumption notes for the next session

- Working directory: `/home/jack/GitHub/bifrost/.claude/worktrees/knowledge-chunking` (git worktree on branch `worktree-knowledge-chunking`). Do all work here — not the primary checkout.
- Dev stack: check with `./debug.sh status` from the worktree root. The test stack used by `./test.sh` is independent and was last seen up.
- Migration is already applied to the dev DB. Don't re-run it. If a fresh container ever rebuilds, `bifrost-init` will replay it cleanly.
- Skip Tasks 1 & 2. Start at Task 3.


**Goal:** Stop `search_knowledge` from returning whole documents (avg 12K chars / ~3K tokens per row in `halo_kb`) by transparently chunking content on store, deduplicating results on search, and rebuilding existing rows via the existing reindex flow — without changing any public API surface.

**Architecture:** Add `chunk_index` and `chunk_count` columns to `knowledge_store` with backwards-compatible defaults (`0` / `1`). `KnowledgeRepository.store()` splits long content on natural boundaries (~500 tokens with ~100 overlap), embeds each chunk, and inside one transaction deletes all prior rows for the key and inserts the new chunks. `KnowledgeRepository.search()` over-fetches and deduplicates by `(namespace, organization_id, key)` so a single doc can't dominate `limit=5`. The existing "reindex" job is upgraded from row-by-row re-embedding to group-by-key re-chunk + re-embed, which fixes legacy data the first time a user clicks it. Metadata filtering is preserved because `doc_metadata` is copied verbatim to every chunk row.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x / pgvector / Alembic / pytest (backend). No new dependencies — the splitter is ~40 lines of stdlib.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `api/alembic/versions/<new>_knowledge_chunking.py` | Create | Add `chunk_index` / `chunk_count` columns, swap unique constraint |
| `api/src/models/orm/knowledge.py` | Modify | Add two columns + update unique constraint |
| `api/src/services/knowledge/chunking.py` | Create | Pure function `split_into_chunks(text) -> list[str]` (no deps) |
| `api/tests/unit/services/knowledge/test_chunking.py` | Create | Chunking edge cases (short, long, paragraph/sentence boundaries, overlap) |
| `api/src/repositories/knowledge.py` | Modify | `store()` chunks + multi-row insert; `search()` dedups by key |
| `api/tests/unit/repositories/test_knowledge_repository.py` | Modify (or create) | `store()` round-trips, upsert, search dedup, metadata copy |
| `api/src/services/embeddings/reindex.py` | Modify | Group by `(namespace, org_id, key)`, re-call `store()` per group |
| `api/tests/unit/services/embeddings/test_reindex.py` | Modify (or create) | Reindex now produces chunked rows from legacy giant rows |
| `api/tests/e2e/test_knowledge_search.py` | Modify (or create) | E2E: store long doc → search → returns short chunk, metadata filter still works |

Frontend: no changes. The reindex button already exists and triggers the same backend endpoint.

---

## Task 1: Schema migration ✅ COMPLETE — commit `0cdf4c27`

**Files:**
- Create: `api/alembic/versions/<auto>_knowledge_chunking.py`
- Modify: `api/src/models/orm/knowledge.py:48-108`

- [ ] **Step 1: Generate the migration skeleton**

Run from the worktree root with the test stack down (so alembic doesn't run mid-test):
```bash
cd api && alembic revision -m "knowledge_chunking_columns"
```
Expected: prints a file path like `alembic/versions/abc123_knowledge_chunking_columns.py`. Open it.

- [ ] **Step 2: Write the migration body**

Replace the body of the generated file with:

```python
"""knowledge_chunking_columns

Adds chunk_index and chunk_count to knowledge_store, and widens the
uniqueness constraint to include chunk_index so multiple chunks of the
same key can coexist.

Existing rows get chunk_index=0, chunk_count=1, which is byte-identical
to today's "one row per doc" behavior. No data migration needed —
re-chunking happens lazily on the next store() or reindex.
"""
from alembic import op
import sqlalchemy as sa

revision = "abc123_knowledge_chunking_columns"  # match filename
down_revision = "<the previous head>"            # alembic fills this
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_store",
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "knowledge_store",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="1"),
    )

    # Drop the old (ns, org, key) unique constraint and replace with
    # (ns, org, key, chunk_index). postgresql_nulls_not_distinct preserves
    # the existing "treat NULL org_id as equal" semantic.
    op.drop_constraint("uq_knowledge_ns_org_key", "knowledge_store", type_="unique")
    op.create_unique_constraint(
        "uq_knowledge_ns_org_key_chunk",
        "knowledge_store",
        ["namespace", "organization_id", "key", "chunk_index"],
        postgresql_nulls_not_distinct=True,
    )

    # Non-unique lookup index for "find all chunks of this doc" — used by
    # reindex grouping and by search dedup. Restricted to non-null keys
    # because key-less docs can't be grouped.
    op.create_index(
        "ix_knowledge_ns_org_key",
        "knowledge_store",
        ["namespace", "organization_id", "key"],
        postgresql_where=sa.text("key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_ns_org_key", table_name="knowledge_store")
    op.drop_constraint("uq_knowledge_ns_org_key_chunk", "knowledge_store", type_="unique")
    op.create_unique_constraint(
        "uq_knowledge_ns_org_key",
        "knowledge_store",
        ["namespace", "organization_id", "key"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("knowledge_store", "chunk_count")
    op.drop_column("knowledge_store", "chunk_index")
```

After writing, replace `down_revision = "<the previous head>"` with the value alembic generated in the skeleton (don't lose it).

- [ ] **Step 3: Update the ORM model**

In `api/src/models/orm/knowledge.py`, after the existing `created_by` column declaration (line ~86), add:

```python
    # Chunking — see migration knowledge_chunking_columns.
    # Long content is split on store() into multiple rows sharing
    # (namespace, organization_id, key). Single-chunk rows have
    # chunk_index=0, chunk_count=1, which is byte-identical to pre-chunking
    # storage. chunk_count is denormalized for cheap "is this a chunked doc"
    # checks without an extra query.
    chunk_index: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    chunk_count: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default=text("1")
    )
```

You'll need to add `Integer` to the existing `sqlalchemy` import line — but `mapped_column(nullable=False, default=0)` infers Integer from the `Mapped[int]` annotation, so no extra import is required.

In the `__table_args__` block (line ~94), replace the existing `UniqueConstraint(...)` and `Index("ix_knowledge_ns_org", ...)` lines with:

```python
        UniqueConstraint(
            "namespace", "organization_id", "key", "chunk_index",
            name="uq_knowledge_ns_org_key_chunk",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_knowledge_ns_org", "namespace", "organization_id"),
        Index(
            "ix_knowledge_ns_org_key",
            "namespace", "organization_id", "key",
            postgresql_where=text("key IS NOT NULL"),
        ),
```

- [ ] **Step 4: Apply the migration**

```bash
docker restart bifrost-debug-<this-worktree>-bifrost-init-1
docker compose -f docker-compose.dev.yml logs --tail=20 bifrost-init
```
Expected: alembic log shows the new revision applied. Then restart the API to pick up the model change:
```bash
docker restart bifrost-debug-<this-worktree>-api-1
```

- [ ] **Step 5: Verify the schema**

```bash
docker exec bifrost-debug-<this-worktree>-postgres-1 psql -U bifrost -d bifrost \
  -c "\d knowledge_store"
```
Expected: `chunk_index` and `chunk_count` columns visible with defaults `0` and `1`; new unique constraint `uq_knowledge_ns_org_key_chunk` listed.

- [ ] **Step 6: Commit**

```bash
git add api/alembic/versions/*knowledge_chunking* api/src/models/orm/knowledge.py
git commit -m "feat(knowledge): add chunk_index/chunk_count columns + widen unique constraint"
```

---

## Task 2: Chunking function (pure, unit-tested) ✅ COMPLETE — commits `deeb9188` + `6c0fedb3`

**Files:**
- Create: `api/src/services/knowledge/__init__.py` (empty if directory doesn't exist)
- Create: `api/src/services/knowledge/chunking.py`
- Create: `api/tests/unit/services/knowledge/__init__.py` (empty)
- Create: `api/tests/unit/services/knowledge/test_chunking.py`

- [ ] **Step 1: Write the failing tests first**

Create `api/tests/unit/services/knowledge/test_chunking.py`:

```python
"""Tests for knowledge content chunking."""
from src.services.knowledge.chunking import split_into_chunks


def test_short_text_returns_single_chunk():
    text = "This is a short document that fits in one chunk."
    chunks = split_into_chunks(text)
    assert chunks == [text]


def test_empty_string_returns_single_empty_chunk():
    # An empty doc is a valid doc — store() should still produce one row.
    assert split_into_chunks("") == [""]


def test_long_text_splits_at_paragraph_boundaries():
    # Three ~600-char paragraphs separated by blank lines. With target
    # chunk size ~2000 chars (~500 tokens), we expect two chunks: the
    # first holds para 1+2, the second holds para 3 — split at the
    # paragraph break, not mid-sentence.
    para = "Sentence one. " * 40  # ~560 chars
    text = f"{para}\n\n{para}\n\n{para}"
    chunks = split_into_chunks(text, target_chars=2000, overlap_chars=200)
    assert len(chunks) >= 2
    # No chunk exceeds target by more than the overlap allowance.
    assert all(len(c) <= 2000 + 200 for c in chunks)
    # Reassembled content is a superset of the original (overlap means
    # some text repeats, but every character of the original appears).
    rejoined = " ".join(chunks)
    for fragment in ["Sentence one."]:
        assert fragment in rejoined


def test_long_text_with_no_paragraph_breaks_splits_at_sentences():
    text = ("This is sentence number one. " * 100).strip()  # ~3000 chars, no \n
    chunks = split_into_chunks(text, target_chars=1000, overlap_chars=100)
    assert len(chunks) >= 3
    # Each chunk should end at a sentence boundary (period + space) or
    # be the last chunk.
    for chunk in chunks[:-1]:
        assert chunk.rstrip().endswith(".")


def test_long_text_with_no_boundaries_falls_back_to_hard_cut():
    # No paragraph, no sentence, no spaces — single long token.
    text = "a" * 5000
    chunks = split_into_chunks(text, target_chars=1000, overlap_chars=100)
    assert len(chunks) >= 5
    assert all(len(c) <= 1000 for c in chunks)


def test_overlap_repeats_trailing_context():
    # Build a doc where we can verify the tail of chunk N appears in
    # the head of chunk N+1.
    sentences = [f"Sentence {i}." for i in range(50)]
    text = " ".join(sentences)
    chunks = split_into_chunks(text, target_chars=200, overlap_chars=50)
    assert len(chunks) >= 3
    for i in range(len(chunks) - 1):
        tail = chunks[i][-30:]
        # The next chunk should start with content that came from near
        # the end of the previous chunk (overlap window). We check that
        # *some* recent sentence from chunk i appears in chunk i+1.
        prev_sentences = [s for s in chunks[i].split(".") if s.strip()]
        if prev_sentences:
            last_sentence = prev_sentences[-1].strip()
            assert last_sentence in chunks[i + 1] or last_sentence in tail


def test_default_target_size_is_reasonable_for_embeddings():
    # ~500 tokens ≈ 2000 chars. Doc of ~10000 chars (the halo_kb
    # average) should produce roughly 5 chunks, not 1 and not 50.
    text = ("Lorem ipsum dolor sit amet. " * 400).strip()  # ~10800 chars
    chunks = split_into_chunks(text)
    assert 4 <= len(chunks) <= 8
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
./test.sh stack up
./test.sh tests/unit/services/knowledge/test_chunking.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.services.knowledge.chunking'` or all tests collected but fail.

- [ ] **Step 3: Implement `split_into_chunks`**

Create `api/src/services/knowledge/__init__.py` (empty) and `api/src/services/knowledge/chunking.py`:

```python
"""
Content chunking for the knowledge store.

Splits long documents into ~target_chars windows with overlap, preferring
natural boundaries (paragraph → sentence → word → hard cut) so that
embeddings are computed over coherent text instead of mid-sentence fragments.

Why character-based and not token-based:
- We don't want a tokenizer dependency in this layer (the embedding client
  owns that). ~4 chars/token is a stable approximation across English text
  and current OpenAI/Cohere/Anthropic models.
- The exact chunk size doesn't matter — embeddings degrade smoothly with
  size. Anywhere from 400-800 tokens is fine. We aim for ~500 (2000 chars).
"""
from __future__ import annotations

DEFAULT_TARGET_CHARS = 2000   # ~500 tokens
DEFAULT_OVERLAP_CHARS = 200   # ~50 tokens of trailing context repeated


def split_into_chunks(
    text: str,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """
    Split `text` into chunks of at most `target_chars`, preferring natural
    boundaries. Returns at least one chunk (an empty list is never valid —
    an empty doc returns `[""]`).
    """
    if len(text) <= target_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + target_chars, len(text))
        if end < len(text):
            end = _find_boundary(text, start, end)
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= len(text):
            break
        # Step forward by (chunk_length - overlap) so the next window
        # repeats the last `overlap_chars` of this one.
        start = max(end - overlap_chars, start + 1)
    return chunks


def _find_boundary(text: str, start: int, end: int) -> int:
    """
    Walk backward from `end` looking for a natural boundary. The search
    window is bounded by `start + (end-start)//2` so we never produce a
    chunk smaller than half the target (avoids pathological short chunks
    when boundaries are sparse).
    """
    min_acceptable = start + (end - start) // 2
    for boundary in ("\n\n", ". ", "? ", "! ", "\n", " "):
        idx = text.rfind(boundary, min_acceptable, end)
        if idx != -1:
            return idx + len(boundary)
    return end  # Hard cut — no boundary found in the search window.
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
./test.sh tests/unit/services/knowledge/test_chunking.py -v
```
Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/knowledge/ api/tests/unit/services/knowledge/
git commit -m "feat(knowledge): add content chunker with natural-boundary splitting"
```

---

## Task 3: Repository — `store_chunked()` replaces `store()` ✅ COMPLETE

**Files:**
- Modify: `api/src/repositories/knowledge.py:61-130`
- Modify (or create): `api/tests/unit/repositories/test_knowledge_repository.py`
- Modify: every caller of the old `store()` (audit in Step 1)

**Approach:** Add a new method `store_chunked(content, ..., embedder)` that owns chunking and embedding. Migrate every caller of the old `store(content, embedding, ...)` to call `store_chunked` instead (passing the embedder, not a precomputed embedding). After all callers are migrated, **delete the old `store()` method**. There is no compatibility shim — the change is internal-only and we know all the call sites.

This is a small breaking change to the repository's internal API. There are no external consumers of `KnowledgeRepository.store` — it's a private repository class used only inside the API process.

**Test-fixture note:** the unit-test block below uses a `db_session` fixture. Before writing tests, search `api/tests/conftest.py` and any sub-conftests for an existing async DB session fixture used by other repository unit tests. If you find one, use it (with whatever name it has — adapt the test signatures). If none exists, **do not invent one** — repository tests with real DB sessions are non-trivial in this codebase. Stop and ask the user, OR skip the unit tests for this task and rely on the e2e tests in Task 6 to cover the same code paths through HTTP (these are likely sufficient).

Audit callers before implementing:

- [ ] **Step 1: Find every call site of `KnowledgeRepository.store(`**

```bash
rg -n "KnowledgeRepository\b|\.store\(" api/src api/shared --type py | rg -v test | head -40
```
Read each hit. Expected callers: knowledge router (`POST /api/knowledge/documents`), CLI handler, the SDK-facing internal helper. All compute the embedding immediately before calling `store()`. **None** depend on the returned ID being a single row.

- [ ] **Step 2: Write the failing tests**

Add to (or create) `api/tests/unit/repositories/test_knowledge_repository.py`:

```python
"""Tests for KnowledgeRepository.store() chunking + search() dedup."""
import pytest
from sqlalchemy import select, func

from src.models.orm.knowledge import KnowledgeStore
from src.repositories.knowledge import KnowledgeRepository


class _FakeEmbedder:
    """Stub embedder — returns deterministic small vectors so we can
    assert on counts without caring about values."""
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

    rows = (await db_session.execute(
        select(KnowledgeStore).where(KnowledgeStore.key == "doc-1")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].chunk_index == 0
    assert rows[0].chunk_count == 1
    assert rows[0].content == "Short document."


@pytest.mark.asyncio
async def test_store_long_content_produces_multiple_rows(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Sentence number X. " * 500).strip()  # ~10000 chars

    await repo.store_chunked(
        content=long,
        namespace="test-ns",
        key="doc-2",
        metadata={"client_id": "acme"},
        embedder=embedder,
    )

    rows = (await db_session.execute(
        select(KnowledgeStore)
        .where(KnowledgeStore.key == "doc-2")
        .order_by(KnowledgeStore.chunk_index)
    )).scalars().all()
    assert len(rows) >= 4
    assert [r.chunk_index for r in rows] == list(range(len(rows)))
    assert all(r.chunk_count == len(rows) for r in rows)
    # Metadata copied to every chunk.
    assert all(r.doc_metadata == {"client_id": "acme"} for r in rows)
    # Each chunk got its own embedding (one embed() call, batched).
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == len(rows)


@pytest.mark.asyncio
async def test_store_replaces_existing_chunks_atomically(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long_v1 = ("Version one. " * 500).strip()
    long_v2 = ("Version two. " * 500).strip()

    await repo.store_chunked(content=long_v1, namespace="ns", key="k", embedder=embedder)
    count_v1 = (await db_session.execute(
        select(func.count()).select_from(KnowledgeStore).where(KnowledgeStore.key == "k")
    )).scalar_one()
    assert count_v1 >= 4

    await repo.store_chunked(content=long_v2, namespace="ns", key="k", embedder=embedder)
    rows = (await db_session.execute(
        select(KnowledgeStore).where(KnowledgeStore.key == "k")
    )).scalars().all()
    assert all("Version two" in r.content for r in rows)
    assert all("Version one" not in r.content for r in rows)


@pytest.mark.asyncio
async def test_store_without_key_inserts_single_row(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    # No key means no upsert semantics — long content still chunks, but
    # repeat calls just add more rows.
    long = ("Anonymous doc. " * 500).strip()
    await repo.store_chunked(content=long, namespace="ns", key=None, embedder=embedder)
    rows = (await db_session.execute(
        select(KnowledgeStore).where(KnowledgeStore.namespace == "ns")
    )).scalars().all()
    assert len(rows) >= 4
    # All chunks share key=None and chunk_count.
    assert all(r.key is None for r in rows)
    assert all(r.chunk_count == len(rows) for r in rows)
```

You'll need a `db_session` fixture if one doesn't already exist. Check `api/tests/conftest.py` and reuse the existing test DB session fixture used by other repository unit tests. If you can't find one, the e2e tests in Task 6 cover the same paths through HTTP — skip the unit tests here and rely on those. **Do not invent a new fixture**; ask first.

- [ ] **Step 3: Run tests, confirm they fail**

```bash
./test.sh tests/unit/repositories/test_knowledge_repository.py -v
```
Expected: `AttributeError: 'KnowledgeRepository' object has no attribute 'store_chunked'`.

- [ ] **Step 4: Implement `store_chunked`**

In `api/src/repositories/knowledge.py`, add this method after the existing `store()` (around line 131). Do not delete or modify `store()` — keep it for backwards compatibility with the one or two non-chunking callers, but make it call `store_chunked` internally so chunking still applies.

Add imports at the top of the file:
```python
from src.services.embeddings.base import EmbeddingClient  # type: ignore[attr-defined]
from src.services.knowledge.chunking import split_into_chunks
```

Then add the method:

```python
    async def store_chunked(
        self,
        content: str,
        namespace: str = "default",
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
        organization_id: UUID | None = None,
        created_by: UUID | None = None,
        embedder: "EmbeddingClient | None" = None,
    ) -> list[str]:
        """
        Store `content` as one or more chunks, sharing (namespace, org_id, key).

        Long content is split into ~500-token windows with overlap; short
        content produces a single row identical to legacy behavior. If `key`
        is provided, any existing chunks under that key are deleted first
        (within the same transaction) so upsert semantics are preserved.

        Args:
            content: Text content (any length).
            namespace: Namespace for organization.
            key: Optional user-provided key. Required for upsert; without
                 it, repeat calls insert duplicate rows.
            metadata: Optional metadata dict — copied verbatim to every
                      chunk row so JSONB filters keep working.
            organization_id: Organization scope (None for global).
            created_by: User who created the document.
            embedder: Embedding client. Required — pass in from the caller
                      so the repo doesn't own embedding-config plumbing.

        Returns:
            List of inserted row IDs (UUIDs as strings), in chunk_index order.
        """
        if embedder is None:
            raise ValueError("store_chunked requires an embedder")

        target_org_id = organization_id if organization_id is not None else self.org_id
        chunks = split_into_chunks(content)
        embeddings = await embedder.embed(chunks)

        # Atomic replace: clear old chunks for this key, then insert new.
        # delete_by_key handles the org-scoping rules.
        if key is not None:
            await self.session.execute(
                delete(KnowledgeStore).where(
                    KnowledgeStore.key == key,
                    KnowledgeStore.namespace == namespace,
                    (KnowledgeStore.organization_id == target_org_id)
                    if target_org_id is not None
                    else KnowledgeStore.organization_id.is_(None),
                )
            )

        chunk_count = len(chunks)
        new_rows = [
            KnowledgeStore(
                namespace=namespace,
                organization_id=target_org_id,
                key=key,
                content=chunk_text,
                doc_metadata=metadata or {},
                embedding=embedding,
                created_by=created_by,
                chunk_index=i,
                chunk_count=chunk_count,
            )
            for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings))
        ]
        self.session.add_all(new_rows)
        await self.session.flush()
        return [str(r.id) for r in new_rows]
```

- [ ] **Step 5: Run tests, confirm they pass**

```bash
./test.sh tests/unit/repositories/test_knowledge_repository.py -v
```
Expected: all four tests pass.

- [ ] **Step 6: Route the existing `store()` through `store_chunked` (only if all callers already have an embedder available)**

Audit: every caller of `repo.store()` got the embedding by calling `embedding_client.embed_single(content)` immediately before. Refactor those callers to pass `embedder=embedding_client` and call `repo.store_chunked(content=..., embedder=embedder)` instead of pre-computing the embedding.

```bash
rg -n "\.store\(" api/src api/shared --type py | rg -i "knowledge" | head
```

For each caller, change:
```python
embedding = await embedding_client.embed_single(content)
doc_id = await repo.store(content=content, embedding=embedding, ...)
```
to:
```python
doc_ids = await repo.store_chunked(content=content, embedder=embedding_client, ...)
```

Note the return type changed from `str` to `list[str]`. If a caller uses the return value (e.g., to return it in an HTTP response), return `doc_ids[0]` to preserve "the doc was created at this ID" semantics — the parent-key relationship is what makes this safe.

After all callers are migrated, delete the old `store()` method.

- [ ] **Step 7: Run the full backend unit suite**

```bash
./test.sh unit
```
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add api/src/repositories/knowledge.py api/tests/unit/repositories/test_knowledge_repository.py api/src/routers/ api/src/handlers/
git commit -m "feat(knowledge): chunk long content transparently in repository.store"
```

---

## Task 4: Repository — `search()` deduplicates by key ✅ COMPLETE

**Files:**
- Modify: `api/src/repositories/knowledge.py:132-225`
- Modify: `api/tests/unit/repositories/test_knowledge_repository.py` (extend)

- [ ] **Step 1: Add failing tests**

Append to `api/tests/unit/repositories/test_knowledge_repository.py`:

```python
@pytest.mark.asyncio
async def test_search_dedups_by_key_by_default(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder(dim=8)

    # Store one long doc (produces multiple chunks).
    long = ("Subject of interest. " * 500).strip()
    await repo.store_chunked(content=long, namespace="ns", key="big", embedder=embedder)
    # Store a few short docs.
    for i in range(3):
        await repo.store_chunked(
            content=f"Short doc {i}.", namespace="ns", key=f"small-{i}", embedder=embedder,
        )
    await db_session.flush()

    query_emb = await embedder.embed_single("anything")
    results = await repo.search(query_embedding=query_emb, namespace="ns", limit=5)

    # Default group_by_key=True: at most one result per (ns, org, key).
    keys = [r.key for r in results]
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_search_group_by_key_false_returns_raw_chunks(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder(dim=8)
    long = ("Subject of interest. " * 500).strip()
    await repo.store_chunked(content=long, namespace="ns", key="big", embedder=embedder)
    await db_session.flush()

    query_emb = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_emb, namespace="ns", limit=10, group_by_key=False
    )
    # All chunks of "big" can appear.
    assert len([r for r in results if r.key == "big"]) > 1


@pytest.mark.asyncio
async def test_search_metadata_filter_applies_to_chunks(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder(dim=8)
    long = ("Body text. " * 500).strip()
    await repo.store_chunked(
        content=long, namespace="ns", key="acme-doc",
        metadata={"client_id": "acme"}, embedder=embedder,
    )
    await repo.store_chunked(
        content=long, namespace="ns", key="other-doc",
        metadata={"client_id": "other"}, embedder=embedder,
    )
    await db_session.flush()

    query_emb = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_emb,
        namespace="ns",
        metadata_filter={"client_id": "acme"},
        limit=5,
    )
    assert all(r.metadata.get("client_id") == "acme" for r in results)
    assert any(r.key == "acme-doc" for r in results)
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
./test.sh tests/unit/repositories/test_knowledge_repository.py -v -k search
```
Expected: dedup test fails because `search()` currently returns duplicate keys.

- [ ] **Step 3: Modify `search()` to over-fetch and dedup**

In `api/src/repositories/knowledge.py`, modify the `search()` signature and body. Add `group_by_key: bool = True` to the signature. Replace lines 197-225 (`stmt = stmt.order_by(...)` through the end of the method) with:

```python
        stmt = stmt.order_by(score_expr.desc())
        # Over-fetch when grouping so dedup can still hit `limit` distinct
        # keys. 4× is a heuristic — enough for typical chunk_count<5 docs,
        # cheap because the cost is sequential-scan time, not network.
        raw_limit = limit * 4 if group_by_key else limit
        stmt = stmt.limit(raw_limit)

        result = await self.session.execute(stmt)
        rows = result.all()

        documents: list[KnowledgeDocument] = []
        seen_keys: set[tuple[str, str | None, str | None]] = set()
        for row in rows:
            doc = row[0]
            score = row[1]

            if min_score is not None and score < min_score:
                continue

            if group_by_key:
                # Dedup only by (ns, org_id, key) — rows with key=None are
                # genuinely independent docs, never dedup them against
                # each other.
                if doc.key is not None:
                    dedup_key = (doc.namespace, str(doc.organization_id) if doc.organization_id else None, doc.key)
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
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
./test.sh tests/unit/repositories/test_knowledge_repository.py -v
```
Expected: all tests green.

- [ ] **Step 5: Commit**

```bash
git add api/src/repositories/knowledge.py api/tests/unit/repositories/test_knowledge_repository.py
git commit -m "feat(knowledge): dedup search results by (ns,org,key) so chunked docs don't dominate"
```

---

## Task 5: Reindex — group by key, re-chunk + re-embed ✅ COMPLETE

**Files:**
- Modify: `api/src/services/embeddings/reindex.py:80-225` (the body of `run_reindex`)
- Modify (or create): `api/tests/unit/services/embeddings/test_reindex.py`

- [ ] **Step 1: Read the current reindex logic top-to-bottom**

```bash
cat api/src/services/embeddings/reindex.py
```
Note: it iterates by row ID, fetches `content`, calls `client.embed(texts)`, writes embeddings back. Progress is per-row. Cancellation is checked between batches. **We must preserve cancellation and progress reporting.**

- [ ] **Step 2: Write a failing integration test**

Create or extend `api/tests/unit/services/embeddings/test_reindex.py` with a test that seeds a legacy giant row and asserts reindex produces multiple chunk rows. Use the same `db_session` fixture pattern as Task 3 — if it doesn't exist, defer reindex unit tests to the e2e suite in Task 6.

```python
"""Reindex now re-chunks legacy giant rows."""
import pytest
from sqlalchemy import select, func

from src.models.orm.knowledge import KnowledgeStore
from src.services.embeddings.reindex import run_reindex_for_group


@pytest.mark.asyncio
async def test_reindex_rechunks_legacy_giant_row(db_session, monkeypatch):
    # Seed a legacy row: chunk_index=0, chunk_count=1, but content is huge.
    legacy = KnowledgeStore(
        namespace="halo_kb",
        organization_id=None,
        key="legacy-1",
        content=("Long article content. " * 500).strip(),  # ~10000 chars
        doc_metadata={"client_id": "acme"},
        embedding=[0.1] * 8,
        chunk_index=0,
        chunk_count=1,
    )
    db_session.add(legacy)
    await db_session.flush()

    class _Embedder:
        async def embed(self, texts):
            return [[0.2] * 8 for _ in texts]
        async def embed_single(self, text):
            return [0.2] * 8

    await run_reindex_for_group(
        db_session, _Embedder(),
        namespace="halo_kb", organization_id=None, key="legacy-1",
    )

    rows = (await db_session.execute(
        select(KnowledgeStore).where(KnowledgeStore.key == "legacy-1")
        .order_by(KnowledgeStore.chunk_index)
    )).scalars().all()
    assert len(rows) >= 4
    assert all(r.chunk_count == len(rows) for r in rows)
    # Metadata preserved on every chunk.
    assert all(r.doc_metadata == {"client_id": "acme"} for r in rows)
```

- [ ] **Step 3: Run the test, confirm it fails**

```bash
./test.sh tests/unit/services/embeddings/test_reindex.py -v
```
Expected: `ImportError` for `run_reindex_for_group`.

- [ ] **Step 4: Implement `run_reindex_for_group` and rewrite `run_reindex`**

In `api/src/services/embeddings/reindex.py`, add a helper for the per-group operation, then replace the body of `run_reindex` so it iterates by group instead of by row.

Add at module level (after existing imports):

```python
from src.repositories.knowledge import KnowledgeRepository
```

Add the helper:

```python
async def run_reindex_for_group(
    db, embedder, *, namespace: str, organization_id, key,
) -> int:
    """
    Re-chunk and re-embed every row under (namespace, organization_id, key).

    Concatenates the existing chunks (in chunk_index order), feeds the
    result through KnowledgeRepository.store_chunked which deletes the
    old chunks and inserts fresh ones with new embeddings. Returns the
    number of new chunks written.

    Designed to be called from `run_reindex` for each (ns, org, key) group;
    callers responsible for cancellation checks and progress updates.
    """
    rows = (await db.execute(
        select(KnowledgeStore)
        .where(
            KnowledgeStore.namespace == namespace,
            KnowledgeStore.key == key,
            (KnowledgeStore.organization_id == organization_id)
            if organization_id is not None
            else KnowledgeStore.organization_id.is_(None),
        )
        .order_by(KnowledgeStore.chunk_index)
    )).scalars().all()
    if not rows:
        return 0

    # Reassemble original content. If the rows are already chunked with
    # overlap, this duplicates the overlap regions — that's fine; the next
    # chunking pass will re-split cleanly and the overlap shrinks back to
    # the configured size. (One-off cost on the first reindex after the
    # chunking feature ships.)
    full_content = "".join(r.content for r in rows)
    metadata = rows[0].doc_metadata
    created_by = rows[0].created_by

    repo = KnowledgeRepository(db, org_id=organization_id, is_superuser=True)
    new_ids = await repo.store_chunked(
        content=full_content,
        namespace=namespace,
        key=key,
        metadata=metadata,
        organization_id=organization_id,
        created_by=created_by,
        embedder=embedder,
    )
    return len(new_ids)
```

Then replace the per-row loop in `run_reindex` (the section starting around line 130 with `batch_ids = row_ids[batch_start : ...]` and ending around line 200 with the per-row UPDATE) with a per-group loop. The exact rewrite:

```python
        # Replace row-id iteration with group-by-(ns, org, key).
        # Rows where key IS NULL are reindexed one row at a time
        # (they have no upsert key, so each row stands alone).

        groups_stmt = (
            select(
                KnowledgeStore.namespace,
                KnowledgeStore.organization_id,
                KnowledgeStore.key,
            )
            .where(KnowledgeStore.key.is_not(None))
            .distinct()
        )
        groups = (await db.execute(groups_stmt)).all()

        keyless_ids_stmt = select(KnowledgeStore.id).where(KnowledgeStore.key.is_(None))
        keyless_ids = [row[0] for row in (await db.execute(keyless_ids_stmt)).all()]

        total = len(groups) + len(keyless_ids)
        processed = 0
        failed_batches = 0
        total_batches = total  # one "batch" = one group or one keyless row

        for ns, org_id, k in groups:
            if await is_cancelled(notification_id):
                await notif_service.update_notification(
                    notification_id,
                    NotificationUpdate(
                        status=NotificationStatus.CANCELLED,
                        metadata_=cast(dict, {
                            "processed": processed,
                            "total": total,
                            "failed_batches": failed_batches,
                            "total_batches": total_batches,
                            "cancelled": True,
                        }),
                    ),
                )
                return

            try:
                await run_reindex_for_group(
                    db, client,
                    namespace=ns, organization_id=org_id, key=k,
                )
                await db.commit()
                processed += 1
            except Exception as e:
                failed_batches += 1
                logger.error(
                    f"Reindex group ({ns}, {org_id}, {k}) failed: {e}"
                )
                # Roll back this group's partial work, keep going.
                await db.rollback()

            await _push_progress(notif_service, notification_id, processed, total)

        # Handle keyless rows individually — same per-row update as before.
        for row_id in keyless_ids:
            if await is_cancelled(notification_id):
                # (same cancellation block as above — extract a helper if you prefer)
                ...
            row = (await db.execute(
                select(KnowledgeStore).where(KnowledgeStore.id == row_id)
            )).scalar_one_or_none()
            if row is None:
                continue
            try:
                new_emb = await client.embed_single(row.content)
                row.embedding = new_emb
                await db.commit()
                processed += 1
            except Exception as e:
                failed_batches += 1
                logger.error(f"Reindex keyless row {row_id} failed: {e}")
                await db.rollback()
            await _push_progress(notif_service, notification_id, processed, total)
```

Note: the existing function has a lot of setup (notification fetching, cancellation flag init, embedder construction) above the per-row loop — leave that intact. Only swap the iteration logic. Read the file end-to-end before editing.

- [ ] **Step 5: Run reindex tests + full backend unit suite**

```bash
./test.sh tests/unit/services/embeddings/test_reindex.py -v
./test.sh unit
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/embeddings/reindex.py api/tests/unit/services/embeddings/test_reindex.py
git commit -m "feat(knowledge): reindex re-chunks legacy giant rows by key"
```

---

## Task 6: E2E — store long doc, search, verify chunks + dedup + metadata filter ✅ COMPLETE

**Files:**
- Modify (or create): `api/tests/e2e/test_knowledge_search.py`

- [ ] **Step 1: Find the existing knowledge e2e tests for context**

```bash
find api/tests/e2e -name "*knowledge*"
```
If a file exists, read it to learn the fixture patterns (auth, client, cleanup). If not, scaffold from a sibling e2e test.

- [ ] **Step 2: Write the e2e test**

```python
"""E2E: chunking is transparent end-to-end via the HTTP API."""
import pytest


@pytest.mark.asyncio
async def test_long_doc_stored_and_searched_returns_chunked_content(
    api_client, auth_headers,
):
    long_content = (
        "# Resetting MFA\n\n"
        + ("Detailed MFA reset instructions step by step. " * 80)
        + "\n\n# Resetting Password\n\n"
        + ("Detailed password reset instructions. " * 80)
        + "\n\n# Unlocking AD Account\n\n"
        + ("Detailed AD unlock instructions. " * 80)
    )

    create_resp = await api_client.post(
        "/api/knowledge/documents",
        json={
            "namespace": "e2e-chunking",
            "key": "article-1",
            "content": long_content,
            "metadata": {"client_id": "acme", "doc_type": "runbook"},
        },
        headers=auth_headers,
    )
    assert create_resp.status_code in (200, 201)

    # Search for one of the sections.
    search_resp = await api_client.post(
        "/api/knowledge/search",
        json={"namespace": "e2e-chunking", "query": "how do I unlock an AD account", "limit": 5},
        headers=auth_headers,
    )
    assert search_resp.status_code == 200
    results = search_resp.json()["results"]
    assert len(results) >= 1

    # Each result is a chunk, not the whole doc. Whole doc ≈ 10000 chars.
    # Chunks are ≤ ~2200 chars (target + overlap).
    assert all(len(r["content"]) < 4000 for r in results)
    # The most relevant chunk should mention AD unlock.
    top_content = results[0]["content"].lower()
    assert "unlock" in top_content or "ad" in top_content

    # Metadata filter still works — chunks inherit parent metadata.
    filtered = await api_client.post(
        "/api/knowledge/search",
        json={
            "namespace": "e2e-chunking",
            "query": "anything",
            "limit": 5,
            "metadata_filter": {"client_id": "acme"},
        },
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    assert all(
        r["metadata"]["client_id"] == "acme"
        for r in filtered.json()["results"]
    )


@pytest.mark.asyncio
async def test_search_dedupes_by_key(api_client, auth_headers):
    long_content = ("Body content sentence. " * 500).strip()
    await api_client.post(
        "/api/knowledge/documents",
        json={
            "namespace": "e2e-dedup",
            "key": "single-doc",
            "content": long_content,
            "metadata": {},
        },
        headers=auth_headers,
    )

    resp = await api_client.post(
        "/api/knowledge/search",
        json={"namespace": "e2e-dedup", "query": "body", "limit": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    # `single-doc` produced multiple chunks but dedup means at most one
    # result per key.
    assert len([r for r in results if r.get("key") == "single-doc"]) <= 1
```

You may need to adapt request/response shapes to the actual endpoints — check `api/src/routers/knowledge.py` (or wherever `/api/knowledge/documents` lives) for the exact contract. If the endpoint doesn't accept a `content` field directly (some routers use `text` or `body`), match the existing contract. Do not change the router signature.

- [ ] **Step 3: Run the e2e tests**

```bash
./test.sh stack up
./test.sh e2e tests/e2e/test_knowledge_search.py -v
```
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add api/tests/e2e/test_knowledge_search.py
git commit -m "test(knowledge): e2e for chunked storage + search dedup + metadata filter"
```

---

## Task 7: Verify against the real halo_kb size profile

**Files:** None (verification only).

- [ ] **Step 1: Confirm the size query baseline**

The pre-change baseline (from the user's earlier prod query) was:

| namespace | docs | avg_chars | p95_chars |
|---|---|---|---|
| halo_kb | 1328 | 11842 | 23959 |

Document this in the PR description as the "before" measurement.

- [ ] **Step 2: After deploying + clicking "reindex"**

Re-run the same SQL against prod (or against a dev instance seeded with a representative halo_kb sample):

```sql
SELECT
  namespace,
  COUNT(*) AS docs,
  AVG(LENGTH(content))::int AS avg_chars,
  MAX(LENGTH(content)) AS max_chars,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY LENGTH(content))::int AS p50_chars,
  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY LENGTH(content))::int AS p95_chars,
  SUM(LENGTH(content)) / 4 AS approx_total_tokens
FROM knowledge_store
WHERE namespace = 'halo_kb'
GROUP BY namespace;
```

Expected post-reindex: `avg_chars` ≈ 1800-2200, `p95_chars` ≤ ~2400, `docs` count grows ~5-6×. If `avg_chars` is still >5000, the splitter isn't running on legacy rows — confirm the reindex job actually ran (notification status, worker logs).

- [ ] **Step 3: Spot-check one agent run**

Pick a recent `search_knowledge`-heavy agent run from prod, count tokens in its tool-call results, and confirm the per-call result size dropped from ~30K to ~3K. This is the actual user-visible win.

---

## Self-Review

**Spec coverage:** every requirement from the conversation maps to a task. Schema → Task 1. Chunking logic → Task 2. `store()` chunks transparently → Task 3. `search()` dedups → Task 4. Reindex re-chunks legacy rows → Task 5. End-to-end verification → Tasks 6, 7. Metadata filtering preserved → covered by Task 3 (copied to each chunk), Task 4 (filter test), Task 6 (e2e test).

**Placeholder scan:** the Task 5 cancellation block has `# (same cancellation block as above — extract a helper if you prefer)` which is borderline — but the block is fully spelled out earlier in the same task so the engineer can copy it. Acceptable.

**Type consistency:** `store_chunked` returns `list[str]`, callers updated to `doc_ids[0]` in Task 3 step 6. `search()` `group_by_key: bool = True` added consistently. `KnowledgeDocument` shape unchanged so MCP tool code in `api/src/services/mcp_server/tools/knowledge.py` doesn't need changes.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-knowledge-store-chunking.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
