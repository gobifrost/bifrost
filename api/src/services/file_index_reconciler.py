"""
File Index Reconciler — heals drift between source storage and the search index.

Storage is the source of truth; the index is a projection of it. For the
instance ``_repo/`` workspace and every Solution install's source prefix, the
reconciler adds rows for unindexed objects, refreshes rows whose content hash
no longer matches the object, and removes rows whose object is gone. It never
writes to storage.

Memory is bounded to one object at a time: listings carry sizes, objects over
the index cap are hashed by streaming, and only path + hash are read from the
database.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_index import FileIndex, SolutionFileIndex
from src.models.orm.solutions import Solution
from src.services.file_index_service import (
    MAX_INDEXABLE_TEXT_BYTES,
    FileIndexService,
    is_tracked_path,
)
from src.services.repo_storage import RepoStorage, S3FileMetadata
from src.services.solutions.storage import SolutionStorage

logger = logging.getLogger(__name__)


RECONCILE_COMMIT_EVERY = 200


def _empty_stats() -> dict[str, int]:
    return {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "failed": 0}


async def _reconcile_scope(
    db: AsyncSession,
    store: RepoStorage | SolutionStorage,
    indexed: dict[str, str | None],
    *,
    write: Callable[[str, bytes | None, str, str | None, bool], Awaitable[None]],
    remove: Callable[[str, str | None], Awaitable[None]],
    stats: dict[str, int],
    label: str,
) -> None:
    """Heal one scope. Each object is handled on its own; batches commit as they go."""
    listing = {
        path: meta
        for path, meta in (await store.list_with_metadata("")).items()
        if is_tracked_path(path)
    }
    pending = 0
    for path, meta in sorted(listing.items()):
        known = path in indexed
        observed = indexed.get(path)
        try:
            if meta.size > MAX_INDEXABLE_TEXT_BYTES:
                content_hash = await store.content_hash(path)
                if content_hash is None:  # deleted since the listing; next run removes its row
                    continue
                body = None
            else:
                body = await store.read(path)
                content_hash = hashlib.sha256(body).hexdigest()
            if observed == content_hash:
                stats["unchanged"] += 1
                continue
            await write(path, body, content_hash, observed, known)
        except Exception:
            stats["failed"] += 1
            logger.warning("Could not reconcile %s %s; leaving its row for the next run", label, path, exc_info=True)
            continue
        stats["updated" if known else "added"] += 1
        pending += 1
        if pending >= RECONCILE_COMMIT_EVERY:
            await db.commit()
            pending = 0

    for path in sorted(set(indexed) - set(listing)):
        await remove(path, indexed[path])
        stats["removed"] += 1
    await db.commit()


async def reconcile_file_index(
    db: AsyncSession,
    repo_storage: RepoStorage | None = None,
    solution_storage_factory: Callable[[UUID], SolutionStorage] = SolutionStorage,
) -> dict[str, dict[str, int]]:
    """Reconcile the workspace and Solution source indexes with storage.

    Returns ``{"workspace": stats, "solutions": stats}`` where each stats dict
    counts ``added``, ``updated``, ``removed``, ``unchanged`` and ``failed``.
    Objects are handled one at a time and committed in batches, so one bad
    object or install never discards the rest of the run, and reconciled writes
    never overwrite a row a user changed after the reconciler read the object.
    """
    service = FileIndexService(db, repo_storage)
    workspace = _empty_stats()
    indexed = dict((await db.execute(select(FileIndex.path, FileIndex.content_hash))).tuples().all())
    try:
        await _reconcile_scope(
            db,
            service.repo_storage,
            indexed,
            write=lambda path, body, content_hash, observed, known: service.reconcile_row(
                path, body, content_hash, observed=observed, known=known
            ),
            remove=service.unindex_if_unchanged,
            stats=workspace,
            label="workspace",
        )
    except Exception:
        await db.rollback()
        workspace["failed"] += 1
        logger.exception("Workspace source index reconcile failed")

    solutions = _empty_stats()
    for (solution_id,) in (await db.execute(select(Solution.id).order_by(Solution.id))).all():
        sol_indexed = dict(
            (
                await db.execute(
                    select(SolutionFileIndex.path, SolutionFileIndex.content_hash).where(
                        SolutionFileIndex.solution_id == solution_id
                    )
                )
            ).tuples().all()
        )

        async def write_sol(
            path: str, body: bytes | None, content_hash: str, observed: str | None, known: bool,
            sid: UUID = solution_id,
        ) -> None:
            await service.reconcile_solution_row(sid, path, body, content_hash, observed=observed, known=known)

        async def remove_sol(path: str, observed: str | None, sid: UUID = solution_id) -> None:
            await service.unindex_solution_if_unchanged(sid, path, observed)

        try:
            await _reconcile_scope(
                db,
                solution_storage_factory(solution_id),
                sol_indexed,
                write=write_sol,
                remove=remove_sol,
                stats=solutions,
                label=f"solution {solution_id}",
            )
        except Exception:
            await db.rollback()
            solutions["failed"] += 1
            logger.exception("Solution source index reconcile failed for %s", solution_id)

    logger.info(
        "File index reconciled: workspace %s; solutions %s", workspace, solutions
    )
    return {"workspace": workspace, "solutions": solutions}
