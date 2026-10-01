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
from typing import Protocol
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


class _SourceStore(Protocol):
    async def list_with_metadata(self, prefix: str = "") -> dict[str, S3FileMetadata]: ...
    async def read(self, path: str) -> bytes: ...
    async def content_hash(self, path: str) -> str | None: ...


def _empty_stats() -> dict[str, int]:
    return {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}


async def _reconcile_scope(
    store: _SourceStore,
    indexed: dict[str, str | None],
    *,
    index: Callable[[str, bytes, str], Awaitable[None]],
    index_path_only: Callable[[str, str], Awaitable[None]],
    unindex: Callable[[str], Awaitable[None]],
    stats: dict[str, int],
) -> None:
    listing = {
        path: meta
        for path, meta in (await store.list_with_metadata("")).items()
        if is_tracked_path(path)
    }
    for path, meta in sorted(listing.items()):
        known = path in indexed
        if meta.size > MAX_INDEXABLE_TEXT_BYTES:
            content_hash = await store.content_hash(path) or ""
            if indexed.get(path) == content_hash:
                stats["unchanged"] += 1
                continue
            await index_path_only(path, content_hash)
        else:
            body = await store.read(path)
            content_hash = hashlib.sha256(body).hexdigest()
            if indexed.get(path) == content_hash:
                stats["unchanged"] += 1
                continue
            await index(path, body, content_hash)
        stats["updated" if known else "added"] += 1

    for path in sorted(set(indexed) - set(listing)):
        await unindex(path)
        stats["removed"] += 1


async def reconcile_file_index(
    db: AsyncSession,
    repo_storage: RepoStorage | None = None,
    solution_storage_factory: Callable[[UUID], SolutionStorage] = SolutionStorage,
) -> dict[str, dict[str, int]]:
    """Reconcile the workspace and Solution source indexes with storage.

    Returns ``{"workspace": stats, "solutions": stats}`` where each stats dict
    counts ``added``, ``updated``, ``removed`` and ``unchanged`` rows; the
    solutions dict also counts installs that ``failed`` to reconcile. Each scope
    commits on its own so one failing install cannot discard the rest.
    """
    service = FileIndexService(db, repo_storage)
    workspace = _empty_stats()
    indexed = dict((await db.execute(select(FileIndex.path, FileIndex.content_hash))).tuples().all())
    await _reconcile_scope(
        service.repo_storage,
        indexed,
        index=service.index,
        index_path_only=service.index_path_only,
        unindex=service.unindex,
        stats=workspace,
    )
    await db.commit()

    solutions = {**_empty_stats(), "failed": 0}
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

        async def index_sol(path: str, body: bytes, content_hash: str, sid: UUID = solution_id) -> None:
            await service.index_solution(sid, path, body, content_hash)

        async def index_sol_path_only(path: str, content_hash: str, sid: UUID = solution_id) -> None:
            await service.index_solution_path_only(sid, path, content_hash)

        async def unindex_sol(path: str, sid: UUID = solution_id) -> None:
            await service.unindex_solution(sid, path)

        try:
            await _reconcile_scope(
                solution_storage_factory(solution_id),
                sol_indexed,
                index=index_sol,
                index_path_only=index_sol_path_only,
                unindex=unindex_sol,
                stats=solutions,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            solutions["failed"] += 1
            logger.exception("Solution source index reconcile failed for %s", solution_id)

    logger.info(
        "File index reconciled: workspace %s; solutions %s", workspace, solutions
    )
    return {"workspace": workspace, "solutions": solutions}
