# Unified File Index + Paged Source Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `bifrost files search` (CLI, SDK, MCP, editor UI) becomes one correct, paged search over every source file the platform holds — loose `_repo/` files and every Solution install's source — fed by one indexer with one policy.

**Architecture:** `FileIndexService` is the only code that writes `file_index` (workspace) or the new `solution_file_index` (Solution source). One policy decides what is indexed. `src/services/source_search/` is the only search implementation. It prefilters candidates in SQL in deterministic `(source, scope, path)` order, matches lines in Python off the event loop, and returns a small page plus an opaque keyset cursor and a plain-language `guidance` sentence. REST `/api/files/search` is the single entry point; the CLI, SDK, MCP `bifrost_file_search` and the editor are clients of it. The reconciler platform job becomes a real healer: it compares hashes, refreshes stale rows and removes orphans.

**Tech Stack:** FastAPI, SQLAlchemy Core (async), PostgreSQL (optional `pg_trgm` GIN accelerator), Alembic, Click, FastMCP, React + openapi-react-query, vitest, Playwright.

**Spec:** This plan's "Investigation findings" and "Design decisions" sections (below), from the 2026-10-01 investigation with Jack. Evidence: prod search for `halo` saw 176 of 1,026 matching files; 296 reconciler runs since 2026-08-07 all reported zero changes.

## Investigation findings (the problems this fixes)

1. `api/src/services/editor/search.py` loads at most 500 `file_index` rows with no `ORDER BY`, then matches in Python. With 2,360 prod files, most of the workspace is never searched, and `truncated` stays false.
2. The glob→`LIKE` translation is wrong: `*` crosses `/`, `**/x` misses root, `_` is a wildcard, braces are unsupported.
3. `total_matches` is counted after truncation. There is no pagination. CLI human output is one Python-dict line.
4. There are two search implementations. MCP `search_content` (`tools/code_editor.py:346`) loads every row on each call. The catalog declares `bifrost_file_search`, but nothing registers it.
5. There are four indexing policies:
   - `file_ops.write_file` indexes everything, with binary stored as `""`.
   - `FileIndexService.write` and `github_sync` use an extension allowlist (no `.json`, `.ps1`, extensionless files).
   - The reconciler uses the allowlist with no size cap.
   - Signed uploads write `content=None` forever.
6. `ref_rewriter.py:186`, `auto_migrate.py:93`, `folder_ops.py:73` and `requirements_cache.py:211` write `_repo/` without touching the index, so it goes stale.
7. The reconciler never compares hashes, never removes orphans, and "reverse-syncs" (resurrects) out-of-band deletes back into S3.
8. Solution source (`_solutions/{id}/`, 886 `.py` files in prod) is not indexed at all.

## Design decisions (confirmed with Jack)

- One indexer and one search system. A small default page (25) with a cursor that an AI can act on from the response alone.
- By default, search covers workspace source **and** all Solution source. Hits say where they live and whether they are editable. `solution_id` narrows to one install. `source` can restrict to `workspace` or `solutions`.
- `pg_trgm` is installed by migration, like `vector`. It is an accelerator only: the query is identical with or without it. If the extension cannot be created, the migration logs a warning and skips the GIN indexes (Jack's explicit "don't break search" requirement).
- Solution source lives in a separate table, `solution_file_index`, owned by the same service. About 20 existing `file_index` readers key by `path` alone, and mixed rows would let them resolve a Solution file as a workspace file.
- MCP: `bifrost_file_search` (a thin REST wrapper) replaces `search_content`. A data migration renames the tool in `agents.system_tools`; prod's "Coding Agent" uses it.
- SDK/CLI: `max_results` is replaced by `limit` and `cursor`. Prod has no workflow or Solution that calls `files.search(`. Old CLIs keep working because the server ignores the unknown `max_results` and the old CLI renders the response dict generically.

## Global Constraints

- Default `limit` = 25; maximum `limit` = 200. `context_lines` default 1, range 0–5.
- `MAX_INDEXABLE_TEXT_BYTES = 8 * 1024 * 1024` stays the single size cap.
- `MAX_LINE_CHARS = 400`: returned match/context lines longer than this are windowed around the match with `…`.
- Search auth stays `CurrentSuperuser` (unchanged).
- Datetimes: `datetime.now(timezone.utc)` + `DateTime(timezone=True)`.
- No dead code: delete `editor/search.py`, `FileIndexService.search`, `TEXT_EXTENSIONS`/`_is_text_file`, `MAX_RESULTS_PER_TYPE`, the MCP `search_content` tool and the reconciler's reverse-sync in the same change that replaces them.
- All index writes use SQLAlchemy Core `insert`/`delete` (never ORM objects). This keeps the Solution `before_flush` guard out of the way.
- Tests run through `./test.sh` (never host pytest). The per-worktree JUnit is at `/tmp/bifrost-<project>/test-results.xml`.

## Review Focus

1. **A cursor reused with a different query or filters**: expect a 400 with "cursor does not belong to this search — start without cursor". Never return silently wrong results. (Task 5 test `test_cursor_rejected_for_different_query`.)
2. **A file edited between page 1 and page 2**: pages must not repeat or skip earlier files. Keyset position `(rank, scope, path, line, column)` resumes strictly after the last returned hit. (Task 5 test `test_page_two_resumes_after_last_hit_when_earlier_file_changes`.)
3. **A minified one-line 2 MB JS file that matches**: the response must stay small. `text` is windowed to `MAX_LINE_CHARS`. (Task 5 test `test_long_line_is_windowed`.)
4. **A 1–2 character query, or a regex with no literal**: there is no trigram prefilter, but results must be identical and memory bounded (chunked content fetch). (Task 5 test `test_regex_query_scans_without_sql_prefilter`.)
5. **A Solution and the workspace both have `functions/foo.py`**: both appear, labelled distinctly, and the workspace row is never shadowed. (Task 5 test `test_same_relative_path_in_workspace_and_solution`.)
6. Accepted limitation: `ILIKE` and Python `re.IGNORECASE` differ for a few non-ASCII case folds (e.g. `ß`), so the case-insensitive literal prefilter can miss those rare matches. This is documented in the request field description.

---

## File Structure

| File | Responsibility |
|---|---|
| `api/src/services/file_index_service.py` (modify) | **The only index writer.** Policy (`indexable_text`, `is_tracked_path`), workspace row ops, Solution row ops, `write`/`delete` (S3 + index). |
| `api/src/models/orm/file_index.py` (modify) | Add `SolutionFileIndex` ORM model beside `FileIndex`. |
| `api/alembic/versions/20261001_unified_file_search.py` (create) | `solution_file_index` table; tolerant `pg_trgm` + GIN indexes; `search_content` → `bifrost_file_search` in `agents.system_tools`. |
| `api/shared/path_glob.py` (create) | ripgrep/gitignore-style glob compiler + literal-prefix extractor. Pure. |
| `api/src/services/source_search/__init__.py` (create) | `search_source(db, request) -> SearchResponse` entry point. |
| `api/src/services/source_search/cursor.py` (create) | `SearchPosition`, fingerprint, encode/decode. |
| `api/src/services/source_search/matching.py` (create) | Pure line matching, context, line windowing. |
| `api/src/services/source_search/candidates.py` (create) | SQL candidate paging over both index tables + chunked content fetch. |
| `api/src/services/source_search/guidance.py` (create) | Transport-neutral guidance sentence. |
| `api/src/models/contracts/editor.py` (modify) | New `SearchRequest`, `SearchSource`, `SearchMatch`, `SearchFileHit`, `SearchResponse`. |
| `api/src/routers/files.py` (modify) | `/search` calls `search_source`; cursor errors → 400. |
| `api/src/services/file_index_reconciler.py` (rewrite) | Hash-comparing healer for workspace + Solutions; no reverse-sync. |
| `api/src/services/repo_storage.py` (modify) | `S3FileMetadata.size`; `head(path)`. |
| `api/src/services/solutions/storage.py` (modify) | `list_with_metadata()`, `content_hash()` mirroring `RepoStorage`. |
| `api/src/services/solutions/deploy.py` (modify) | `_write_python` indexes and unindexes Solution source. |
| Direct writers (modify) | `file_ops.py`, `folder_ops.py`, `ref_rewriter.py`, `auto_migrate.py`, `requirements_cache.py`, `github_sync.py`, `routers/files.py` (signed-upload completion). |
| `api/bifrost/files.py`, `api/bifrost/commands/files.py` (modify) | SDK signature; CLI flags, grep-style output and copy-pasteable next-page command. |
| `api/src/services/mcp_server/tools/code_editor.py` (modify) | Replace `search_content` with thin `bifrost_file_search`. |
| `client/src/components/editor/SearchPanel.tsx`, `client/src/components/quick-access/QuickAccess.tsx` (modify) | New response shape; "Load more". |
| Tests | Listed per task. |

---

### Task 1: Single index policy + Solution index table + migration

**Files:**
- Modify: `api/src/services/file_index_service.py`
- Modify: `api/src/models/orm/file_index.py`, `api/src/models/orm/__init__.py`, `api/src/models/_exports.py` (export `SolutionFileIndex` next to `FileIndex`)
- Create: `api/alembic/versions/20261001_unified_file_search.py`
- Test: `api/tests/unit/test_file_index_service.py` (extend), `api/tests/unit/test_file_index_model.py` (extend)

**Interfaces:**
- Produces:
  - `is_tracked_path(path: str) -> bool`
  - `indexable_text(content: bytes) -> str | None`
  - `FileIndexService.index(path: str, content: bytes, content_hash: str, updated_by: str | None = None) -> None`
  - `FileIndexService.index_path_only(path: str, content_hash: str, updated_by: str | None = None) -> None`
  - `FileIndexService.unindex(path: str) -> None`
  - `FileIndexService.move_index(old_path: str, new_path: str) -> None`
  - `FileIndexService.index_solution(solution_id: UUID, path: str, content: bytes, content_hash: str) -> None`
  - `FileIndexService.index_solution_path_only(solution_id: UUID, path: str, content_hash: str) -> None`
  - `FileIndexService.unindex_solution(solution_id: UUID, path: str) -> None`
  - The existing `write(path, content, updated_by) -> str` and `delete(path)` keep their signatures and call the above.
  - ORM `SolutionFileIndex(solution_id, path, content, content_hash, updated_at)`.

- [ ] **Step 1: Write the failing policy tests** in `api/tests/unit/test_file_index_service.py`:

```python
import pytest

from src.services.file_index_service import (
    MAX_INDEXABLE_TEXT_BYTES,
    indexable_text,
    is_tracked_path,
)


@pytest.mark.parametrize("content", [b'{"a": 1}', b"Write-Host hi\r\n", b"FROM python:3.11\n"])
def test_any_utf8_text_is_indexed_regardless_of_extension(content):
    assert indexable_text(content) == content.decode("utf-8")


def test_utf8_bom_is_stripped():
    assert indexable_text(b"\xef\xbb\xbfhello") == "hello"


@pytest.mark.parametrize(
    "content",
    [b"PK\x03\x04\x00\x00", b"\xff\xfe\xfa", b"a" * (MAX_INDEXABLE_TEXT_BYTES + 1)],
)
def test_binary_invalid_or_oversized_is_path_only(content):
    assert indexable_text(content) is None


def test_empty_file_is_indexed_as_empty_text():
    assert indexable_text(b"") == ""


@pytest.mark.parametrize("path", [".git/HEAD", "node_modules/x/index.js", "a/__pycache__/m.pyc", ".env"])
def test_excluded_paths_are_not_tracked(path):
    assert is_tracked_path(path) is False


@pytest.mark.parametrize("path", ["workflows/x.py", "data/config.json", "scripts/Fix.ps1", "Dockerfile"])
def test_source_paths_are_tracked(path):
    assert is_tracked_path(path) is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./test.sh tests/unit/test_file_index_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'indexable_text'`

- [ ] **Step 3: Implement the policy and row operations.** In `file_index_service.py`, delete `TEXT_EXTENSIONS` and `_is_text_file`, and add:

```python
from uuid import UUID

from src.models.orm.file_index import FileIndex, SolutionFileIndex
from src.services.editor.file_filter import is_excluded_path

MAX_INDEXABLE_TEXT_BYTES = 8 * 1024 * 1024


def is_tracked_path(path: str) -> bool:
    """Every _repo/ or Solution source object gets an index row unless excluded."""
    return not is_excluded_path(path)


def indexable_text(content: bytes) -> str | None:
    """Return searchable text, or None for a path-only row (binary/invalid/oversized)."""
    if len(content) > MAX_INDEXABLE_TEXT_BYTES or b"\x00" in content:
        return None
    try:
        return content.decode("utf-8").removeprefix("﻿")
    except UnicodeDecodeError:
        return None
```

Then add these methods to `FileIndexService`:
- `index` upserts `FileIndex(path, content=indexable_text(content), content_hash, updated_by, updated_at=NOW())` with `on_conflict_do_update(index_elements=[FileIndex.path])`. When `is_tracked_path(path)` is false it calls `unindex(path)` instead.
- `index_path_only` is the same upsert with `content=None`.
- `unindex` runs `delete(FileIndex).where(FileIndex.path == path)`.
- `move_index` copies the row to `new_path` (upsert) and then deletes `old_path`.
- The `*_solution` variants do the same against `SolutionFileIndex`, with `index_elements=[SolutionFileIndex.solution_id, SolutionFileIndex.path]`.

Rewrite `write()` as: S3 write → `await self.index(path, content, content_hash, updated_by)` → `_invalidate_python_module_cache(path)` when the content is not indexable (current behaviour for skipped files). Rewrite `write_file()` so that the non-indexable branch calls `index_path_only` instead of deleting the row. `delete()` calls `unindex`. Delete the `search()` method; first run `grep -rn "\.search(" api/src | grep -i index` to confirm there are no callers.

- [ ] **Step 4: Add the ORM model** to `api/src/models/orm/file_index.py`:

```python
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID


class SolutionFileIndex(Base):
    """Search index for Solution install source in _solutions/{solution_id}/."""

    __tablename__ = "solution_file_index"

    solution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("solutions.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(String(1000), primary_key=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

- [ ] **Step 5: Write the migration** `api/alembic/versions/20261001_unified_file_search.py`:

```python
"""Unified source search: solution_file_index, optional pg_trgm, MCP tool rename.

Revision ID: 20261001_unified_file_search
Revises: 20260929_user_base_perm_fix
Create Date: 2026-10-01

pg_trgm is an accelerator only. Search issues the same ILIKE/LIKE either way,
so hosts that cannot create the extension keep identical results (seq scan).
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261001_unified_file_search"
down_revision = "20260929_user_base_perm_fix"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.create_table(
        "solution_file_index",
        sa.Column("solution_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("solutions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("path", sa.String(1000), primary_key=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.execute(
        "UPDATE agents SET system_tools = array_replace(system_tools, 'search_content', 'bifrost_file_search') "
        "WHERE 'search_content' = ANY(system_tools)"
    )
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except sa.exc.DBAPIError as exc:
        logger.warning(
            "pg_trgm unavailable (%s); source search works without the trigram "
            "index but scans sequentially", exc.orig,
        )
        return
    op.execute("CREATE INDEX IF NOT EXISTS ix_file_index_content_trgm ON file_index USING gin (content gin_trgm_ops)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_solution_file_index_content_trgm "
        "ON solution_file_index USING gin (content gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_file_index_content_trgm")
    op.drop_table("solution_file_index")
    op.execute(
        "UPDATE agents SET system_tools = array_replace(system_tools, 'bifrost_file_search', 'search_content') "
        "WHERE 'bifrost_file_search' = ANY(system_tools)"
    )
```

The downgrade leaves the extension installed on purpose: other objects may depend on it.

- [ ] **Step 6: Apply the migration on the debug stack and verify both branches**

Run: `docker restart bifrost-debug-09aa8aac-init-1 && docker logs -f bifrost-debug-09aa8aac-init-1 2>&1 | grep -m1 -E "20261001|Error"` then `docker restart bifrost-debug-09aa8aac-api-1`.
Then: `docker exec bifrost-debug-09aa8aac-postgres-1 psql -U bifrost -d bifrost -c "\di *trgm*"`
Expected: both `*_content_trgm` indexes listed.
Prove the tolerant branch: `docker exec ... psql -c "DROP INDEX ix_file_index_content_trgm; DROP INDEX ix_solution_file_index_content_trgm; DROP EXTENSION pg_trgm"`. Run the migration's `upgrade()` body against a role without CREATE privilege (`CREATE ROLE nocreate LOGIN PASSWORD 'x'; GRANT USAGE ON SCHEMA public TO nocreate;`) and confirm the warning is logged and the transaction still commits. Then restore with a normal `alembic downgrade -1 && alembic upgrade head`.

- [ ] **Step 7: Run the tests**

Run: `./test.sh tests/unit/test_file_index_service.py tests/unit/test_file_index_model.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add api/src/services/file_index_service.py api/src/models/orm/file_index.py api/src/models/orm/__init__.py api/src/models/_exports.py api/alembic/versions/20261001_unified_file_search.py api/tests/unit/test_file_index_service.py api/tests/unit/test_file_index_model.py
git commit -m "feat(files): single index policy + solution source index table"
```

---

### Task 2: Route every writer through the indexer + single-writer guard test

**Files:**
- Modify: `api/src/services/file_storage/file_ops.py` (write ~L178-210, `record_signed_upload_metadata` ~L320, `_remove_from_search_index` ~L405, `move_file` ~L640-716)
- Modify: `api/src/services/file_storage/folder_ops.py:73`, `api/src/services/solutions/ref_rewriter.py:186`, `api/src/services/app_bundler/auto_migrate.py:93`, `api/src/core/requirements_cache.py:211`, `api/src/dev/scheduler_fixtures.py:201`
- Modify: `api/src/services/github_sync.py` `_update_file_index` (~L1819)
- Modify: `api/src/routers/files.py` `_record_completed_signed_upload` (~L685)
- Modify: `api/src/services/solutions/deploy.py` `_write_python` (~L797)
- Modify: `api/src/services/repo_storage.py` (`S3FileMetadata.size`, `head`)
- Create: `api/tests/unit/test_file_index_single_writer.py`
- Test: extend `api/tests/unit/test_solution_deploy_reconcile.py` (or the nearest `_write_python` test) and `api/tests/e2e/platform/test_storage_integrity.py`

**Interfaces:**
- Consumes: Task 1 `FileIndexService` methods.
- Produces:
  - `RepoStorage.head(path) -> S3FileMetadata | None`
  - `S3FileMetadata.size: int`
  - `FileIndexService.index_existing_object(path: str, updated_by: str | None) -> None`. It reads the object if `size <= MAX_INDEXABLE_TEXT_BYTES`, otherwise streams `content_hash` and writes a path-only row. Both signed-upload completion and the reconciler use it.

- [ ] **Step 1: Write the guard test** `api/tests/unit/test_file_index_single_writer.py`:

```python
"""file_index / solution_file_index have exactly one writer: FileIndexService."""

import re
from pathlib import Path

API = Path(__file__).resolve().parents[2]
WRITE = re.compile(r"\b(insert|delete|update)\(\s*(FileIndex|SolutionFileIndex)\b")
ALLOWED = {"src/services/file_index_service.py"}


def test_only_file_index_service_writes_index_tables():
    offenders = []
    for root in ("src", "shared"):
        for path in (API / root).rglob("*.py"):
            rel = path.relative_to(API).as_posix()
            if rel not in ALLOWED and WRITE.search(path.read_text(encoding="utf-8")):
                offenders.append(rel)
    assert offenders == [], f"route index writes through FileIndexService: {offenders}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/unit/test_file_index_single_writer.py -v`
Expected: FAIL listing `file_ops.py`, `github_sync.py`, `file_index_reconciler.py`, and any others found.

- [ ] **Step 3: Convert each writer**
  - `file_ops.write_file`: keep the S3 `put_object` (it sets ContentType). Replace the `insert(FileIndex)` block with `await FileIndexService(self.db).index(path, content, content_hash, updated_by)`. This drops the `errors="replace"` decode and the `""`-for-binary rule.
  - `record_signed_upload_metadata`: remove it from `file_ops`. The signing-time marker is no longer needed, because completion indexes the real object. In `_record_completed_signed_upload`, call `await FileIndexService(db).index_existing_object(path, updated_by=user.email)` for `location == "workspace"`.
  - `_remove_from_search_index` → `FileIndexService(self.db).unindex(path)`.
  - `move_file` → `FileIndexService(self.db).move_index(old_path, new_path)`.
  - `folder_ops.create_folder`, `ref_rewriter._rewrite_sources`, `auto_migrate_repo_prefix`, `requirements_cache.save_requirements`, `scheduler_fixtures`: replace `RepoStorage().write(p, b)` with `await FileIndexService(db).write(p, b)`. Where no `db` is in scope (`auto_migrate_repo_prefix`, `save_requirements`), add a `db: AsyncSession` parameter and pass it from every caller (`grep -rn "auto_migrate_repo_prefix\|save_requirements(" api/src`).
  - `github_sync._update_file_index`: keep the prefetch + batching. For each entry, skip it if `not is_tracked_path(rel_path)`. If `entry.size <= MAX_INDEXABLE_TEXT_BYTES`, read the bytes and build the row with `content=indexable_text(b)`; otherwise use `content=None`. Always use `content_hash=entry.sha256`. Flush via a new `FileIndexService.upsert_many(rows: list[dict]) -> None` (add it in `file_index_service.py`), and delete stale rows via `FileIndexService.unindex_many(paths)`.
  - `deploy._write_python`: after `storage.write(...)`, call `await FileIndexService(self.db).index_solution(sid, rel_path, content.encode("utf-8"), content_hash)`. After `storage.delete(rel_path)`, call `unindex_solution(sid, rel_path)`.

- [ ] **Step 4: Add `S3FileMetadata.size` and `RepoStorage.head`.** Populate `size=obj["Size"]` in `_list_with_metadata_from_s3`. Implement `head()` with `head_object` → `S3FileMetadata(etag, last_modified, size=resp["ContentLength"])`, returning None on 404. Fix every other `S3FileMetadata(` constructor (`grep -rn "S3FileMetadata(" api`).

- [ ] **Step 5: Add regression tests for the formerly-bypassing writers.** In `api/tests/unit/test_file_index_service.py`, using the DB-session fixture other DB-backed unit tests use (`grep -rln "db_session" api/tests/unit | head -3`) and a fake `RepoStorage` with an `objects: dict[str, bytes]`:

```python
async def test_ref_rewriter_updates_index_content(db_session, fake_repo):
    from src.services.solutions.ref_rewriter import WorkflowRename, WorkflowRefRewriter

    await FileIndexService(db_session, fake_repo).write("forms/f.py", b"run('old.py::main')\n")
    rewriter = WorkflowRefRewriter(db_session, repo=fake_repo)
    await rewriter._rewrite_sources([WorkflowRename(old_ref="old.py::main", new_ref="new.py::main")], ["forms/f.py"])
    row = await db_session.scalar(select(FileIndex.content).where(FileIndex.path == "forms/f.py"))
    assert "new.py::main" in row


async def test_write_python_indexes_and_unindexes_solution_source(db_session, solution_row, fake_solution_storage):
    deployer = make_deployer(db_session, storage=fake_solution_storage)  # existing helper in test_solution_deploy_reconcile.py
    await deployer._write_python(solution_row.id, {"functions/a.py": "A = 1\n", "functions/b.py": "B = 1\n"})
    await deployer._write_python(solution_row.id, {"functions/a.py": "A = 2\n"})
    rows = dict((await db_session.execute(
        select(SolutionFileIndex.path, SolutionFileIndex.content).where(SolutionFileIndex.solution_id == solution_row.id)
    )).all())
    assert rows == {"functions/a.py": "A = 2\n"}
```

Use the real class/dataclass names from `ref_rewriter.py` (`grep -n "^class\|@dataclass" api/src/services/solutions/ref_rewriter.py`) and the deployer construction helper already used in `test_solution_deploy_reconcile.py`. Adjust the two constructor lines to match; the assertions stay as written.

- [ ] **Step 6: Run the tests**

Run: `./test.sh tests/unit/test_file_index_single_writer.py tests/unit/test_file_index_service.py tests/unit/test_solution_deploy_reconcile.py -v`
Expected: PASS (the guard may still list `file_index_reconciler.py` until Task 3; if so, add it to `ALLOWED` temporarily **in this commit only**, with Task 3 removing it).

- [ ] **Step 7: Commit**

```bash
git commit -am "fix(files): route every _repo and solution source write through FileIndexService"
```

---

### Task 3: Reconciler becomes a real healer (workspace + Solutions)

**Files:**
- Rewrite: `api/src/services/file_index_reconciler.py`
- Modify: `api/src/services/solutions/storage.py` (`list_with_metadata`, `content_hash`)
- Modify: `api/src/jobs/platform/system_maintenance.py:92-108` (log message for the new stats shape)
- Test: rewrite `api/tests/unit/test_file_index_reconciler.py`

**Interfaces:**
- Consumes: `FileIndexService` (Tasks 1–2), `RepoStorage.list_with_metadata`/`content_hash`, `SolutionStorage.list_with_metadata`/`content_hash`/`read`.
- Produces: `reconcile_file_index(db, repo_storage=None, solution_storage_factory=SolutionStorage) -> dict`, returning `{"workspace": {"added","updated","removed","unchanged"}, "solutions": {...same...}}`.

- [ ] **Step 1: Write the failing tests** using in-memory fakes (follow the fake-storage pattern already in `test_file_index_reconciler.py`):

```python
async def test_stale_row_is_refreshed(db, fake_repo):
    fake_repo.objects["workflows/a.py"] = b"NEW = 1\n"
    await seed_file_index(db, "workflows/a.py", content="OLD = 1\n", content_hash="0" * 64)
    stats = await reconcile_file_index(db, repo_storage=fake_repo, solution_storage_factory=no_solutions)
    assert stats["workspace"]["updated"] == 1
    assert await index_content(db, "workflows/a.py") == "NEW = 1\n"


async def test_orphan_row_is_removed_and_never_written_back(db, fake_repo):
    await seed_file_index(db, "gone.txt", content="x", content_hash="1" * 64)
    stats = await reconcile_file_index(db, repo_storage=fake_repo, solution_storage_factory=no_solutions)
    assert stats["workspace"]["removed"] == 1
    assert "gone.txt" not in fake_repo.objects


async def test_json_and_binary_get_rows(db, fake_repo):
    fake_repo.objects["data/c.json"] = b'{"k": 1}'
    fake_repo.objects["img/logo.png"] = b"\x89PNG\x00\x00"
    await reconcile_file_index(db, repo_storage=fake_repo, solution_storage_factory=no_solutions)
    assert await index_content(db, "data/c.json") == '{"k": 1}'
    assert await index_row_exists(db, "img/logo.png") and await index_content(db, "img/logo.png") is None


async def test_solution_source_is_indexed(db, fake_solution_storage, solution_id):
    fake_solution_storage.objects["functions/x.py"] = b"def x(): ...\n"
    stats = await reconcile_file_index(db, repo_storage=FakeRepo(), solution_storage_factory=lambda sid: fake_solution_storage)
    assert stats["solutions"]["added"] == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./test.sh tests/unit/test_file_index_reconciler.py -v`
Expected: FAIL (old stats shape; no `updated`; the orphan is reverse-synced).

- [ ] **Step 3: Implement.** For each scope (the workspace; then each `solutions.id` from `select(Solution.id)`):
  1. `listing = await storage.list_with_metadata("")`, filtered by `is_tracked_path`.
  2. `rows = {path: content_hash}` from the matching index table. Select only path + hash, never content.
  3. For each listed path, one object at a time:
     - If `meta.size <= MAX_INDEXABLE_TEXT_BYTES`, read the bytes, compute `h = sha256(b)`, and skip if `h == rows.get(path)` (unchanged). Otherwise upsert via the `FileIndexService` method (added or updated).
     - If it is over the cap, compute `h = await storage.content_hash(path)` and do a path-only upsert when it differs.
  4. `unindex` / `unindex_solution` each row path not in the listing (removed).
  5. `await db.commit()` per scope, so a failure in one scope doesn't discard the others.

  Delete the reverse-sync code entirely. Then remove `file_index_reconciler.py` from the guard test `ALLOWED` if Task 2 added it.

- [ ] **Step 4: Update the job log line** in `run_file_index_reconciliation` to report both scopes (`workspace: +a ~u -r, solutions: +a ~u -r`).

- [ ] **Step 5: Run the tests**

Run: `./test.sh tests/unit/test_file_index_reconciler.py tests/unit/test_file_index_single_writer.py -v`
Expected: PASS

- [ ] **Step 6: Live check** on the debug stack. Re-run the investigation repro (`/tmp/claude-1000/.../scratchpad/stale.py`): `reconcile_file_index` must now report `updated: 1` for `workflows/top.py` and `removed: 1` for `my_x.txt`, and S3 must still not contain `my_x.txt`.

- [ ] **Step 7: Commit**

```bash
git commit -am "fix(files): reconciler compares hashes, removes orphans, indexes solution source"
```

---

### Task 4: Glob compiler

**Files:**
- Create: `api/shared/path_glob.py`
- Test: `api/tests/unit/test_path_glob.py`

**Interfaces:**
- Produces:
  - `compile_glob(pattern: str) -> re.Pattern[str]`
  - `glob_matches(compiled: re.Pattern[str], path: str) -> bool` (true if the path **or any ancestor directory** matches, gitignore-style)
  - `glob_literal_prefix(pattern: str) -> str` (the anchored literal prefix usable as `path LIKE prefix%`, or `""`)

- [ ] **Step 1: Write the failing table test**

```python
import pytest

from shared.path_glob import compile_glob, glob_literal_prefix, glob_matches

CASES = [
    ("workflows/*.py", "workflows/top.py", True),
    ("workflows/*.py", "workflows/sub/nested.py", False),
    ("workflows/**/*.py", "workflows/sub/nested.py", True),
    ("workflows/**/*.py", "workflows/top.py", True),
    ("**/foo.py", "foo.py", True),
    ("**/foo.py", "a/b/foo.py", True),
    ("*.py", "a/b/c.py", True),            # no slash -> any depth
    ("/*.py", "a/c.py", False),            # leading slash anchors to root
    ("/*.py", "c.py", True),
    ("my_x.txt", "myAx.txt", False),       # '_' is literal
    ("*.{py,json}", "data/c.json", True),
    ("*.{py,json}", "a.ts", False),
    ("file?.txt", "file1.txt", True),
    ("file?.txt", "file/.txt", False),
    ("[ab].py", "a.py", True),
    ("[!ab].py", "a.py", False),
    ("modules", "modules/x/y.py", True),   # directory pattern covers contents
    ("modules/", "modules/x.py", True),
    ("WORKFLOWS/*.py", "workflows/top.py", False),  # case-sensitive like rg
    ("**", "anything/at/all", True),
]


@pytest.mark.parametrize("pattern,path,expected", CASES)
def test_glob_semantics(pattern, path, expected):
    assert glob_matches(compile_glob(pattern), path) is expected


@pytest.mark.parametrize(
    "pattern,prefix",
    [("workflows/*.py", "workflows/"), ("/apps/x/**", "apps/x/"), ("*.py", ""), ("**/foo.py", ""), ("a_b/*", "a_b/")],
)
def test_literal_prefix(pattern, prefix):
    assert glob_literal_prefix(pattern) == prefix


def test_unbalanced_brace_is_value_error():
    with pytest.raises(ValueError):
        compile_glob("*.{py,json")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_path_glob.py -v`
Expected: FAIL `ModuleNotFoundError: shared.path_glob`

- [ ] **Step 3: Implement**

```python
"""ripgrep/gitignore-style path globs for source search.

* ``*`` and ``?`` never cross ``/``; ``**`` does. ``{a,b}`` alternates; ``[...]``
  / ``[!...]`` are classes. Matching is case-sensitive.
* A pattern with no ``/`` (other than a trailing one) matches at any depth.
  A leading ``/`` anchors to the root. A trailing ``/`` matches a directory.
* A pattern matching a directory also matches everything below it.
"""

from __future__ import annotations

import re

_META = set("*?[{")


def _expand_braces(pattern: str) -> list[str]:
    start = pattern.find("{")
    if start == -1:
        if "}" in pattern:
            raise ValueError(f"unbalanced '}}' in glob {pattern!r}")
        return [pattern]
    depth = 0
    for end in range(start, len(pattern)):
        if pattern[end] == "{":
            depth += 1
        elif pattern[end] == "}":
            depth -= 1
            if depth == 0:
                break
    else:
        raise ValueError(f"unbalanced '{{' in glob {pattern!r}")
    body, parts, level, last = pattern[start + 1:end], [], 0, 0
    for i, ch in enumerate(body):
        if ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
        elif ch == "," and level == 0:
            parts.append(body[last:i])
            last = i + 1
    parts.append(body[last:])
    head, tail = pattern[:start], pattern[end + 1:]
    return [x for p in parts for x in _expand_braces(head + p + tail)]


def _translate(pattern: str) -> str:
    out, i, n = [], 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            close = pattern.find("]", i + 2)
            if close == -1:
                out.append(re.escape("["))
                i += 1
                continue
            body = pattern[i + 1:close]
            if body.startswith("!"):
                body = "^" + body[1:]
            escaped = body.replace("\\", "\\\\")
            out.append(f"[{escaped}]")
            i = close + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def compile_glob(pattern: str) -> re.Pattern[str]:
    alternatives = []
    for alt in _expand_braces(pattern):
        anchored = alt.startswith("/")
        alt = alt.strip("/") if alt != "/" else ""
        body = _translate(alt)
        if not anchored and "/" not in alt:
            body = "(?:.*/)?" + body
        alternatives.append(body)
    return re.compile(r"\A(?:" + "|".join(alternatives) + r")\Z")


def glob_matches(compiled: re.Pattern[str], path: str) -> bool:
    parts = path.split("/")
    return any(compiled.match("/".join(parts[:k])) for k in range(len(parts), 0, -1))


def glob_literal_prefix(pattern: str) -> str:
    alt = pattern.lstrip("/")
    if "{" in alt or ("/" not in alt.rstrip("/") and not pattern.startswith("/")):
        return ""
    prefix = []
    for ch in alt:
        if ch in _META:
            break
        prefix.append(ch)
    literal = "".join(prefix)
    return literal[: literal.rfind("/") + 1]
```

- [ ] **Step 4: Run to verify it passes**

Run: `./test.sh tests/unit/test_path_glob.py -v`
Expected: PASS (all 20 cases + prefix + error).

- [ ] **Step 5: Commit**

```bash
git add api/shared/path_glob.py api/tests/unit/test_path_glob.py
git commit -m "feat(files): ripgrep-style glob compiler for source search"
```

---

### Task 5: The single search service + REST contract

**Files:**
- Create: `api/src/services/source_search/{__init__,cursor,matching,candidates,guidance}.py`
- Modify: `api/src/models/contracts/editor.py` (replace `SearchRequest`/`SearchResult`/`SearchResponse`), plus the exports wherever `SearchResult` is re-exported (`grep -rn "SearchResult" api/src/models`)
- Modify: `api/src/routers/files.py:1616-1640`
- Delete: `api/src/services/editor/search.py`, `api/tests/unit/services/test_editor_search.py`
- Test: `api/tests/unit/services/test_source_search_matching.py`, `api/tests/unit/services/test_source_search_cursor.py`, `api/tests/e2e/platform/test_source_search.py`

**Interfaces:**
- Consumes: `compile_glob`, `glob_matches`, `glob_literal_prefix` (Task 4); `FileIndex`, `SolutionFileIndex` (Task 1); `Solution` ORM (`src.models.orm.solutions`).
- Produces (wire contract used by Tasks 6–8):

```python
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Literal text, or a Python `re` pattern when is_regex=true. Matched per line.")
    is_regex: bool = False
    case_sensitive: bool = Field(default=False, description="Case-insensitive literal matching uses PostgreSQL ILIKE to prefilter; a few non-ASCII case folds (e.g. ß) may be missed.")
    include_pattern: str | None = Field(default=None, description="ripgrep-style glob, e.g. '*.py', 'workflows/**', '*.{ts,tsx}'.")
    source: Literal["all", "workspace", "solutions"] = "all"
    solution_id: UUID | None = Field(default=None, description="Restrict to one Solution install's source.")
    output_mode: Literal["content", "files"] = "content"
    context_lines: int = Field(default=1, ge=0, le=5)
    limit: int = Field(default=25, ge=1, le=200, description="Matches (content) or files (files) per page.")
    cursor: str | None = Field(default=None, description="next_cursor from the previous page of this exact search.")

class SearchSource(BaseModel):
    kind: Literal["workspace", "solution"]
    solution_id: UUID | None = None
    solution_slug: str | None = None
    editable: bool

class SearchMatch(BaseModel):
    file_path: str
    source: SearchSource
    line: int = Field(..., ge=1)
    column: int = Field(..., ge=0)
    text: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)

class SearchFileHit(BaseModel):
    file_path: str
    source: SearchSource
    match_count: int
    first_line: int

class SearchResponse(BaseModel):
    query: str
    output_mode: Literal["content", "files"]
    matches: list[SearchMatch] = Field(default_factory=list)
    files: list[SearchFileHit] = Field(default_factory=list)
    returned: int
    has_more_matches: bool
    response_complete: bool
    next_cursor: str | None = None
    guidance: str
    search_time_ms: int
```

  - `search_source(db: AsyncSession, request: SearchRequest) -> SearchResponse`, raising `InvalidSearchRequest(ValueError)` for a bad regex, a bad glob or a foreign cursor.

- [ ] **Step 1: Write the pure-function tests** (`test_source_search_matching.py`):

```python
from src.services.source_search.matching import build_matcher, match_lines, window_line

def test_literal_is_escaped_and_case_insensitive():
    m = build_matcher("a.b", is_regex=False, case_sensitive=False)
    assert [h.line for h in match_lines("x\nA.B\naxb", m, context_lines=0)] == [2]

def test_every_occurrence_on_a_line_is_a_hit_with_its_column():
    hits = match_lines("foo foo", build_matcher("foo", False, True), context_lines=0)
    assert [(h.line, h.column) for h in hits] == [(1, 0), (1, 4)]

def test_crlf_is_normalised_and_context_collected():
    hits = match_lines("a\r\nTARGET\r\nc\r\n", build_matcher("TARGET", False, True), context_lines=1)
    assert hits[0].text == "TARGET" and hits[0].context_before == ["a"] and hits[0].context_after == ["c"]

def test_long_line_is_windowed():
    line = "x" * 5000 + "NEEDLE" + "y" * 5000
    text = window_line(line, column=5000)
    assert "NEEDLE" in text and len(text) <= 402 and text.startswith("…") and text.endswith("…")

def test_invalid_regex_raises_value_error():
    import pytest
    with pytest.raises(ValueError):
        build_matcher("(", is_regex=True, case_sensitive=False)
```

`test_source_search_cursor.py`:

```python
import pytest
from src.models.contracts.editor import SearchRequest
from src.services.source_search.cursor import SearchPosition, decode_cursor, encode_cursor, fingerprint

def test_round_trip():
    req = SearchRequest(query="x")
    pos = SearchPosition(rank=1, scope="3f0c...", path="a.py", line=4, column=2, seen=25)
    assert decode_cursor(encode_cursor(fingerprint(req), pos), fingerprint(req)) == pos

def test_cursor_rejected_for_different_query():
    token = encode_cursor(fingerprint(SearchRequest(query="x")), SearchPosition(0, "", "a", 1, 0, 25))
    with pytest.raises(ValueError, match="does not belong to this search"):
        decode_cursor(token, fingerprint(SearchRequest(query="y")))

def test_limit_change_keeps_cursor_valid():
    a, b = SearchRequest(query="x", limit=25), SearchRequest(query="x", limit=100)
    assert fingerprint(a) == fingerprint(b)

def test_garbage_cursor_is_value_error():
    with pytest.raises(ValueError):
        decode_cursor("not-base64!!", "f")
```

- [ ] **Step 2: Run to verify they fail**

Run: `./test.sh tests/unit/services/test_source_search_matching.py tests/unit/services/test_source_search_cursor.py -v`
Expected: FAIL `ModuleNotFoundError: src.services.source_search`

- [ ] **Step 3: Implement `matching.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_LINE_CHARS = 400


@dataclass(frozen=True)
class LineHit:
    line: int
    column: int
    text: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


def build_matcher(query: str, is_regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(query if is_regex else re.escape(query), flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


def window_line(line: str, column: int) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line
    start = max(0, column - MAX_LINE_CHARS // 4)
    end = start + MAX_LINE_CHARS
    return ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")


def match_lines(content: str, matcher: re.Pattern[str], context_lines: int) -> list[LineHit]:
    lines = content.replace("\r\n", "\n").split("\n")
    hits: list[LineHit] = []
    for idx, line in enumerate(lines):
        for m in matcher.finditer(line):
            hits.append(LineHit(
                line=idx + 1,
                column=m.start(),
                text=window_line(line, m.start()),
                context_before=[window_line(x, 0) for x in lines[max(0, idx - context_lines):idx]],
                context_after=[window_line(x, 0) for x in lines[idx + 1:idx + 1 + context_lines]],
            ))
    return hits
```

- [ ] **Step 4: Implement `cursor.py`**

```python
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass

from src.models.contracts.editor import SearchRequest


@dataclass(frozen=True)
class SearchPosition:
    rank: int      # 0 = workspace, 1 = solution
    scope: str     # "" for workspace, solution UUID string otherwise
    path: str
    line: int      # 0 in files mode
    column: int
    seen: int      # results returned so far, for "results 26-50" guidance


def fingerprint(request: SearchRequest) -> str:
    body = request.model_dump(mode="json", exclude={"cursor", "limit"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def encode_cursor(fp: str, pos: SearchPosition) -> str:
    raw = json.dumps({"f": fp, **asdict(pos)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(token: str, fp: str) -> SearchPosition:
    try:
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        found = data.pop("f")
        pos = SearchPosition(**data)
    except Exception as exc:
        raise ValueError("cursor is invalid — start the search again without cursor") from exc
    if found != fp:
        raise ValueError("cursor does not belong to this search — start without cursor or repeat the original query and filters")
    return pos
```

- [ ] **Step 5: Implement `candidates.py`.** It is an async generator of `Candidate(rank, scope, path, content, solution_slug)` in `(rank, scope, path)` order, starting at or after a position:

```python
CANDIDATE_PAGE = 200
CONTENT_CHUNK_BYTES = 8 * 1024 * 1024


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

  - Build `ws = select(literal(0).label("rank"), literal("").label("scope"), FileIndex.path, func.octet_length(FileIndex.content).label("size"), literal(None, String).label("slug")).where(FileIndex.content.isnot(None))`.
  - Build `sol = select(literal(1), cast(SolutionFileIndex.solution_id, String), SolutionFileIndex.path, func.octet_length(SolutionFileIndex.content), Solution.slug).join(Solution, Solution.id == SolutionFileIndex.solution_id).where(SolutionFileIndex.content.isnot(None))`.
  - Apply to each side: the literal-query prefilter `content.ilike(f"%{_escape_like(q)}%", escape="\\")` (or `.like` when `case_sensitive`) only when `not is_regex`; `path.like(_escape_like(prefix) + "%", escape="\\")` when `glob_literal_prefix(include)` is non-empty; and the `source`/`solution_id` filters (omit a side entirely when excluded).
  - `u = union_all(...).subquery()`, then `select(u).where(tuple_(u.c.rank, u.c.scope, u.c.path) >= (pos.rank, pos.scope, pos.path)).order_by(u.c.rank, u.c.scope, u.c.path).limit(CANDIDATE_PAGE)`. Loop, advancing the keyset strictly past the last row of each page.
  - For each candidate page, drop rows failing `glob_matches` or `is_excluded_path`. Group the remaining rows into chunks with total `size` ≤ `CONTENT_CHUNK_BYTES` (at least one row per chunk). Fetch each chunk's content with one `select(path, content)` per table, keyed `IN (...)`. Yield in order.

- [ ] **Step 6: Implement `guidance.py`**

```python
def guidance(*, returned: int, seen_before: int, files: int, has_more: bool, output_mode: str, next_cursor: str | None, scope_label: str) -> str:
    unit = "files" if output_mode == "files" else "matches"
    if returned == 0 and not has_more:
        return (f"No matches in {scope_label}. Check spelling, set is_regex for patterns, "
                "or widen include_pattern/source.")
    span = f"{unit} {seen_before + 1}-{seen_before + returned}"
    where = "" if output_mode == "files" else f" across {files} files"
    if not has_more:
        return f"Complete: showing {span}{where}. There are no more results."
    return (f"Showing {span}{where}; more results exist. To get the next page, repeat this exact "
            f"search with cursor=\"{next_cursor}\". To narrow instead, set include_pattern or "
            f"solution_id, or use output_mode=\"files\" to list matching files first.")
```

`scope_label` is `"workspace and Solution source"`, `"workspace source"`, `"Solution source"`, or `"Solution <slug> source"`.

- [ ] **Step 7: Implement `__init__.py::search_source`.**
  1. `fp = fingerprint(request)`. Set `pos = decode_cursor(...)` when a cursor is given, else `SearchPosition(0, "", "", 0, -1, 0)`.
  2. Compile the matcher and the glob; raise `InvalidSearchRequest(str(exc))` on `ValueError`.
  3. Iterate candidates. For each one, run `await asyncio.to_thread(match_lines, content, matcher, request.context_lines)`. In the cursor's own file `(rank, scope, path) == (pos.rank, pos.scope, pos.path)`, drop hits with `(line, column) <= (pos.line, pos.column)`. In files mode, a file equal to the cursor's file is skipped entirely.
  4. Collect hits (content mode) or one `SearchFileHit` per file (files mode) until `limit + 1` results exist. Extra results beyond `limit` only set `has_more_matches`; they are never returned.
  5. `next_cursor` = the position of the last **returned** result with `seen = pos.seen + returned` (files mode: `line=0, column=0`).
  6. Each result's `source` is `SearchSource(kind="workspace", editable=True)` for rank 0, or `SearchSource(kind="solution", solution_id=UUID(scope), solution_slug=slug, editable=False)`.

- [ ] **Step 8: Wire the router.** Replace the body of `search_file_contents`:

```python
    try:
        return await search_source(db, request)
    except InvalidSearchRequest as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
```

Update the import at `files.py:51`. Delete `api/src/services/editor/search.py` and `api/tests/unit/services/test_editor_search.py`.

- [ ] **Step 9: Write the e2e test** `api/tests/e2e/platform/test_source_search.py`, using the platform-admin client fixture used by neighbouring `test_storage_integrity.py`:

```python
async def _write(client, path, content):
    r = await client.post("/api/files/write", json={"path": path, "content": content, "mode": "cloud", "location": "workspace"})
    assert r.status_code == 204, r.text

async def test_pages_cover_every_match_exactly_once(admin_client, unique_token):
    for i in range(30):
        await _write(admin_client, f"search_e2e/{unique_token}/f{i:02}.txt", f"{unique_token}\n")
    seen, cursor = [], None
    while True:
        body = {"query": unique_token, "include_pattern": f"search_e2e/{unique_token}/**"}
        if cursor:
            body["cursor"] = cursor
        page = (await admin_client.post("/api/files/search", json=body)).json()
        seen += [m["file_path"] for m in page["matches"]]
        if page["response_complete"]:
            assert page["next_cursor"] is None and "no more results" in page["guidance"]
            break
        assert page["returned"] == 25 and "cursor=" in page["guidance"]
        cursor = page["next_cursor"]
    assert seen == sorted(seen) and len(seen) == len(set(seen)) == 30

async def test_json_and_extensionless_files_are_searchable(admin_client, unique_token):
    await _write(admin_client, f"search_e2e/{unique_token}/c.json", f'{{"k": "{unique_token}"}}')
    await _write(admin_client, f"search_e2e/{unique_token}/Dockerfile", f"# {unique_token}")
    page = (await admin_client.post("/api/files/search", json={"query": unique_token, "output_mode": "files"})).json()
    assert {f["file_path"].rsplit("/", 1)[-1] for f in page["files"]} == {"c.json", "Dockerfile"}

async def test_cursor_from_other_query_is_400(admin_client):
    first = (await admin_client.post("/api/files/search", json={"query": "import", "limit": 1})).json()
    if first["next_cursor"]:
        r = await admin_client.post("/api/files/search", json={"query": "other", "cursor": first["next_cursor"]})
        assert r.status_code == 400 and "does not belong" in r.json()["detail"]
```

Add a Solution case using the deploy fixture in `api/tests/e2e/platform/test_solution_deploy_files.py` (find it with `grep -n "async def\|fixture" api/tests/e2e/platform/test_solution_deploy_files.py | head`). Deploy a Solution containing `functions/{token}.py` and also write the workspace file `functions/{token}.py`. Search the token with no filters and assert two hits, one `{"kind": "workspace", "editable": true}` and one `{"kind": "solution", "editable": false}` with the slug. Then search with `solution_id` and assert only the Solution hit. This covers Review Focus 5.

Also add, as unit tests with a seeded DB session (pattern from existing DB-backed unit tests: `grep -rln "async_session\|db_session" api/tests/unit | head`):
- `test_page_two_resumes_after_last_hit_when_earlier_file_changes`: page 1 ends in `b.txt`; rewrite `a.txt` with more matches; page 2 starts in `b.txt`, after the cursor position.
- `test_regex_query_scans_without_sql_prefilter`: regex `\bX\d+` finds `X12` across files; `CONTENT_CHUNK_BYTES` monkeypatched to 10 forces chunking.

- [ ] **Step 10: Run the tests**

Run: `./test.sh tests/unit/services/test_source_search_matching.py tests/unit/services/test_source_search_cursor.py tests/unit/test_path_glob.py -v` then `./test.sh tests/e2e/platform/test_source_search.py -v` (per memory, `e2e <path>` runs the whole suite; pass the file path directly as shown).
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git commit -am "feat(files): single paged source search over workspace and solution source"
```

---

### Task 6: SDK + CLI

**Files:**
- Modify: `api/bifrost/files.py:403-452`, `api/bifrost/commands/files.py:16-17,402-441`
- Test: `api/tests/unit/test_files_sdk_search.py`, `api/tests/unit/test_cli_files.py`

**Interfaces:**
- Consumes: the Task 5 wire contract.
- Produces:
  - SDK `files.search(query, *, is_regex=False, case_sensitive=False, include_pattern=None, source="all", solution_id=None, output_mode="content", context_lines=1, limit=25, cursor=None) -> dict`
  - CLI `bifrost files search QUERY [--regex] [--case-sensitive] [--include GLOB] [--source all|workspace|solutions] [--solution SLUG|ID] [--files] [-C N] [--limit N] [--cursor TOKEN] [--json]`

- [ ] **Step 1: Write the failing CLI tests** (follow the existing `test_cli_files.py` mocking style for `files_sdk`):

```python
def test_search_renders_grep_style_with_next_page_command(cli_runner, mock_files_sdk):
    mock_files_sdk.search.return_value = {
        "query": "halo", "output_mode": "content", "returned": 1, "has_more_matches": True,
        "response_complete": False, "next_cursor": "abc", "guidance": "…", "search_time_ms": 3, "files": [],
        "matches": [{"file_path": "modules/halo.py", "line": 12, "column": 4, "text": "def halo():",
                     "context_before": [], "context_after": [],
                     "source": {"kind": "solution", "solution_slug": "covi-psa", "editable": False}}],
    }
    result = cli_runner.invoke(cli, ["files", "search", "halo", "--include", "*.py"])
    assert "covi-psa:modules/halo.py  (read-only Solution source)" in result.output
    assert "  12: def halo():" in result.output
    assert "bifrost files search halo --include '*.py' --cursor abc" in result.output

def test_search_passes_paging_and_scope(cli_runner, mock_files_sdk):
    cli_runner.invoke(cli, ["files", "search", "x", "--limit", "5", "--cursor", "c1", "--files", "--source", "workspace"])
    kwargs = mock_files_sdk.search.call_args.kwargs
    assert (kwargs["limit"], kwargs["cursor"], kwargs["output_mode"], kwargs["source"]) == (5, "c1", "files", "workspace")
```

- [ ] **Step 2: Run to verify they fail**

Run: `./test.sh tests/unit/test_cli_files.py -k search -v`
Expected: FAIL (`--limit` unknown / output mismatch).

- [ ] **Step 3: Implement the SDK.** Send only the documented keys; send `cursor`/`solution_id`/`include_pattern` only when not None. Update the docstring example to loop on `next_cursor`.

- [ ] **Step 4: Implement the CLI.**
  - Resolve `--solution` with the existing `_resolve_solution_install_id(client, ref)` → `solution_id`. The help text must state that this targets **Solution source**, unlike the runtime-file `--solution` on other `files` verbs.
  - `--json` → `output_result(result)`.
  - Human output:
    - Group consecutive matches by `(source, file_path)`. The header is `file_path` for the workspace, or `"{slug}:{file_path}  (read-only Solution source)"`.
    - Lines are `f"  {line}: {text}"`, with context lines as `f"  {n}- {text}"`.
    - Files mode prints `f"{header}  ({match_count} matches, first at line {first_line})"`.
  - Footer:
    - When `has_more_matches`: `More results: ` + `shlex.join(["bifrost", *argv_without_cursor, "--cursor", next_cursor])`, where `argv_without_cursor` is `sys.argv[1:]` with any existing `--cursor X` / `--cursor=X` removed.
    - Otherwise: `f"{returned} results — complete."`
  - Remove `--max-results`.

- [ ] **Step 5: Contract tripwires**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`
If the fingerprint test fails, apply the CLAUDE.md rule. Old CLIs send `max_results`, which Pydantic ignores, and render the response generically, so this is **compatible**: refresh only `EXPECTED_CONTRACT_FINGERPRINT`. Then regenerate skill appendices: `python api/scripts/skill-truth/generate.py`.

- [ ] **Step 6: Run the tests**

Run: `./test.sh tests/unit/test_cli_files.py tests/unit/test_files_sdk_search.py tests/unit/services/test_worker_sdk_http.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(cli): paged grep-style bifrost files search with copy-paste next page"
```

---

### Task 7: MCP `bifrost_file_search` (thin wrapper) replaces `search_content`

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py` (remove `search_content` + its TOOLS entry; add `bifrost_file_search`)
- Modify: any doc/skill/agent-prompt reference to `search_content` (`grep -rn "search_content" api/src skills plugins .claude docs client/src --include=* | grep -v node_modules`)
- Test: `api/tests/unit/services/mcp_server/test_code_editor_tools.py`, `test_tool_access.py`, `test_tool_implementations.py`, `api/tests/unit/test_mcp_thin_wrapper.py`

**Interfaces:**
- Consumes: REST `/api/files/search` via `call_rest(context, "POST", "/api/files/search", json=body)`.
- Produces: MCP tool `bifrost_file_search(context, query, is_regex=False, case_sensitive=False, include_pattern=None, source="all", solution_id=None, output_mode="content", context_lines=1, limit=25, cursor=None) -> ToolResult`.

- [ ] **Step 1: Write the failing test.** `bifrost_file_search` must call REST and render guidance. Follow the `call_rest` patching style used by the configs parity tests:

```python
async def test_bifrost_file_search_is_thin_and_surfaces_guidance(monkeypatch, mcp_context):
    calls = []
    async def fake_call_rest(ctx, method, path, **kw):
        calls.append((method, path, kw["json"]))
        return 200, {"query": "x", "output_mode": "content", "returned": 1, "has_more_matches": True,
                     "response_complete": False, "next_cursor": "c", "guidance": "Showing matches 1-1; … cursor=\"c\" …",
                     "search_time_ms": 1, "files": [], "matches": [{"file_path": "a.py", "line": 3, "column": 0,
                     "text": "x = 1", "context_before": [], "context_after": [],
                     "source": {"kind": "workspace", "editable": True}}]}
    monkeypatch.setattr(code_editor, "call_rest", fake_call_rest)
    result = await code_editor.bifrost_file_search(mcp_context, query="x", limit=1)
    assert calls == [("POST", "/api/files/search", {"query": "x", "limit": 1, "is_regex": False, "case_sensitive": False,
                      "source": "all", "output_mode": "content", "context_lines": 1})]
    assert "a.py:3: x = 1" in result.content[0].text and 'cursor="c"' in result.content[0].text
```

Add `"code_editor": {"bifrost_file_search"}` to `PARITY_HANDLERS` in `test_mcp_thin_wrapper.py`, so the no-ORM rule is enforced for this handler.

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/services/mcp_server/test_code_editor_tools.py tests/unit/test_mcp_thin_wrapper.py -v`
Expected: FAIL (`AttributeError: bifrost_file_search`)

- [ ] **Step 3: Implement.** Build the body like the SDK. On a non-200 response, return `error_result(f"bifrost_file_search failed: HTTP {status}", {"body": body})`. Display text: one `path:line: text` line per match (prefix `slug:` for Solution hits, plus `(read-only)`), or one `path (N matches)` line per file, followed by a blank line and `body["guidance"]`. Structured content is the full response body. Register it as `("bifrost_file_search", "Search Files", "Search workspace and Solution source like grep. Returns a small page; follow `guidance`/`next_cursor` for more.")`. Remove `search_content`, `_find_match_locations` (if only used by it), the `FileIndex` import if now unused, and `format_grep_matches` if unused elsewhere (`grep -rn format_grep_matches api/src`).

- [ ] **Step 4: Run the tests**

Run: `./test.sh tests/unit/services/mcp_server/ tests/unit/test_mcp_thin_wrapper.py tests/unit/services/test_operation_catalog.py -v`
Expected: PASS. `test_operation_catalog` still passes because `workspace.*` is not a canonical MCP domain; the catalog name `bifrost_file_search` now has a real handler.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(mcp): bifrost_file_search thin wrapper replaces legacy search_content"
```

---

### Task 8: Editor UI + QuickAccess

**Files:**
- Modify: `client/src/lib/v1.d.ts` (regenerated), `client/src/components/editor/SearchPanel.tsx`, `client/src/components/editor/SearchResultItem.tsx`, `client/src/components/quick-access/QuickAccess.tsx`
- Test: `client/src/components/editor/SearchPanel.test.tsx`, `client/src/components/quick-access/QuickAccess.test.tsx`, create `client/e2e/editor-search.admin.spec.ts`

**Interfaces:**
- Consumes: the Task 5 wire contract via regenerated types (`SearchMatch`, `SearchResponse`).

- [ ] **Step 1: Regenerate the types** against this worktree's stack: `(cd client && OPENAPI_URL=http://localhost:37657/openapi.json npm run generate:types)`. Re-export `SearchMatch` from `client/src/services/searchService.ts` in place of `SearchResult`.

- [ ] **Step 2: Write the failing vitest.** `SearchPanel` shows "Load more" when `has_more_matches`, and appends page 2:

```tsx
it("loads the next page with the returned cursor and appends results", async () => {
  const searchFiles = vi.spyOn(searchService, "searchFiles")
    .mockResolvedValueOnce(page({ matches: [match("a.py", 1)], has_more_matches: true, response_complete: false, next_cursor: "c1" }))
    .mockResolvedValueOnce(page({ matches: [match("b.py", 2)], has_more_matches: false, response_complete: true, next_cursor: null }));
  render(<SearchPanel />);
  await user.type(screen.getByRole("searchbox"), "needle{enter}");
  await user.click(await screen.findByRole("button", { name: /load more/i }));
  expect(searchFiles).toHaveBeenLastCalledWith(expect.objectContaining({ query: "needle", cursor: "c1", limit: 100 }));
  expect(await screen.findByText("b.py")).toBeInTheDocument();
  expect(screen.getByText("a.py")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
});
```

(Define `page()`/`match()` factories in the test file. Match the searchbox/role queries already used in `SearchPanel.test.tsx`.)

- [ ] **Step 3: Run to verify it fails**

Run: `./test.sh client unit src/components/editor/SearchPanel.test.tsx`
Expected: FAIL

- [ ] **Step 4: Implement.**
  - `SearchPanel` sends `{query, case_sensitive, is_regex, limit: 100}` and keeps `matches` in state.
  - "Load more" re-sends the same body plus `cursor`.
  - The summary line reads `${matches.length} matches${has_more ? "+" : ""}`.
  - Solution hits show a "Solution · {slug} · read-only" badge in `SearchResultItem`, and opening one is disabled with a tooltip "Deploy-owned source — edit locally and run bifrost solution deploy".
  - `QuickAccess` sends `limit: 20, output_mode: "files"` and maps `files` to script results: `name = basename`, `description = "{match_count} matches"`.

- [ ] **Step 5: Add the Playwright happy path** `client/e2e/editor-search.admin.spec.ts`: seed 30 files containing a unique token via the API helper used by other `*.admin.spec.ts` files, open the editor search, search the token, click "Load more", and expect 30 result rows.

- [ ] **Step 6: Run checks**

Run: `(cd client && npm run tsc && npm run lint)`, `./test.sh client unit src/components/editor/SearchPanel.test.tsx src/components/quick-access/QuickAccess.test.tsx`, `./test.sh client e2e e2e/editor-search.admin.spec.ts`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(editor): paged source search with load-more and solution badges"
```

---

### Task 9: Verification + live drive

- [ ] **Step 1: Quality gates**

Run: `./test.sh quality api`, then `./test.sh tests/unit/test_file_index_service.py tests/unit/test_file_index_single_writer.py tests/unit/test_file_index_reconciler.py tests/unit/test_path_glob.py tests/unit/services/test_source_search_matching.py tests/unit/services/test_source_search_cursor.py tests/unit/test_cli_files.py tests/unit/test_files_sdk_search.py tests/unit/services/mcp_server/ tests/unit/test_mcp_thin_wrapper.py tests/unit/services/test_operation_catalog.py tests/unit/test_dto_flags.py tests/unit/test_contract_version.py tests/unit/services/test_worker_sdk_http.py -v`
Expected: all PASS. Check the JUnit XML at `/tmp/bifrost-<project>/test-results.xml`.

- [ ] **Step 2: Live drive with the CLI** from `/tmp/bifrost-cli-fsearch`, after reinstalling the CLI from the stack.
  - Re-run every investigation repro: the 4 glob cases, the stale index after a direct write, resurrection, and `.json` visibility. Each must now behave correctly.
  - Seed 600 files containing a token, and confirm that paging via the printed "More results" command reaches all 600 with no duplicates.
  - Deploy a Solution and search it with no flags (labelled read-only), then with `--solution`.
  - Drive `bifrost_file_search` through the MCP endpoint once.
  - Open the editor in the browser (port mode) and use "Load more".

- [ ] **Step 3: Confirm the migration on a fresh DB**

Run: `./debug.sh down && BIFROST_FORCE_PORT=1 ./debug.sh up`, then `\di *trgm*` in psql. Confirm the "Coding Agent"-style rename by seeding an agent with `system_tools=['search_content']` before running the migration.

- [ ] **Step 4: Final commit / PR** via the `bifrost-issues` skill: a fresh issue, PR from `investigate/files-search` (rename the branch to `feat/unified-file-search`), body listing exact commands run and suites not run.
