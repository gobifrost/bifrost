"""Tests for FileIndexService — the single writer of the source search index."""

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.file_index import FileIndex, SolutionFileIndex
from src.models.orm.solutions import Solution
from src.services.repo_storage import RepoStorage
from src.services.file_index_service import (
    MAX_INDEXABLE_TEXT_BYTES,
    FileIndexService,
    indexable_text,
    is_tracked_path,
)


class FakeRepoStorage(RepoStorage):
    def __init__(self):
        super().__init__()
        self.objects: dict[str, bytes] = {}

    async def write(self, path: str, content: bytes) -> str:
        self.objects[path] = content
        return hashlib.sha256(content).hexdigest()

    async def delete(self, path: str) -> None:
        self.objects.pop(path, None)


# --- policy -----------------------------------------------------------------


@pytest.mark.parametrize("content", [b'{"a": 1}', b"Write-Host hi\r\n", b"FROM python:3.11\n"])
def test_any_utf8_text_is_indexed_regardless_of_extension(content):
    assert indexable_text(content) == content.decode("utf-8")


def test_utf8_bom_is_stripped():
    assert indexable_text(b"\xef\xbb\xbfhello") == "hello"


@pytest.mark.parametrize(
    "content",
    [b"PK\x03\x04\x00\x00", b"\xff\xfe\xfa", b"a" * (MAX_INDEXABLE_TEXT_BYTES + 1)],
)
def test_binary_invalid_or_oversized_is_path_only(content):
    assert indexable_text(content) is None


def test_empty_file_is_indexed_as_empty_text():
    assert indexable_text(b"") == ""


@pytest.mark.parametrize("path", [".git/HEAD", "node_modules/x/index.js", "a/__pycache__/m.pyc", ".env"])
def test_excluded_paths_are_not_tracked(path):
    assert is_tracked_path(path) is False


@pytest.mark.parametrize("path", ["workflows/x.py", "data/config.json", "scripts/Fix.ps1", "Dockerfile"])
def test_source_paths_are_tracked(path):
    assert is_tracked_path(path) is True


# --- workspace rows ---------------------------------------------------------


async def _row(db, path):
    return (await db.execute(select(FileIndex).where(FileIndex.path == path))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_write_stores_bytes_and_indexes_json(db_session):
    repo = FakeRepoStorage()
    await FileIndexService(db_session, repo).write("data/c.json", b'{"k": 1}', updated_by="me")
    row = await _row(db_session, "data/c.json")
    assert repo.objects["data/c.json"] == b'{"k": 1}'
    assert (row.content, row.content_hash, row.updated_by) == (
        '{"k": 1}', hashlib.sha256(b'{"k": 1}').hexdigest(), "me",
    )


@pytest.mark.asyncio
async def test_binary_write_gets_path_only_row(db_session):
    await FileIndexService(db_session, FakeRepoStorage()).write("img/logo.png", b"\x89PNG\x00\x00")
    row = await _row(db_session, "img/logo.png")
    assert row is not None and row.content is None


@pytest.mark.asyncio
async def test_excluded_path_write_has_no_row(db_session):
    await FileIndexService(db_session, FakeRepoStorage()).write("node_modules/x.js", b"x")
    assert await _row(db_session, "node_modules/x.js") is None


@pytest.mark.asyncio
async def test_delete_removes_object_and_row(db_session):
    repo = FakeRepoStorage()
    service = FileIndexService(db_session, repo)
    await service.write("workflows/t.py", b"x = 1\n")
    await service.delete("workflows/t.py")
    assert "workflows/t.py" not in repo.objects and await _row(db_session, "workflows/t.py") is None


@pytest.mark.asyncio
async def test_move_index_carries_content(db_session):
    service = FileIndexService(db_session, FakeRepoStorage())
    await service.write("a/old.py", b"OLD = 1\n")
    await service.move_index("a/old.py", "a/new.py")
    assert await _row(db_session, "a/old.py") is None
    assert (await _row(db_session, "a/new.py")).content == "OLD = 1\n"


# --- solution rows ----------------------------------------------------------


@pytest.mark.asyncio
async def test_solution_rows_are_separate_from_workspace(db_session):
    solution = Solution(id=uuid4(), slug=f"idx-{uuid4().hex[:8]}", name="Index test")
    db_session.add(solution)
    await db_session.flush()
    service = FileIndexService(db_session, FakeRepoStorage())
    body = b"def f(): ...\n"
    await service.index_solution(solution.id, "functions/foo.py", body, hashlib.sha256(body).hexdigest())

    sol_row = (await db_session.execute(
        select(SolutionFileIndex).where(SolutionFileIndex.solution_id == solution.id)
    )).scalar_one()
    assert (sol_row.path, sol_row.content) == ("functions/foo.py", "def f(): ...\n")
    assert await _row(db_session, "functions/foo.py") is None

    await service.unindex_solution(solution.id, "functions/foo.py")
    assert (await db_session.execute(
        select(SolutionFileIndex).where(SolutionFileIndex.solution_id == solution.id)
    )).first() is None


@pytest.mark.asyncio
async def test_deleting_a_solution_drops_its_index_rows(db_session):
    solution = Solution(id=uuid4(), slug=f"idx-{uuid4().hex[:8]}", name="Cascade test")
    db_session.add(solution)
    await db_session.flush()
    await FileIndexService(db_session, FakeRepoStorage()).index_solution(solution.id, "a.py", b"A\n", "h")
    await db_session.delete(solution)
    await db_session.flush()
    assert (await db_session.execute(
        select(SolutionFileIndex).where(SolutionFileIndex.solution_id == solution.id)
    )).first() is None
