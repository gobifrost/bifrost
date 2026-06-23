"""Service for managing files that belong to a Solution install.

Provides enumerate / read / write / orphan-move operations.  All metadata
writes use SQLAlchemy **Core** statements (``insert()`` / ``update()``) so
they bypass the ORM unit-of-work and are invisible to the
``before_flush`` read-only guard in ``solutions/guard.py``.
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

# UUID list type alias for captured_keys payloads.
_FileIdList = list[str]


@dataclass
class SolutionFileEntry:
    """Lightweight summary of one file belonging to a solution install."""

    location: str
    path: str
    sha256: str | None
    size: int | None


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


async def orphan_solution_files(
    db: AsyncSession,
    install_id: UUID,
    org_id: UUID,
    slug: str,
) -> int:
    """Re-stamp all files owned by *install_id* as org-owned orphans.

    For each file:
    1. Core-UPDATE the ``file_metadata`` row: clear ``solution_id``, set
       ``organization_id``, record provenance (``origin_solution_id``,
       ``origin_solution_slug``, ``orphaned_at``).
    2. Move the S3 object from the solution-scoped key to the org-scoped key
       (read → write new key → delete old key).

    Returns the number of files orphaned.
    """
    rows = (
        await db.execute(
            select(
                FileMetadata.id,
                FileMetadata.location,
                FileMetadata.path,
                FileMetadata.s3_key,
            ).where(FileMetadata.solution_id == install_id)
        )
    ).all()

    if not rows:
        return 0

    storage = FileStorageService(db)
    now = datetime.now(timezone.utc)

    for row in rows:
        old_s3_key = row.s3_key
        new_s3_key = resolve_s3_key(row.location, str(org_id), row.path)

        # Move bytes: read old → write new → delete old.
        content = await storage.read_uploaded_file(old_s3_key)
        await storage.write_raw_to_s3(new_s3_key, content)
        await storage.delete_raw_from_s3(old_s3_key)

        # Core UPDATE — bypasses unit-of-work; guard never fires.
        await db.execute(
            update(FileMetadata)
            .where(FileMetadata.id == row.id)
            .values(
                solution_id=None,
                organization_id=org_id,
                s3_key=new_s3_key,
                origin_solution_id=install_id,
                origin_solution_slug=slug,
                orphaned_at=now,
            )
        )

    await db.commit()
    return len(rows)


async def orphan_solution_files_by_ids(
    db: AsyncSession,
    install_id: UUID,
    org_id: UUID,
    slug: str,
    file_ids: _FileIdList,
) -> int:
    """Re-stamp a specific set of files (by ID) as org-owned orphans.

    This is the C3-safe variant: it works from a list of ``FileMetadata.id``
    values snapshotted at job-enqueue time, NOT by querying
    ``solution_id == install_id``.  After an uninstall the ``solution_id``
    column has already been cleared, so any query by ``solution_id`` would
    return zero rows.  The caller (``_run_file_job``) passes the IDs captured
    before the restamp happened.

    Returns the number of files successfully orphaned.
    """
    if not file_ids:
        return 0

    parsed_ids = [UUID(fid) for fid in file_ids]

    rows = (
        await db.execute(
            select(
                FileMetadata.id,
                FileMetadata.location,
                FileMetadata.path,
                FileMetadata.s3_key,
            ).where(FileMetadata.id.in_(parsed_ids))
        )
    ).all()

    if not rows:
        return 0

    storage = FileStorageService(db)
    now = datetime.now(timezone.utc)

    for row in rows:
        old_s3_key = row.s3_key
        new_s3_key = resolve_s3_key(row.location, str(org_id), row.path)

        content = await storage.read_uploaded_file(old_s3_key)
        await storage.write_raw_to_s3(new_s3_key, content)
        await storage.delete_raw_from_s3(old_s3_key)

        # Core UPDATE — bypasses unit-of-work; guard never fires.
        await db.execute(
            update(FileMetadata)
            .where(FileMetadata.id == row.id)
            .values(
                solution_id=None,
                organization_id=org_id,
                s3_key=new_s3_key,
                origin_solution_id=install_id,
                origin_solution_slug=slug,
                orphaned_at=now,
            )
        )

    await db.commit()
    return len(rows)
