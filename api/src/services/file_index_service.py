"""
File Index Service — dual-write facade for _repo/ files.

Every write goes to both S3 (_repo/) and the file_index DB table.
Searches go through the DB; content reads go through get_module() (Redis/S3).
Binary files are written to S3 only (not indexed).
"""

from __future__ import annotations

import logging
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_index import FileIndex
from src.services.repo_storage import RepoStorage
from src.services.file_storage.s3_client import S3StorageClient

logger = logging.getLogger(__name__)

# File extensions that should be indexed (text-searchable)
TEXT_EXTENSIONS = frozenset({
    ".py", ".yaml", ".yml", ".md", ".txt", ".rst",
    ".toml", ".ini", ".cfg", ".csv", ".tsx", ".ts", ".js",
    ".jsx", ".css", ".html", ".xml", ".sql", ".sh",
})

# Search is intentionally a bounded projection of workspace content, not an
# alternate source of truth. Oversized text stays in repository storage but is
# omitted from PostgreSQL search so sync/import cannot materialize it in a job.
MAX_INDEXABLE_TEXT_BYTES = 8 * 1024 * 1024
FILE_COPY_CHUNK_SIZE = 8 * 1024 * 1024


def _is_text_file(path: str) -> bool:
    """Check if a file should be indexed based on extension."""
    for ext in TEXT_EXTENSIONS:
        if path.endswith(ext):
            return True
    return False


async def _invalidate_python_module_cache(path: str) -> None:
    if path.endswith(".py"):
        from src.core.module_cache import invalidate_module

        await invalidate_module(path)


class FileIndexService:
    """Dual-write facade for _repo/ files."""

    def __init__(self, db: AsyncSession, repo_storage: RepoStorage | None = None):
        self.db = db
        self.repo_storage = repo_storage or RepoStorage()

    async def write(self, path: str, content: bytes, updated_by: str | None = None) -> str:
        """
        Write a file to S3 and index it in the DB.

        Returns the content hash.
        """
        # Always write to S3
        content_hash = await self.repo_storage.write(path, content)

        # Only index text files
        if _is_text_file(path):
            if len(content) > MAX_INDEXABLE_TEXT_BYTES:
                logger.info("Skipping oversized text file in search index: %s", path)
                await self.db.execute(delete(FileIndex).where(FileIndex.path == path))
                await _invalidate_python_module_cache(path)
                return content_hash
            try:
                content_str = content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(f"Could not decode {path} as UTF-8, skipping index")
                await self.db.execute(delete(FileIndex).where(FileIndex.path == path))
                await _invalidate_python_module_cache(path)
                return content_hash

            stmt = insert(FileIndex).values(
                path=path,
                content=content_str,
                content_hash=content_hash,
                updated_by=updated_by,
            ).on_conflict_do_update(
                index_elements=[FileIndex.path],
                set_={
                    "content": content_str,
                    "content_hash": content_hash,
                    "updated_at": text("NOW()"),
                    "updated_by": updated_by,
                },
            )
            await self.db.execute(stmt)

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

        storage = S3StorageClient(self.repo_storage._settings)
        content_hash, size = await storage.put_object_from_chunks(
            self.repo_storage._repo_key(path), chunks()
        )
        if content_hash != expected_hash:
            raise ValueError(f"promoted source hash mismatch for {path}")
        if not _is_text_file(path) or size > MAX_INDEXABLE_TEXT_BYTES:
            await self.db.execute(delete(FileIndex).where(FileIndex.path == path))
            await _invalidate_python_module_cache(path)
            return content_hash
        content = source.read_bytes()
        content_hash = await self.write(path, content)
        if path.endswith(".py"):
            from src.core.module_cache import set_module

            try:
                module_content = content.decode("utf-8")
            except UnicodeDecodeError:
                return content_hash
            await set_module(path, module_content, content_hash)
        return content_hash

    async def delete(self, path: str) -> None:
        """Delete a file from S3 and the DB index."""
        await self.repo_storage.delete(path)
        await self.db.execute(
            delete(FileIndex).where(FileIndex.path == path)
        )

    async def search(self, pattern: str) -> list[dict]:
        """
        Search file contents for a pattern.

        Returns list of dicts with 'path' and 'content' keys.
        """
        result = await self.db.execute(
            select(FileIndex.path, FileIndex.content).where(
                FileIndex.content.ilike(f"%{pattern}%")
            )
        )
        return [{"path": row.path, "content": row.content} for row in result.all()]
