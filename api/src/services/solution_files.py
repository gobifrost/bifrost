"""Service for managing files that belong to a Solution install.

Provides enumerate / read / write operations.  All metadata writes use
SQLAlchemy **Core** statements (``insert()`` / ``update()``) so they bypass
the ORM unit-of-work and are invisible to the ``before_flush`` read-only
guard in ``solutions/guard.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.file_paths import resolve_s3_key
from src.models.orm.file_metadata import FileMetadata
from src.services.file_storage import FileStorageService


@dataclass
class SolutionFileEntry:
    """Summary of one file belonging to a solution install.

    ``s3_key`` points at the runtime object so full exports can stream payloads
    from S3. ``content_bytes`` is retained for small in-memory test fixtures and
    legacy deploy paths; live capture leaves it unset.
    """

    location: str
    path: str
    sha256: str | None
    size: int | None
    s3_key: str | None = None
    payload: str | None = None
    content_bytes: bytes | None = None


async def enumerate_solution_files(
    db: AsyncSession,
    install_id: UUID,
) -> list[SolutionFileEntry]:
    """Return metadata for every file belonging to *install_id*.

    Reads the ``file_metadata`` index — no S3 calls.
    """
    rows = (
        await db.execute(
            select(
                FileMetadata.location,
                FileMetadata.path,
                FileMetadata.sha256,
                FileMetadata.size_bytes,
                FileMetadata.s3_key,
            ).where(FileMetadata.solution_id == install_id)
        )
    ).all()
    return [
        SolutionFileEntry(
            location=row.location,
            path=row.path,
            sha256=row.sha256,
            size=row.size_bytes,
            s3_key=row.s3_key,
        )
        for row in rows
    ]


async def read_solution_file(
    db: AsyncSession,
    install_id: UUID,
    location: str,
    path: str,
) -> bytes:
    """Read the bytes for a solution-owned file from S3.

    Raises ``FileNotFoundError`` when the metadata row does not exist or
    when S3 has no object at the resolved key.
    """
    # Verify the row belongs to this install before hitting S3.
    row = (
        await db.execute(
            select(FileMetadata.s3_key).where(
                FileMetadata.solution_id == install_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise FileNotFoundError(
            f"No file for install {install_id}: location={location!r} path={path!r}"
        )
    storage = FileStorageService(db)
    return await storage.read_uploaded_file(row)


async def iter_solution_file_chunks(
    db: AsyncSession,
    install_id: UUID,
    location: str,
    path: str,
    *,
    chunk_size: int = 8 * 1024 * 1024,
) -> AsyncIterator[bytes]:
    """Yield a solution-owned file's bytes from S3 in bounded chunks."""
    row = (
        await db.execute(
            select(FileMetadata.s3_key).where(
                FileMetadata.solution_id == install_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise FileNotFoundError(
            f"No file for install {install_id}: location={location!r} path={path!r}"
        )
    storage = FileStorageService(db)
    async for chunk in storage.iter_raw_s3_chunks(row, chunk_size=chunk_size):
        yield chunk


async def write_solution_file(
    db: AsyncSession,
    install_id: UUID,
    location: str,
    path: str,
    content: bytes,
    *,
    mode: str = "replace",
) -> bool:
    """Write *content* to S3 and upsert the metadata row.

    Parameters
    ----------
    mode:
        ``"replace"`` — always overwrite (returns ``True``).
        ``"skip"``    — preserve an existing file; returns ``False`` and leaves
                        both S3 bytes and the metadata row unchanged.

    Returns ``True`` when the file was written, ``False`` when it was skipped.
    """
    if mode not in ("replace", "skip"):
        raise ValueError(f"mode must be 'replace' or 'skip', got {mode!r}")

    s3_key = resolve_s3_key(location, str(install_id), path)

    existing_id: UUID | None = (
        await db.execute(
            select(FileMetadata.id).where(
                FileMetadata.solution_id == install_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None and mode == "skip":
        return False

    # Compute metadata before the S3 write so a hash failure doesn't leave an
    # orphaned object.
    sha256 = hashlib.sha256(content).hexdigest()
    size = len(content)
    now = datetime.now(timezone.utc)

    storage = FileStorageService(db)
    await storage.write_raw_to_s3(s3_key, content)

    if existing_id is None:
        # Core INSERT — bypasses unit-of-work; guard never fires.
        await db.execute(
            insert(FileMetadata).values(
                id=uuid4(),
                solution_id=install_id,
                organization_id=None,
                location=location,
                path=path,
                s3_key=s3_key,
                sha256=sha256,
                size_bytes=size,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        # Core UPDATE — same reason.
        await db.execute(
            update(FileMetadata)
            .where(FileMetadata.id == existing_id)
            .values(
                s3_key=s3_key,
                sha256=sha256,
                size_bytes=size,
                updated_at=now,
            )
        )

    await db.commit()
    return True


async def write_solution_file_from_chunks(
    db: AsyncSession,
    install_id: UUID,
    location: str,
    path: str,
    chunks: AsyncIterator[bytes],
    *,
    mode: str = "replace",
) -> bool:
    """Stream file chunks to S3 and upsert the solution file metadata row."""
    if mode not in ("replace", "skip"):
        raise ValueError(f"mode must be 'replace' or 'skip', got {mode!r}")

    s3_key = resolve_s3_key(location, str(install_id), path)
    existing_id: UUID | None = (
        await db.execute(
            select(FileMetadata.id).where(
                FileMetadata.solution_id == install_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None and mode == "skip":
        return False

    storage = FileStorageService(db)
    sha256, size = await storage.write_raw_chunks_to_s3(
        s3_key,
        chunks,
    )
    now = datetime.now(timezone.utc)

    if existing_id is None:
        await db.execute(
            insert(FileMetadata).values(
                id=uuid4(),
                solution_id=install_id,
                organization_id=None,
                location=location,
                path=path,
                s3_key=s3_key,
                sha256=sha256,
                size_bytes=size,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        await db.execute(
            update(FileMetadata)
            .where(FileMetadata.id == existing_id)
            .values(
                s3_key=s3_key,
                sha256=sha256,
                size_bytes=size,
                updated_at=now,
            )
        )

    await db.commit()
    return True
