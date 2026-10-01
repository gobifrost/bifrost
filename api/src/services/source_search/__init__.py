"""Source search — the single search over workspace and Solution source.

REST ``POST /api/files/search`` is the only entry point; the CLI, SDK, MCP
``bifrost_file_search`` and the editor are clients of it.
"""

from __future__ import annotations

import asyncio
import time
from itertools import islice
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.path_glob import compile_glob
from src.models.contracts.editor import (
    SearchFileHit,
    SearchMatch,
    SearchRequest,
    SearchResponse,
    SearchSource,
)
from src.models.orm.solutions import Solution
from src.services.source_search.candidates import Candidate, iter_candidates
from src.services.source_search.cursor import (
    SearchPosition,
    decode_cursor,
    encode_cursor,
    fingerprint,
)
from src.services.source_search.guidance import guidance
from src.services.source_search.matching import (
    RegexTimeout,
    build_matcher,
    count_matches,
    iter_line_hits,
)


class InvalidSearchRequest(ValueError):
    """The request cannot be run as given (bad regex, glob, cursor, or scope)."""


def _source(cand: Candidate) -> SearchSource:
    if cand.rank == 0:
        return SearchSource(kind="workspace", editable=True)
    return SearchSource(
        kind="solution", solution_id=UUID(cand.scope), solution_slug=cand.slug, editable=False
    )


async def _scope_label(db: AsyncSession, request: SearchRequest) -> str:
    if request.solution_id is not None:
        slug = await db.scalar(select(Solution.slug).where(Solution.id == request.solution_id))
        if slug is None:
            raise InvalidSearchRequest(f"No Solution install with id {request.solution_id}")
        return f"Solution {slug} source"
    return {
        "all": "workspace and Solution source",
        "workspace": "workspace source",
        "solutions": "Solution source",
    }[request.source]


async def search_source(db: AsyncSession, request: SearchRequest) -> SearchResponse:
    started = time.monotonic()
    if request.solution_id is not None and request.source == "workspace":
        raise InvalidSearchRequest("solution_id cannot be combined with source='workspace'")
    fp = fingerprint(request)
    try:
        pos = decode_cursor(request.cursor, fp) if request.cursor else None
        matcher = build_matcher(request.query, request.is_regex, request.case_sensitive)
        include = compile_glob(request.include_pattern) if request.include_pattern else None
    except ValueError as exc:
        raise InvalidSearchRequest(str(exc)) from exc
    scope_label = await _scope_label(db, request)

    files_mode = request.output_mode == "files"
    seen_before = pos.seen if pos else 0
    matches: list[SearchMatch] = []
    files: list[SearchFileHit] = []
    last: SearchPosition | None = None
    has_more = False
    start = (pos.rank, pos.scope, pos.path) if pos else (0, "", "")

    async for cand in iter_candidates(db, request, start=start, include=include):
        in_cursor_file = pos is not None and (cand.rank, cand.scope, cand.path) == start
        source = _source(cand)
        if files_mode:
            if in_cursor_file:
                continue
            try:
                count, first_line = await asyncio.to_thread(count_matches, cand.content, matcher)
            except RegexTimeout as exc:
                raise InvalidSearchRequest(str(exc)) from exc
            if not count:
                continue
            if len(files) == request.limit:
                has_more = True
                break
            files.append(SearchFileHit(
                file_path=cand.path, source=source, match_count=count, first_line=first_line,
            ))
            last = SearchPosition(cand.rank, cand.scope, cand.path, 0, 0, seen_before + len(files))
            continue
        # One more hit than the page needs tells us whether more results exist.
        wanted = request.limit - len(matches) + 1
        after = (pos.line, pos.column) if in_cursor_file and pos is not None else None
        try:
            hits = await asyncio.to_thread(
                lambda: list(islice(
                    iter_line_hits(cand.content, matcher, request.context_lines, after=after), wanted,
                ))
            )
        except RegexTimeout as exc:
            raise InvalidSearchRequest(str(exc)) from exc
        for hit in hits:
            if len(matches) == request.limit:
                has_more = True
                break
            matches.append(SearchMatch(
                file_path=cand.path,
                source=source,
                line=hit.line,
                column=hit.column,
                text=hit.text,
                context_before=hit.context_before,
                context_after=hit.context_after,
            ))
            last = SearchPosition(
                cand.rank, cand.scope, cand.path, hit.line, hit.column, seen_before + len(matches)
            )
        if has_more:
            break

    returned = len(files) if files_mode else len(matches)
    next_cursor = encode_cursor(fp, last) if has_more and last is not None else None
    distinct_files = len({(m.source.kind, m.source.solution_id, m.file_path) for m in matches})
    return SearchResponse(
        query=request.query,
        output_mode=request.output_mode,
        matches=matches,
        files=files,
        returned=returned,
        has_more_matches=has_more,
        response_complete=not has_more,
        next_cursor=next_cursor,
        guidance=guidance(
            returned=returned,
            seen_before=seen_before,
            files=distinct_files,
            has_more=has_more,
            output_mode=request.output_mode,
            next_cursor=next_cursor,
            scope_label=scope_label,
        ),
        search_time_ms=int((time.monotonic() - started) * 1000),
    )
