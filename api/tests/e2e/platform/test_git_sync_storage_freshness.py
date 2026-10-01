"""Git operations against real workspace storage (SeaweedFS), not a simulated one.

Editor, MCP and CLI writes land in S3 ``_repo/`` only. Commit, sync and discard
work on the persistent working tree, so they must never act on a tree that
predates those writes: sync and discard upload it back with ``--delete``.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from git import Repo
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.services.repo_storage import RepoStorage

EDITED = "notes/git_sync_freshness_edited.txt"
SAME_SIZE = "notes/git_sync_freshness_same_size.txt"
CREATED = "notes/git_sync_freshness_created.txt"


async def _wipe_repo_storage(storage: RepoStorage) -> None:
    for path in await storage.list(""):
        await storage.delete(path)


@pytest_asyncio.fixture
async def storage():
    storage = RepoStorage(get_settings())
    await _wipe_repo_storage(storage)
    yield storage
    await _wipe_repo_storage(storage)


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    Repo.init(str(path), bare=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    return path


@pytest.fixture
def work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "git"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", path)
    return path


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_platform_edits_after_fetch_are_never_overwritten(
    db_session: AsyncSession,
    storage: RepoStorage,
    bare_repo: Path,
    work_dir: Path,
):
    from src.services.github_sync import GitHubSyncService

    service = GitHubSyncService(db=db_session, repo_url=f"file://{bare_repo}", branch="main")
    await storage.write(EDITED, b"first version\n")
    await storage.write(SAME_SIZE, b"value=1\n")
    fetched = await service.desktop_fetch()
    assert fetched.success, fetched.error
    assert (work_dir / SAME_SIZE).read_bytes() == b"value=1\n"

    # Platform writes after the fetch reach storage only.
    await storage.write(EDITED, b"second, longer version\n")
    await storage.write(SAME_SIZE, b"value=2\n")
    await storage.write(CREATED, b"created in the editor\n")

    commit = await service.desktop_commit("stale commit")
    sync = await service.desktop_sync()
    discard = await service.desktop_discard([EDITED])

    assert await storage.read(EDITED) == b"second, longer version\n"
    assert await storage.read(SAME_SIZE) == b"value=2\n"
    assert await storage.read(CREATED) == b"created in the editor\n"
    for result in (commit, sync, discard):
        assert result.success is False
        assert "Run Fetch" in (result.error or "")

    # Fetch picks the edits up, including one that kept the file's size.
    fetched = await service.desktop_fetch()
    assert fetched.success, fetched.error
    commit = await service.desktop_commit("fresh commit")
    assert commit.success, commit.error
    head = Repo(str(work_dir)).head.commit
    assert (head.tree / EDITED).data_stream.read() == b"second, longer version\n"
    assert (head.tree / SAME_SIZE).data_stream.read() == b"value=2\n"
    assert (head.tree / CREATED).data_stream.read() == b"created in the editor\n"
