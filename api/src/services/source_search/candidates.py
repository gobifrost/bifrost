"""SQL candidate paging over the workspace and Solution source indexes.

Candidates come back in deterministic ``(rank, scope, path)`` byte order so a
keyset cursor can resume exactly. Literal queries are prefiltered in SQL
(accelerated by the optional pg_trgm GIN index); regex queries scan every
in-scope row. Content is fetched in size-bounded chunks so memory stays small
however large the index is.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import String, cast, func, literal, null, select, tuple_, union_all
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


def _content_filter(column, request: SearchRequest):
    if request.is_regex:
        return None
    pattern = f"%{_escape_like(request.query)}%"
    if request.case_sensitive:
        return column.like(pattern, escape="\\")
    return column.ilike(pattern, escape="\\")


def _branches(request: SearchRequest):
    prefix = glob_literal_prefix(request.include_pattern) if request.include_pattern else ""
    branches = []
    if request.source in ("all", "workspace") and request.solution_id is None:
        ws = select(
            literal(0).label("rank"),
            literal("").label("scope"),
            FileIndex.path.label("path"),
            func.octet_length(FileIndex.content).label("size"),
            cast(null(), String).label("slug"),
        ).where(FileIndex.content.isnot(None))
        if (cond := _content_filter(FileIndex.content, request)) is not None:
            ws = ws.where(cond)
        if prefix:
            ws = ws.where(FileIndex.path.like(_escape_like(prefix) + "%", escape="\\"))
        branches.append(ws)
    if request.source in ("all", "solutions"):
        sol = (
            select(
                literal(1).label("rank"),
                cast(SolutionFileIndex.solution_id, String).label("scope"),
                SolutionFileIndex.path.label("path"),
                func.octet_length(SolutionFileIndex.content).label("size"),
                Solution.slug.label("slug"),
            )
            .join(Solution, Solution.id == SolutionFileIndex.solution_id)
            .where(SolutionFileIndex.content.isnot(None))
        )
        if request.solution_id is not None:
            sol = sol.where(SolutionFileIndex.solution_id == request.solution_id)
        if (cond := _content_filter(SolutionFileIndex.content, request)) is not None:
            sol = sol.where(cond)
        if prefix:
            sol = sol.where(SolutionFileIndex.path.like(_escape_like(prefix) + "%", escape="\\"))
        branches.append(sol)
    return branches


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
    branches = _branches(request)
    if not branches:
        return
    union = union_all(*branches).subquery() if len(branches) > 1 else branches[0].subquery()
    key = tuple_(union.c.rank, union.c.scope.collate("C"), union.c.path.collate("C"))
    position, inclusive = start, True
    while True:
        bound = tuple_(literal(position[0]), literal(position[1]), literal(position[2]))
        page = (
            await db.execute(
                select(union.c.rank, union.c.scope, union.c.path, union.c.size, union.c.slug)
                .where(key >= bound if inclusive else key > bound)
                .order_by(union.c.rank, union.c.scope.collate("C"), union.c.path.collate("C"))
                .limit(CANDIDATE_PAGE)
            )
        ).tuples().all()
        if not page:
            return
        position, inclusive = (page[-1][0], page[-1][1], page[-1][2]), False
        wanted = [
            row for row in page
            if not is_excluded_path(row[2]) and (include is None or glob_matches(include, row[2]))
        ]
        chunk: list[tuple[int, str, str, str | None]] = []
        chunk_bytes = 0
        for rank, scope, path, size, slug in wanted:
            if chunk and chunk_bytes + (size or 0) > CONTENT_CHUNK_BYTES:
                async for cand in _emit(db, chunk):
                    yield cand
                chunk, chunk_bytes = [], 0
            chunk.append((rank, scope, path, slug))
            chunk_bytes += size or 0
        if chunk:
            async for cand in _emit(db, chunk):
                yield cand
        if len(page) < CANDIDATE_PAGE:
            return


async def _emit(
    db: AsyncSession, chunk: list[tuple[int, str, str, str | None]]
) -> AsyncIterator[Candidate]:
    contents = await _fetch_contents(db, chunk)
    for rank, scope, path, slug in chunk:
        content = contents.get((rank, scope, path))
        if content is not None:
            yield Candidate(rank=rank, scope=scope, path=path, slug=slug, content=content)
