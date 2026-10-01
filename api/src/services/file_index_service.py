"""
File Index Service — the single writer of the source search index.

``file_index`` mirrors the instance ``_repo/`` workspace and
``solution_file_index`` mirrors each Solution install's ``_solutions/{id}/``
source. Every code path that changes those objects updates the index through
this service, and one policy decides what a row holds:

* every tracked object (anything not excluded by ``file_filter``) gets a row,
  so listing, moves, and search agree on what exists;
* ``content`` is the UTF-8 text when the bytes decode and fit under
  ``MAX_INDEXABLE_TEXT_BYTES``; otherwise it is NULL (a path-only row).

Content reads still go through RepoStorage / get_module(); the index is for
search and metadata only.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_index import FileIndex, SolutionFileIndex
from src.services.editor.file_filter import is_excluded_path
from src.services.repo_storage import RepoStorage

# Search is a bounded projection of source content, not an alternate source of
# truth. Oversized text stays in storage but gets a path-only row so sync and
# import never materialize it in PostgreSQL.
MAX_INDEXABLE_TEXT_BYTES = 8 * 1024 * 1024
FILE_COPY_CHUNK_SIZE = 8 * 1024 * 1024


def is_tracked_path(path: str) -> bool:
    """Every source object gets an index row unless the workspace filter excludes it."""
    return not is_excluded_path(path)


def indexable_text(content: bytes) -> str | None:
    """Return searchable text, or None for a path-only row (binary/invalid/oversized)."""
    if len(content) > MAX_INDEXABLE_TEXT_BYTES or b"\x00" in content:
        return None
    try:
        return content.decode("utf-8").removeprefix("﻿")
    except UnicodeDecodeError:
        return None


def _hash_is(column, observed: str | None):
    return column.is_(None) if observed is None else column == observed


async def _invalidate_python_module_cache(path: str) -> None:
    if path.endswith(".py"):
        from src.core.module_cache import invalidate_module

        await invalidate_module(path)


class FileIndexService:
    """Single writer for ``file_index`` and ``solution_file_index``."""

    def __init__(self, db: AsyncSession, repo_storage: RepoStorage | None = None):
        self.db = db
        self.repo_storage = repo_storage or RepoStorage()

    # ── workspace rows ──────────────────────────────────────────────────────

    async def _upsert(
        self,
        path: str,
        content: str | None,
        content_hash: str,
        updated_by: str | None,
    ) -> None:
        values = {
            "path": path,
            "content": content,
            "content_hash": content_hash,
            "updated_by": updated_by,
        }
        stmt = insert(FileIndex).values(**values).on_conflict_do_update(
            index_elements=[FileIndex.path],
            set_={
                "content": content,
                "content_hash": content_hash,
                "updated_at": text("NOW()"),
                "updated_by": updated_by,
            },
        )
        await self.db.execute(stmt)

    async def index(
        self,
        path: str,
        content: bytes,
        content_hash: str,
        updated_by: str | None = None,
    ) -> None:
        """Index workspace bytes already stored at ``path``."""
        if not is_tracked_path(path):
            await self.unindex(path)
            return
        await self._upsert(path, indexable_text(content), content_hash, updated_by)

    async def index_path_only(
        self,
        path: str,
        content_hash: str,
        updated_by: str | None = None,
    ) -> None:
        """Record a workspace object whose bytes are not searchable."""
        if not is_tracked_path(path):
            await self.unindex(path)
            return
        await self._upsert(path, None, content_hash, updated_by)

    async def unindex(self, path: str) -> None:
        await self.db.execute(delete(FileIndex).where(FileIndex.path == path))

    async def move_index(self, old_path: str, new_path: str) -> None:
        """Carry a workspace row to a new path (content is unchanged by a move)."""
        row = (
            await self.db.execute(
                select(FileIndex.content, FileIndex.content_hash, FileIndex.updated_by).where(
                    FileIndex.path == old_path
                )
            )
        ).first()
        await self.unindex(old_path)
        if row is None or not is_tracked_path(new_path):
            return
        await self._upsert(new_path, row.content, row.content_hash or "", row.updated_by)

    async def upsert_many(self, rows: list[dict]) -> None:
        """Bulk upsert prepared workspace rows (``path``, ``content``, ``content_hash``)."""
        if not rows:
            return
        stmt = insert(FileIndex).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[FileIndex.path],
            set_={
                "content": stmt.excluded.content,
                "content_hash": stmt.excluded.content_hash,
                "updated_at": text("NOW()"),
            },
        )
        await self.db.execute(stmt)

    async def unindex_many(self, paths: list[str]) -> None:
        if paths:
            await self.db.execute(delete(FileIndex).where(FileIndex.path.in_(paths)))

    async def index_existing_object(self, path: str, updated_by: str | None = None) -> None:
        """Index a workspace object already in S3 (e.g. after a presigned PUT)."""
        meta = await self.repo_storage.head(path)
        if meta is None:
            await self.unindex(path)
            return
        if meta.size > MAX_INDEXABLE_TEXT_BYTES:
            await self.index_path_only(path, await self.repo_storage.content_hash(path) or "", updated_by)
            return
        content = await self.repo_storage.read(path)
        await self.index(path, content, hashlib.sha256(content).hexdigest(), updated_by)

    # ── reconciler writes (never clobber a concurrent writer) ───────────────
    #
    # The reconciler reads an object, then writes its row. A user write landing
    # in between must win, so each reconciled write only applies while the row
    # still carries the hash the reconciler observed (or is still absent).

    async def reconcile_row(
        self, path: str, content: bytes | None, content_hash: str, *, observed: str | None, known: bool
    ) -> None:
        text_content = indexable_text(content) if content is not None else None
        if not known:
            await self.db.execute(
                insert(FileIndex)
                .values(path=path, content=text_content, content_hash=content_hash)
                .on_conflict_do_nothing(index_elements=[FileIndex.path])
            )
            return
        await self.db.execute(
            update(FileIndex)
            .where(FileIndex.path == path, _hash_is(FileIndex.content_hash, observed))
            .values(content=text_content, content_hash=content_hash, updated_at=text("NOW()"))
        )

    async def unindex_if_unchanged(self, path: str, observed: str | None) -> None:
        await self.db.execute(
            delete(FileIndex).where(FileIndex.path == path, _hash_is(FileIndex.content_hash, observed))
        )

    async def reconcile_solution_row(
        self,
        solution_id: UUID,
        path: str,
        content: bytes | None,
        content_hash: str,
        *,
        observed: str | None,
        known: bool,
    ) -> None:
        text_content = indexable_text(content) if content is not None else None
        if not known:
            await self.db.execute(
                insert(SolutionFileIndex)
                .values(solution_id=solution_id, path=path, content=text_content, content_hash=content_hash)
                .on_conflict_do_nothing(index_elements=[SolutionFileIndex.solution_id, SolutionFileIndex.path])
            )
            return
        await self.db.execute(
            update(SolutionFileIndex)
            .where(
                SolutionFileIndex.solution_id == solution_id,
                SolutionFileIndex.path == path,
                _hash_is(SolutionFileIndex.content_hash, observed),
            )
            .values(content=text_content, content_hash=content_hash, updated_at=text("NOW()"))
        )

    async def unindex_solution_if_unchanged(
        self, solution_id: UUID, path: str, observed: str | None
    ) -> None:
        await self.db.execute(
            delete(SolutionFileIndex).where(
                SolutionFileIndex.solution_id == solution_id,
                SolutionFileIndex.path == path,
                _hash_is(SolutionFileIndex.content_hash, observed),
            )
        )

    # ── workspace S3 + index ────────────────────────────────────────────────

    async def write(self, path: str, content: bytes, updated_by: str | None = None) -> str:
        """Write a workspace file to S3 and index it. Returns the content hash."""
        content_hash = await self.repo_storage.write(path, content)
        await self.index(path, content, content_hash, updated_by)
        if indexable_text(content) is None:
            await _invalidate_python_module_cache(path)
        return content_hash

    async def write_file(self, path: str, source: Path, *, expected_hash: str) -> str:
        """Stream a staged file into _repo/ and index only bounded text."""
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(FILE_COPY_CHUNK_SIZE):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise ValueError(f"staged source hash mismatch for {path}")

        async def chunks() -> AsyncIterator[bytes]:
            with source.open("rb") as handle:
                while chunk := handle.read(FILE_COPY_CHUNK_SIZE):
                    yield chunk

        from src.services.file_storage.s3_client import S3StorageClient

        storage = S3StorageClient(self.repo_storage._settings)
        content_hash, size = await storage.put_object_from_chunks(
            self.repo_storage._repo_key(path), chunks()
        )
        if content_hash != expected_hash:
            raise ValueError(f"promoted source hash mismatch for {path}")
        if size > MAX_INDEXABLE_TEXT_BYTES:
            await self.index_path_only(path, content_hash)
            await _invalidate_python_module_cache(path)
            return content_hash
        content = source.read_bytes()
        content_hash = await self.write(path, content)
        if path.endswith(".py"):
            from src.core.module_cache import set_module

            module_content = indexable_text(content)
            if module_content is None:
                return content_hash
            await set_module(path, module_content, content_hash)
        return content_hash

    async def delete(self, path: str) -> None:
        """Delete a workspace file from S3 and the index."""
        await self.repo_storage.delete(path)
        await self.unindex(path)

    # ── Solution source rows ────────────────────────────────────────────────

    async def _upsert_solution(
        self,
        solution_id: UUID,
        path: str,
        content: str | None,
        content_hash: str,
    ) -> None:
        stmt = insert(SolutionFileIndex).values(
            solution_id=solution_id,
            path=path,
            content=content,
            content_hash=content_hash,
        ).on_conflict_do_update(
            index_elements=[SolutionFileIndex.solution_id, SolutionFileIndex.path],
            set_={
                "content": content,
                "content_hash": content_hash,
                "updated_at": text("NOW()"),
            },
        )
        await self.db.execute(stmt)

    async def index_solution(
        self,
        solution_id: UUID,
        path: str,
        content: bytes,
        content_hash: str,
    ) -> None:
        """Index Solution source bytes already stored under the install prefix."""
        if not is_tracked_path(path):
            await self.unindex_solution(solution_id, path)
            return
        await self._upsert_solution(solution_id, path, indexable_text(content), content_hash)

    async def unindex_solution(self, solution_id: UUID, path: str) -> None:
        await self.db.execute(
            delete(SolutionFileIndex).where(
                SolutionFileIndex.solution_id == solution_id,
                SolutionFileIndex.path == path,
            )
        )
