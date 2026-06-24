"""Service for managing files that belong to a Solution install.

Provides enumerate / read / write operations.  All metadata writes use
SQLAlchemy **Core** statements (``insert()`` / ``update()``) so they bypass
the ORM unit-of-work and are invisible to the ``before_flush`` read-only
guard in ``solutions/guard.py``.
"""

from __future__ import annotations

import hashlib
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

    ``content_bytes`` is populated during bundle capture (include_data=True) and
    carries the raw file content for encryption into the export bundle.  It is
    None when the entry comes from a metadata-only enumerate (no S3 read).
    """

    location: str
    path: str
    sha256: str | None
    size: int | None
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
            ).where(FileMetadata.solution_id == install_id)
        )
    ).all()
    return [
        SolutionFileEntry(
            location=row.location,
            path=row.path,
            sha256=row.sha256,
            size=row.size_bytes,
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
