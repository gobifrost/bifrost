"""The reconciler heals the source search index from storage, never the reverse."""

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.models.orm.file_index import FileIndex, SolutionFileIndex
from src.models.orm.solutions import Solution
from src.services.file_index_reconciler import reconcile_file_index
from src.services.file_index_service import FileIndexService
from src.services.repo_storage import RepoStorage, S3FileMetadata
from src.services.solutions.storage import SolutionStorage


def _meta(body: bytes) -> S3FileMetadata:
    return S3FileMetadata(etag="e", last_modified=datetime.now(timezone.utc), size=len(body))


class FakeRepo(RepoStorage):
    def __init__(self, objects: dict[str, bytes] | None = None):  # noqa: D107 - no S3 settings
        self.objects = dict(objects or {})

    async def list_with_metadata(self, prefix: str = "") -> dict[str, S3FileMetadata]:
        return {p: _meta(b) for p, b in self.objects.items() if p.startswith(prefix)}

    async def read(self, path: str) -> bytes:
        return self.objects[path]

    async def content_hash(self, path: str) -> str | None:
        return hashlib.sha256(self.objects[path]).hexdigest() if path in self.objects else None

    async def write(self, path: str, content: bytes) -> str:
        self.objects[path] = content
        return hashlib.sha256(content).hexdigest()


class FakeSolutionStorage(SolutionStorage):
    def __init__(self, objects: dict[str, bytes] | None = None):  # noqa: D107 - no S3 settings
        self.objects = dict(objects or {})

    async def list_with_metadata(self, prefix: str = "") -> dict[str, S3FileMetadata]:
        return {p: _meta(b) for p, b in self.objects.items() if p.startswith(prefix)}

    async def read(self, path: str) -> bytes:
        return self.objects[path]

    async def content_hash(self, path: str) -> str | None:
        return hashlib.sha256(self.objects[path]).hexdigest() if path in self.objects else None


def _no_solutions(_sid: UUID) -> SolutionStorage:
    return FakeSolutionStorage()


async def _content(db, path: str) -> str | None:
    return await db.scalar(select(FileIndex.content).where(FileIndex.path == path))


async def _exists(db, path: str) -> bool:
    return await db.scalar(select(FileIndex.path).where(FileIndex.path == path)) is not None


@pytest.mark.asyncio
async def test_stale_row_is_refreshed(db_session):
    repo = FakeRepo({"workflows/a.py": b"NEW = 1\n"})
    await FileIndexService(db_session, repo).index("workflows/a.py", b"OLD = 1\n", "0" * 64)
    stats = await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    assert stats["workspace"]["updated"] == 1
    assert await _content(db_session, "workflows/a.py") == "NEW = 1\n"


@pytest.mark.asyncio
async def test_unchanged_row_is_left_alone(db_session):
    body = b"SAME = 1\n"
    repo = FakeRepo({"workflows/same.py": body})
    await FileIndexService(db_session, repo).index("workflows/same.py", body, hashlib.sha256(body).hexdigest())
    stats = await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    # Rows left by earlier tests are orphans relative to this fake, so only the
    # seeded object's own outcome is asserted.
    assert (stats["workspace"]["added"], stats["workspace"]["updated"], stats["workspace"]["unchanged"]) == (0, 0, 1)


@pytest.mark.asyncio
async def test_orphan_row_is_removed_and_never_written_back(db_session):
    repo = FakeRepo()
    await FileIndexService(db_session, repo).index("gone.txt", b"x", "1" * 64)
    stats = await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    assert stats["workspace"]["removed"] >= 1
    assert not await _exists(db_session, "gone.txt")
    assert "gone.txt" not in repo.objects


@pytest.mark.asyncio
async def test_json_and_binary_objects_get_rows(db_session):
    repo = FakeRepo({"data/c.json": b'{"k": 1}', "img/logo.png": b"\x89PNG\x00\x00"})
    stats = await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    assert stats["workspace"]["added"] == 2
    assert await _content(db_session, "data/c.json") == '{"k": 1}'
    assert await _exists(db_session, "img/logo.png") and await _content(db_session, "img/logo.png") is None


@pytest.mark.asyncio
async def test_excluded_objects_are_not_indexed(db_session):
    repo = FakeRepo({".git/HEAD": b"ref: refs/heads/main\n", "node_modules/x/i.js": b"x"})
    stats = await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    assert stats["workspace"]["added"] == 0
    assert not await _exists(db_session, ".git/HEAD")


@pytest.mark.asyncio
async def test_oversized_object_gets_path_only_row_without_full_read(db_session, monkeypatch):
    import src.services.file_index_reconciler as reconciler

    class NoReadRepo(FakeRepo):
        async def read(self, path: str) -> bytes:
            raise AssertionError("oversized objects must not be read whole")

    monkeypatch.setattr(reconciler, "MAX_INDEXABLE_TEXT_BYTES", 4)
    repo = NoReadRepo({"big.log": b"0123456789"})
    await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    assert await _exists(db_session, "big.log") and await _content(db_session, "big.log") is None


@pytest.mark.asyncio
async def test_solution_source_is_indexed_and_healed(db_session):
    solution = Solution(id=uuid4(), slug=f"rec-{uuid4().hex[:8]}", name="Reconcile")
    db_session.add(solution)
    await db_session.flush()
    storage = FakeSolutionStorage({"functions/x.py": b"def x(): ...\n"})
    await FileIndexService(db_session).index_solution(solution.id, "functions/gone.py", b"old", "2" * 64)

    def factory(sid: UUID) -> SolutionStorage:
        return storage if sid == solution.id else FakeSolutionStorage()

    stats = await reconcile_file_index(db_session, repo_storage=FakeRepo(), solution_storage_factory=factory)
    assert stats["solutions"]["added"] >= 1 and stats["solutions"]["removed"] >= 1
    result = await db_session.execute(
        select(SolutionFileIndex.path, SolutionFileIndex.content).where(
            SolutionFileIndex.solution_id == solution.id
        )
    )
    assert {path: content for path, content in result.all()} == {"functions/x.py": "def x(): ...\n"}


@pytest.mark.asyncio
async def test_object_that_fails_to_read_is_counted_and_the_run_continues(db_session):
    class FlakyRepo(FakeRepo):
        async def read(self, path: str) -> bytes:
            if path == "gone-mid-run.txt":
                raise FileNotFoundError(path)
            return await super().read(path)

    repo = FlakyRepo({"gone-mid-run.txt": b"x", "kept.txt": b"kept"})
    solution = Solution(id=uuid4(), slug=f"rec-{uuid4().hex[:8]}", name="Reconcile")
    db_session.add(solution)
    await db_session.flush()
    storage = FakeSolutionStorage({"f.py": b"F = 1\n"})

    def factory(sid: UUID) -> SolutionStorage:
        return storage if sid == solution.id else FakeSolutionStorage()

    stats = await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=factory)
    assert stats["workspace"]["failed"] == 1
    assert await _content(db_session, "kept.txt") == "kept"
    assert stats["solutions"]["added"] >= 1


@pytest.mark.asyncio
async def test_a_write_racing_the_reconciler_is_not_overwritten(db_session):
    service = FileIndexService(db_session)
    await service.index("race.txt", b"OLD", "0" * 64)

    class RacingRepo(FakeRepo):
        async def read(self, path: str) -> bytes:
            body = await super().read(path)
            if path == "race.txt":  # a user saves while the reconciler holds stale bytes
                await service.index("race.txt", b"NEWEST", hashlib.sha256(b"NEWEST").hexdigest())
            return body

    repo = RacingRepo({"race.txt": b"STALE-READ"})
    await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    assert await _content(db_session, "race.txt") == "NEWEST"


@pytest.mark.asyncio
async def test_large_object_that_vanishes_gets_no_row(db_session, monkeypatch):
    import src.services.file_index_reconciler as reconciler

    class VanishingRepo(FakeRepo):
        async def content_hash(self, path: str) -> str | None:
            return None

    monkeypatch.setattr(reconciler, "MAX_INDEXABLE_TEXT_BYTES", 4)
    repo = VanishingRepo({"huge.bin": b"0123456789"})
    await reconcile_file_index(db_session, repo_storage=repo, solution_storage_factory=_no_solutions)
    assert not await _exists(db_session, "huge.bin")
