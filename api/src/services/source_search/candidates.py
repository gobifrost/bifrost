"""SQL candidate paging over the workspace and Solution source indexes.

Candidates come back in ``(rank, scope, path)`` order — the workspace first,
then each Solution install — so a keyset cursor can resume exactly. Each scope
is paged by its own primary key (``path``, then ``(solution_id, path)``) in
the database's collation, which lets PostgreSQL walk the index in order and
stop after a page instead of scanning and sorting every match. Literal queries
are prefiltered in SQL (accelerated by the optional pg_trgm GIN index); regex
queries scan every in-scope row. Content is fetched in size-bounded chunks so
memory stays small however large the index is.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from shared.path_glob import glob_literal_prefix, glob_matches
from src.models.contracts.editor import SearchRequest
from src.models.orm.file_index import FileIndex, SolutionFileIndex
from src.models.orm.solutions import Solution
from src.services.editor.file_filter import is_excluded_path

CANDIDATE_PAGE = 200
CONTENT_CHUNK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Candidate:
    rank: int
    scope: str
    path: str
    slug: str | None
    content: str


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filters(content_col, path_col, request: SearchRequest) -> list:
    conds = [content_col.isnot(None)]
    if not request.is_regex:
        pattern = f"%{_escape_like(request.query)}%"
        conds.append(
            content_col.like(pattern, escape="\\")
            if request.case_sensitive
            else content_col.ilike(pattern, escape="\\")
        )
    prefix = glob_literal_prefix(request.include_pattern) if request.include_pattern else ""
    if prefix:
        conds.append(path_col.like(_escape_like(prefix) + "%", escape="\\"))
    return conds


def _wanted(path: str, include: re.Pattern[str] | None) -> bool:
    return not is_excluded_path(path) and (include is None or glob_matches(include, path))


async def _workspace_pages(
    db: AsyncSession, request: SearchRequest, after: str, inclusive: bool
) -> AsyncIterator[list[tuple[int, str, str, int, str | None]]]:
    conds = _filters(FileIndex.content, FileIndex.path, request)
    while True:
        bound = FileIndex.path >= after if inclusive else FileIndex.path > after
        page = (
            await db.execute(
                select(FileIndex.path, func.octet_length(FileIndex.content))
                .where(*conds, bound)
                .order_by(FileIndex.path)
                .limit(CANDIDATE_PAGE)
            )
        ).tuples().all()
        if not page:
            return
        yield [(0, "", path, size or 0, None) for path, size in page]
        if len(page) < CANDIDATE_PAGE:
            return
        after, inclusive = page[-1][0], False


async def _solution_pages(
    db: AsyncSession, request: SearchRequest, after: tuple[UUID, str] | None, inclusive: bool
) -> AsyncIterator[list[tuple[int, str, str, int, str | None]]]:
    conds = _filters(SolutionFileIndex.content, SolutionFileIndex.path, request)
    if request.solution_id is not None:
        conds.append(SolutionFileIndex.solution_id == request.solution_id)
    slugs: dict[UUID, str] = {}
    key = tuple_(SolutionFileIndex.solution_id, SolutionFileIndex.path)
    while True:
        stmt = select(
            SolutionFileIndex.solution_id,
            SolutionFileIndex.path,
            func.octet_length(SolutionFileIndex.content),
        ).where(*conds)
        if after is not None:
            stmt = stmt.where(key >= tuple_(*after) if inclusive else key > tuple_(*after))
        page = (
            await db.execute(
                stmt.order_by(SolutionFileIndex.solution_id, SolutionFileIndex.path).limit(CANDIDATE_PAGE)
            )
        ).tuples().all()
        if not page:
            return
        missing = {sid for sid, _path, _size in page} - slugs.keys()
        if missing:
            result = await db.execute(select(Solution.id, Solution.slug).where(Solution.id.in_(missing)))
            slugs.update(result.tuples().all())
        yield [(1, str(sid), path, size or 0, slugs.get(sid)) for sid, path, size in page]
        if len(page) < CANDIDATE_PAGE:
            return
        after, inclusive = (page[-1][0], page[-1][1]), False


async def _fetch_contents(
    db: AsyncSession, rows: list[tuple[int, str, str, str | None]]
) -> dict[tuple[int, str, str], str]:
    ws_paths = [path for rank, _scope, path, _slug in rows if rank == 0]
    sol_keys = [(UUID(scope), path) for rank, scope, path, _slug in rows if rank == 1]
    found: dict[tuple[int, str, str], str] = {}
    if ws_paths:
        result = await db.execute(
            select(FileIndex.path, FileIndex.content).where(FileIndex.path.in_(ws_paths))
        )
        for path, content in result.tuples():
            if content is not None:
                found[(0, "", path)] = content
    if sol_keys:
        result = await db.execute(
            select(SolutionFileIndex.solution_id, SolutionFileIndex.path, SolutionFileIndex.content).where(
                tuple_(SolutionFileIndex.solution_id, SolutionFileIndex.path).in_(sol_keys)
            )
        )
        for sid, path, content in result.tuples():
            if content is not None:
                found[(1, str(sid), path)] = content
    return found


async def iter_candidates(
    db: AsyncSession,
    request: SearchRequest,
    *,
    start: tuple[int, str, str],
    include: re.Pattern[str] | None,
) -> AsyncIterator[Candidate]:
    """Yield in-scope files with content, in cursor order, starting at ``start``."""
    rank, scope, path = start
    sources = []
    if request.source in ("all", "workspace") and request.solution_id is None and rank == 0:
        sources.append(_workspace_pages(db, request, path, inclusive=True))
    if request.source in ("all", "solutions"):
        sol_start = (UUID(scope), path) if rank == 1 else None
        sources.append(_solution_pages(db, request, sol_start, inclusive=True))
    for pages in sources:
        async for page in pages:
            chunk: list[tuple[int, str, str, str | None]] = []
            chunk_bytes = 0
            for row_rank, row_scope, row_path, size, slug in page:
                if not _wanted(row_path, include):
                    continue
                if chunk and chunk_bytes + size > CONTENT_CHUNK_BYTES:
                    async for cand in _emit(db, chunk):
                        yield cand
                    chunk, chunk_bytes = [], 0
                chunk.append((row_rank, row_scope, row_path, slug))
                chunk_bytes += size
            if chunk:
                async for cand in _emit(db, chunk):
                    yield cand


async def _emit(
    db: AsyncSession, chunk: list[tuple[int, str, str, str | None]]
) -> AsyncIterator[Candidate]:
    contents = await _fetch_contents(db, chunk)
    for rank, scope, path, slug in chunk:
        content = contents.get((rank, scope, path))
        if content is not None:
            yield Candidate(rank=rank, scope=scope, path=path, slug=slug, content=content)
