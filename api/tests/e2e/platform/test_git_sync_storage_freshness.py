"""Git operations against real workspace storage (SeaweedFS), not a simulated one.

Editor, MCP and CLI writes land in S3 ``_repo/`` only. Commit, sync and discard
work on the persistent working tree, so they must never act on a tree that
predates those writes: sync and discard upload it back with ``--delete``.

A merge left unresolved after a conflicting sync lives only in the working
tree, so it must never be mirrored to storage either.

The working tree, including ``.git/config``, is mirrored to storage and into
retry checkpoints, so the GitHub token must never be written into it.
"""

import shutil
from pathlib import Path

import pytest
import pytest_asyncio
from git import GitCommandError, Repo
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.services.repo_storage import RepoStorage

EDITED = "notes/git_sync_freshness_edited.txt"
SAME_SIZE = "notes/git_sync_freshness_same_size.txt"
CREATED = "notes/git_sync_freshness_created.txt"
CONFLICTED = "modules/git_sync_merge_conflicted.py"
UNRELATED = "modules/git_sync_merge_unrelated.py"
PAT = "ghp_e2eSecretToken0123456789abcdef"
TOKENIZED_REMOTE = f"https://x-access-token:{PAT}@github.com/owner/repo.git"


@pytest_asyncio.fixture
async def storage():
    """Run on an empty _repo/, then put back exactly what other tests left there.

    The operations under test mirror the whole working tree, so the test owns
    every key it finds afterwards; only those are deleted.
    """
    storage = RepoStorage(get_settings())
    set_aside = {path: await storage.read(path) for path in await storage.list("")}
    for path in set_aside:
        await storage.delete(path)
    yield storage
    for path in await storage.list(""):
        await storage.delete(path)
    for path, content in set_aside.items():
        await storage.write(path, content)


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


def _legacy_workspace(path: Path) -> Path:
    """A working tree stored by an earlier release, with the token in its remote."""
    repo = Repo.init(str(path))
    repo.create_remote("origin", TOKENIZED_REMOTE)
    # Storage keeps no empty directories, so give .git/ real objects and refs.
    (path / "README.md").write_text("workspace\n")
    repo.index.add(["README.md"])
    repo.index.commit("legacy workspace")
    return path


def _assert_tree_has_no_token(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            assert PAT.encode() not in path.read_bytes(), path


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_token_never_reaches_disk_or_storage(
    db_session: AsyncSession,
    storage: RepoStorage,
    bare_repo: Path,
    work_dir: Path,
    tmp_path: Path,
):
    from src.services.git_repo_manager import GitRepoManager
    from src.services.github_sync import GitHubSyncService

    manager = GitRepoManager(get_settings())
    await manager.sync_up(_legacy_workspace(tmp_path / "legacy"))
    await storage.write(EDITED, b"content\n")
    service = GitHubSyncService(
        db=db_session, repo_url=f"file://{bare_repo}", branch="main", token=PAT,
    )

    fetched = await service.desktop_fetch()
    assert fetched.success, fetched.error
    commit = await service.desktop_commit("commit with a token configured")
    assert commit.success, commit.error
    checkpoint = await manager.checkpoint_workspace(work_dir)
    try:
        await manager.restore_workspace_checkpoint(checkpoint, tmp_path / "restored")
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)

    _assert_tree_has_no_token(work_dir)
    _assert_tree_has_no_token(tmp_path / "restored")
    for path in await storage.list(""):
        assert PAT.encode() not in await storage.read(path), path


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_disconnect_removes_stored_credentials(
    storage: RepoStorage,
    work_dir: Path,
    tmp_path: Path,
):
    from src.services.git_repo_manager import GitRepoManager

    manager = GitRepoManager(get_settings())
    legacy = _legacy_workspace(tmp_path / "legacy")
    await manager.sync_up(legacy)
    checkpoint = await manager.checkpoint_workspace(legacy)
    shutil.copytree(legacy, work_dir)
    try:
        await manager.remove_stored_credentials()
        await manager.restore_workspace_checkpoint(checkpoint, tmp_path / "restored")
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)

    for config in (
        (await storage.read(".git/config")).decode(),
        (tmp_path / "restored" / ".git" / "config").read_text(),
        (work_dir / ".git" / "config").read_text(),
    ):
        assert PAT not in config
        assert "https://github.com/owner/repo.git" in config


async def _start_conflicting_merge(
    service, storage: RepoStorage, bare_repo: Path, work_dir: Path, tmp_path: Path,
) -> Repo:
    """Leave the working tree mid-merge, as a sync that hit conflicts does.

    Storage still holds the fetched files, since a conflicting sync never
    publishes. UNRELATED carries an uncommitted edit the user may discard.
    """
    await storage.write(CONFLICTED, b"value = 'base'\n")
    await storage.write(UNRELATED, b"value = 'base'\n")
    fetched = await service.desktop_fetch()
    assert fetched.success, fetched.error

    repo = Repo(str(work_dir))
    repo.git.checkout("-B", "main")
    repo.git.add(A=True)
    repo.index.commit("base")
    repo.git.push("origin", "main")

    other = Repo.clone_from(str(bare_repo), str(tmp_path / "other"), branch="main")
    (Path(other.working_dir) / CONFLICTED).write_text("value = 'theirs'\n")
    other.git.add(A=True)
    other.index.commit("remote edit")
    other.git.push("origin", "main")

    (work_dir / CONFLICTED).write_text("value = 'ours'\n")
    repo.git.add(A=True)
    repo.index.commit("local edit")
    (work_dir / UNRELATED).write_text("value = 'edited'\n")
    repo.git.fetch("origin")
    with pytest.raises(GitCommandError):
        repo.git.merge("origin/main")
    assert (work_dir / ".git" / "MERGE_HEAD").exists()
    return repo


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_discard_during_merge_never_publishes_conflicts(
    db_session: AsyncSession,
    storage: RepoStorage,
    bare_repo: Path,
    work_dir: Path,
    tmp_path: Path,
):
    from src.core.module_cache import get_module
    from src.services.github_sync import GitHubSyncService

    service = GitHubSyncService(db=db_session, repo_url=f"file://{bare_repo}", branch="main")
    await _start_conflicting_merge(service, storage, bare_repo, work_dir, tmp_path)
    stored_before = {path: await storage.read(path) for path in await storage.list("")}

    discard = await service.desktop_discard([UNRELATED])

    assert discard.success is False
    assert "merge is in progress" in (discard.error or "")
    assert {path: await storage.read(path) for path in await storage.list("")} == stored_before
    assert b"<<<<<<<" not in await storage.read(CONFLICTED)
    module = await get_module(CONFLICTED)
    assert module is None or "<<<<<<<" not in module["content"]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_discard_after_aborting_merge_publishes_the_revert(
    db_session: AsyncSession,
    storage: RepoStorage,
    bare_repo: Path,
    work_dir: Path,
    tmp_path: Path,
):
    from src.services.github_sync import GitHubSyncService

    service = GitHubSyncService(db=db_session, repo_url=f"file://{bare_repo}", branch="main")
    await _start_conflicting_merge(service, storage, bare_repo, work_dir, tmp_path)
    aborted = await service.desktop_abort_merge()
    assert aborted.success, aborted.error

    discard = await service.desktop_discard([UNRELATED])

    assert discard.success, discard.error
    assert await storage.read(UNRELATED) == b"value = 'base'\n"
    assert not await storage.exists(".git/MERGE_HEAD")
