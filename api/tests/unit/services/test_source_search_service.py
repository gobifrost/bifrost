"""search_source against real index rows (PostgreSQL), one contract per test."""

import hashlib
from uuid import uuid4

import pytest

from src.models.contracts.editor import SearchRequest
from src.models.orm.solutions import Solution
from src.services.file_index_service import FileIndexService
from src.services.source_search import InvalidSearchRequest, search_source


async def _seed(db, files: dict[str, str]) -> None:
    service = FileIndexService(db)
    for path, text in files.items():
        body = text.encode()
        await service.index(path, body, hashlib.sha256(body).hexdigest())
    await db.flush()


def _token() -> str:
    return f"TOK{uuid4().hex[:10]}"


async def _all_pages(db, **kw) -> list[tuple[str, int]]:
    seen, cursor = [], None
    while True:
        page = await search_source(db, SearchRequest(cursor=cursor, **kw))
        seen += [(m.file_path, m.line) for m in page.matches]
        if page.response_complete:
            return seen
        cursor = page.next_cursor


@pytest.mark.asyncio
async def test_pages_return_every_match_once_in_path_order(db_session):
    tok = _token()
    await _seed(db_session, {f"ss/{tok}/f{i:02}.txt": f"{tok}\nx\n{tok}\n" for i in range(7)})
    seen = await _all_pages(db_session, query=tok, include_pattern=f"ss/{tok}/**", limit=3)
    expected = [(f"ss/{tok}/f{i:02}.txt", line) for i in range(7) for line in (1, 3)]
    assert seen == expected


@pytest.mark.asyncio
async def test_page_two_resumes_after_last_hit_when_earlier_file_changes(db_session):
    tok = _token()
    await _seed(db_session, {f"ss/{tok}/a.txt": tok, f"ss/{tok}/b.txt": f"{tok}\n{tok}"})
    first = await search_source(db_session, SearchRequest(query=tok, include_pattern=f"ss/{tok}/**", limit=2))
    assert [(m.file_path, m.line) for m in first.matches] == [(f"ss/{tok}/a.txt", 1), (f"ss/{tok}/b.txt", 1)]
    await _seed(db_session, {f"ss/{tok}/a.txt": f"{tok}\n{tok}\n{tok}"})
    second = await search_source(
        db_session, SearchRequest(query=tok, include_pattern=f"ss/{tok}/**", limit=2, cursor=first.next_cursor)
    )
    assert [(m.file_path, m.line) for m in second.matches] == [(f"ss/{tok}/b.txt", 2)]
    assert second.response_complete and "matches 3-3" in second.guidance


@pytest.mark.asyncio
async def test_regex_query_scans_without_sql_prefilter(db_session, monkeypatch):
    import src.services.source_search.candidates as candidates

    monkeypatch.setattr(candidates, "CONTENT_CHUNK_BYTES", 10)
    tok = _token()
    await _seed(db_session, {f"ss/{tok}/{n}.py": f"v = X{n}{n}\n" for n in range(1, 5)})
    page = await search_source(
        db_session, SearchRequest(query=r"\bX\d+", is_regex=True, include_pattern=f"ss/{tok}/*.py")
    )
    assert [m.text for m in page.matches] == [f"v = X{n}{n}" for n in range(1, 5)]


@pytest.mark.asyncio
async def test_same_relative_path_in_workspace_and_solution(db_session):
    tok = _token()
    solution = Solution(id=uuid4(), slug=f"ss-{tok.lower()}", name="search test")
    db_session.add(solution)
    await db_session.flush()
    path = f"functions/{tok}.py"
    await _seed(db_session, {path: f"WS = '{tok}'\n"})
    body = f"SOL = '{tok}'\n".encode()
    await FileIndexService(db_session).index_solution(solution.id, path, body, hashlib.sha256(body).hexdigest())

    page = await search_source(db_session, SearchRequest(query=tok))
    assert [(m.source.kind, m.source.editable, m.source.solution_slug, m.text) for m in page.matches] == [
        ("workspace", True, None, f"WS = '{tok}'"),
        ("solution", False, solution.slug, f"SOL = '{tok}'"),
    ]
    only = await search_source(db_session, SearchRequest(query=tok, solution_id=solution.id))
    assert [m.source.kind for m in only.matches] == ["solution"]
    ws = await search_source(db_session, SearchRequest(query=tok, source="workspace"))
    assert [m.source.kind for m in ws.matches] == ["workspace"]


@pytest.mark.asyncio
async def test_files_mode_counts_matches_and_pages_by_file(db_session):
    tok = _token()
    await _seed(db_session, {f"ss/{tok}/a.txt": f"{tok} {tok}", f"ss/{tok}/b.txt": tok, f"ss/{tok}/c.txt": "none"})
    first = await search_source(db_session, SearchRequest(query=tok, output_mode="files", limit=1))
    assert [(f.file_path, f.match_count) for f in first.files] == [(f"ss/{tok}/a.txt", 2)]
    second = await search_source(
        db_session, SearchRequest(query=tok, output_mode="files", limit=1, cursor=first.next_cursor)
    )
    assert [(f.file_path, f.match_count) for f in second.files] == [(f"ss/{tok}/b.txt", 1)]
    assert second.response_complete


@pytest.mark.asyncio
async def test_like_wildcards_in_query_are_literal(db_session):
    tok = _token()
    await _seed(db_session, {f"ss/{tok}/a.txt": f"{tok}_x", f"ss/{tok}/b.txt": f"{tok}Ax"})
    page = await search_source(db_session, SearchRequest(query=f"{tok}_x", include_pattern=f"ss/{tok}/**"))
    assert [m.file_path for m in page.matches] == [f"ss/{tok}/a.txt"]


@pytest.mark.asyncio
async def test_unknown_solution_and_conflicting_scope_are_invalid(db_session):
    with pytest.raises(InvalidSearchRequest, match="No Solution install"):
        await search_source(db_session, SearchRequest(query="x", solution_id=uuid4()))
    with pytest.raises(InvalidSearchRequest, match="cannot be combined"):
        await search_source(db_session, SearchRequest(query="x", solution_id=uuid4(), source="workspace"))


@pytest.mark.asyncio
async def test_cursor_from_other_filters_is_invalid(db_session):
    tok = _token()
    await _seed(db_session, {f"ss/{tok}/{i}.txt": tok for i in range(3)})
    first = await search_source(db_session, SearchRequest(query=tok, limit=1))
    with pytest.raises(InvalidSearchRequest, match="does not belong"):
        await search_source(db_session, SearchRequest(query=tok, limit=1, is_regex=True, cursor=first.next_cursor))


@pytest.mark.asyncio
async def test_cursor_crosses_from_workspace_into_each_solution_exactly_once(db_session):
    tok = _token()
    solutions = [Solution(id=uuid4(), slug=f"x{n}-{tok.lower()}", name="cross") for n in range(2)]
    db_session.add_all(solutions)
    await db_session.flush()
    await _seed(db_session, {f"ss/{tok}/w{i}.txt": tok for i in range(2)})
    for sol in solutions:
        for i in range(2):
            body = f"{tok}\n".encode()
            await FileIndexService(db_session).index_solution(sol.id, f"f{i}.py", body, hashlib.sha256(body).hexdigest())

    seen, cursor = [], None
    while True:
        page = await search_source(db_session, SearchRequest(query=tok, limit=1, cursor=cursor))
        seen += [(m.source.solution_slug, m.file_path) for m in page.matches]
        if page.response_complete:
            break
        cursor = page.next_cursor
    ordered = sorted(solutions, key=lambda s: s.id)
    assert seen == [(None, f"ss/{tok}/w0.txt"), (None, f"ss/{tok}/w1.txt")] + [
        (sol.slug, f"f{i}.py") for sol in ordered for i in range(2)
    ]


@pytest.mark.asyncio
async def test_dense_file_pages_resume_inside_the_file(db_session):
    tok = _token()
    await _seed(db_session, {f"ss/{tok}/dense.txt": "\n".join(f"{tok} {tok}" for _ in range(10_000))})
    first = await search_source(db_session, SearchRequest(query=tok, limit=5))
    assert [(m.line, m.column) for m in first.matches] == [(1, 0), (1, 14), (2, 0), (2, 14), (3, 0)]
    second = await search_source(db_session, SearchRequest(query=tok, limit=5, cursor=first.next_cursor))
    assert [(m.line, m.column) for m in second.matches] == [(3, 14), (4, 0), (4, 14), (5, 0), (5, 14)]
    assert second.has_more_matches and "matches 6-10" in second.guidance
    files = await search_source(db_session, SearchRequest(query=tok, output_mode="files"))
    assert [(f.match_count, f.first_line) for f in files.files] == [(20_000, 1)]


@pytest.mark.asyncio
async def test_bad_glob_is_an_invalid_request(db_session):
    with pytest.raises(InvalidSearchRequest, match="Invalid glob"):
        await search_source(db_session, SearchRequest(query="x", include_pattern="[!]"))


@pytest.mark.asyncio
async def test_catastrophic_regex_is_rejected_quickly_instead_of_hanging(db_session):
    import time

    tok = _token()
    await _seed(db_session, {f"ss/{tok}/evil.txt": "a" * 35 + "b"})
    started = time.monotonic()
    with pytest.raises(InvalidSearchRequest, match="took too long"):
        await search_source(
            db_session, SearchRequest(query=r"(a|a)+$", is_regex=True, include_pattern=f"ss/{tok}/**")
        )
    assert time.monotonic() - started < 5
