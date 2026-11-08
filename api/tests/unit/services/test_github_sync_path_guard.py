"""Selected Git paths stay confined without changing literal filename behavior."""

from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from git import Repo
from git.index.typ import BaseIndexEntry
from gitdb import IStream

from src.services.github_sync import GitHubSyncService


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    repo = Repo.init(root)
    repo.git.config("user.name", "Git Path Test")
    repo.git.config("user.email", "git-path@example.invalid")
    (root / "baseline.txt").write_text("baseline\n")
    repo.index.add(["baseline.txt"])
    repo.index.commit("baseline")

    @asynccontextmanager
    async def checkout(*, readonly=False):
        yield root

    service = GitHubSyncService.__new__(GitHubSyncService)
    service.branch = "main"
    service.repo_manager = SimpleNamespace(
        workspace=checkout,
        ensure_storage_unchanged=AsyncMock(),
        sync_up=AsyncMock(),
    )
    service._open_or_init = Mock(return_value=repo)
    service._update_file_index = AsyncMock()
    service.db = AsyncMock()
    monkeypatch.setattr("src.core.module_cache.refresh_modules_from_directory", AsyncMock())
    yield root, repo, service
    repo.close()


def _commit(repo, root, path, content="before\n"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    with repo.git.custom_environment(GIT_LITERAL_PATHSPECS="1"):
        repo.git.add("--", path)
    repo.index.commit("selected file")
    return target


def _conflict(repo, path, *, symlink_stage=None, target=b"../outside.txt"):
    index = repo.index
    index.entries.pop((path, 0), None)
    for stage, content in [(2, b"ours\n"), (3, b"theirs\n")]:
        mode = 0o120000 if stage == symlink_stage else 0o100644
        data = target if stage == symlink_stage else content
        blob = repo.odb.store(IStream(b"blob", len(data), BytesIO(data)))
        index.entries[(path, stage)] = BaseIndexEntry((mode, blob.binsha, stage << 12, path))
    index.write()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["diff", "discard", "resolve"])
@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "directory-symlink", "dangling", "metadata", "metadata-alias", "root", "nul"])
async def test_unsafe_selected_path_never_reaches_git(workspace, tmp_path, operation, kind):
    root, repo, service = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("private outside bytes\n")
    (root / "escape").symlink_to(outside)
    (root / "escape-dir").symlink_to(tmp_path, target_is_directory=True)
    (root / "dangling").symlink_to(tmp_path / "missing.txt")
    (root / "metadata-alias").symlink_to(root / ".git" / "config")
    path = {
        "traversal": "../outside.txt", "absolute": str(outside),
        "symlink": "escape", "dangling": "dangling",
        "directory-symlink": "escape-dir/outside.txt",
        "metadata": ".git/config", "metadata-alias": "metadata-alias",
        "root": ".", "nul": "bad\0path",
    }[kind]
    before = (root / ".git" / "index").read_bytes()

    if operation == "diff":
        result = await service.desktop_diff(path)
        assert result.head_content is None and result.working_content is None
    elif operation == "discard":
        assert not (await service.desktop_discard([path])).success
    else:
        assert not (await service.desktop_resolve({path: "ours"})).success

    service._open_or_init.assert_not_called()
    service.repo_manager.sync_up.assert_not_awaited()
    assert outside.read_text() == "private outside bytes\n"
    assert (root / ".git" / "index").read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [".bifrost/configs.yaml", "-leading.txt", "brackets[1].txt", ":(glob)*.txt", "spaces ü.txt", "back\\slash.txt", "%2e%2e.txt"])
async def test_diff_and_discard_preserve_literal_filenames(workspace, path):
    root, repo, service = workspace
    selected = _commit(repo, root, path)
    sibling = _commit(repo, root, "brackets1.txt")
    selected.write_text("selected edit\n")
    sibling.write_text("keep sibling edit\n")

    diff = await service.desktop_diff(path)
    assert diff.head_content == "before"
    assert diff.working_content == "selected edit\n"
    discarded = await service.desktop_discard([path])

    assert discarded.success, discarded.error
    assert discarded.discarded == [path]
    assert selected.read_text() == "before\n"
    assert sibling.read_text() == "keep sibling edit\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["./baseline.txt", "nested/../baseline.txt", "nested//file.txt"])
async def test_safe_relative_aliases_remain_usable(workspace, path):
    root, repo, service = workspace
    (root / "nested").mkdir()
    _commit(repo, root, "nested/file.txt")
    target = root / path
    original = target.read_text()
    target.write_text("edit\n")

    assert (await service.desktop_diff(path)).working_content == "edit\n"
    result = await service.desktop_discard([path])

    assert result.success, result.error
    assert target.read_text() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discard", "resolve"])
async def test_invalid_later_target_cannot_partially_mutate_batch(workspace, operation):
    root, repo, service = workspace
    selected = root / "baseline.txt"
    selected.write_text("keep edit\n")
    before = (root / ".git" / "index").read_bytes()
    if operation == "discard":
        result = await service.desktop_discard(["baseline.txt", "../outside.txt"])
    else:
        result = await service.desktop_resolve({"baseline.txt": "ours", "../outside.txt": "ours"})
    assert not result.success
    service._open_or_init.assert_not_called()
    assert selected.read_text() == "keep edit\n"
    assert (root / ".git" / "index").read_bytes() == before


@pytest.mark.asyncio
async def test_discard_rechecks_path_after_checkout_hydration(workspace, tmp_path):
    root, repo, service = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("keep outside bytes\n")

    async def hydrate(work_dir, branch):
        (work_dir / "hydrated").symlink_to(tmp_path, target_is_directory=True)

    service.repo_manager.ensure_storage_unchanged.side_effect = hydrate

    result = await service.desktop_discard(["hydrated/outside.txt"])

    assert not result.success
    service._open_or_init.assert_not_called()
    service.repo_manager.sync_up.assert_not_awaited()
    assert outside.read_text() == "keep outside bytes\n"


@pytest.mark.asyncio
async def test_untracked_safe_symlink_discard_removes_link_only(workspace):
    root, repo, service = workspace
    link = root / "link.txt"
    link.symlink_to("baseline.txt")

    result = await service.desktop_discard(["link.txt"])

    assert result.success, result.error
    assert not link.is_symlink()
    assert (root / "baseline.txt").read_text() == "baseline\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_path", ["links/external", "links"])
async def test_discard_rejects_escape_in_restored_head_symlink(workspace, tmp_path, selected_path):
    root, repo, service = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    link = root / "links" / "external"
    link.parent.mkdir()
    link.symlink_to(outside)
    repo.index.add(["links/external"])
    repo.index.commit("tracked symlink")
    link.unlink()
    link.write_text("keep current file\n")

    result = await service.desktop_discard([selected_path])

    assert not result.success
    assert not link.is_symlink()
    assert link.read_text() == "keep current file\n"
    service.repo_manager.sync_up.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution,stage", [("ours", 2), ("theirs", 3)])
@pytest.mark.parametrize("target", [b"../outside.txt", b"newline\n"])
async def test_resolve_rejects_escape_in_selected_conflict_symlink(workspace, tmp_path, resolution, stage, target):
    root, repo, service = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    (root / "newline\n").symlink_to(outside)
    path = "baseline.txt"
    _conflict(repo, path, symlink_stage=stage, target=target)
    before = (root / ".git" / "index").read_bytes()

    result = await service.desktop_resolve({path: resolution})

    assert not result.success
    assert (root / path).read_text() == "baseline\n"
    assert not (root / path).is_symlink()
    assert (root / ".git" / "index").read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [".bifrost/configs.yaml", "-leading.txt", "brackets[1].txt", ":(glob)*.txt", "spaces ü.txt"])
async def test_resolve_preserves_literal_names_and_manifest_paths(workspace, path):
    root, repo, service = workspace
    selected = _commit(repo, root, path)
    sibling = _commit(repo, root, "brackets1.txt")
    sibling.write_text("keep sibling edit\n")
    _conflict(repo, path)

    result = await service.desktop_resolve({path: "ours"})

    assert result.success, result.error
    assert selected.read_text() == "ours\n"
    assert sibling.read_text() == "keep sibling edit\n"
    assert not repo.index.unmerged_blobs()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["baseline.txt", "safe\n"])
async def test_discard_can_restore_safe_in_workspace_symlink(workspace, target):
    root, repo, service = workspace
    if target != "baseline.txt":
        _commit(repo, root, target, "baseline\n")
    link = root / "link.txt"
    link.symlink_to(target)
    repo.index.add(["link.txt"])
    repo.index.commit("safe symlink")
    link.unlink()
    link.write_text("replace this edit\n")

    result = await service.desktop_discard(["link.txt"])

    assert result.success, result.error
    assert link.is_symlink()
    assert link.read_text() == "baseline\n"
