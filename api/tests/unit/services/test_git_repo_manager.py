"""Tests for GitRepoManager — S3-backed persistent git working tree."""

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.git_repo_manager import GitRepoManager, WorkspaceStorageChanged
from src.config import Settings


def test_repository_content_cannot_impersonate_git_metadata(tmp_path):
    """Content restored beside .git must not redirect native index commits."""
    from git import Repo

    initialized = Repo.init(tmp_path, initial_branch="main")
    for name in ("objects", "refs", "hooks"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "README").write_text("ordinary tracked content")
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")

    restored = Repo(tmp_path)
    assert Path(restored.git_dir) == tmp_path / ".git"
    restored.index.add(["HEAD", "objects/README", "refs/README", "hooks/README"])
    from git import Actor

    actor = Actor("Test", "test@example.com")
    commit = restored.index.commit("Preserve repository content", author=actor, committer=actor)
    assert initialized.head.commit.hexsha == commit.hexsha
    assert (commit.tree / "hooks" / "README").data_stream.read() == b"ordinary tracked content"


@pytest.mark.parametrize("configured", [None, "/tmp/bifrost/solution-repo-fixtures/git-checkouts/worker"])
def test_fresh_executor_import_uses_configured_checkout_parent(monkeypatch, configured):
    import subprocess
    import sys

    if configured is None:
        monkeypatch.delenv("BIFROST_GIT_WORK_DIR", raising=False)
    else:
        monkeypatch.setenv("BIFROST_GIT_WORK_DIR", configured)
    result = subprocess.run(
        [sys.executable, "-c", "from src.services.git_repo_manager import PERSISTENT_WORK_DIR; print(PERSISTENT_WORK_DIR)"],
        check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == (configured or "/tmp/git")


@pytest.fixture
def mock_settings():
    """Create mock settings for S3 configuration."""
    return Settings(_env_file=None, s3_bucket="bifrost-local", s3_endpoint_url="http://seaweedfs:8333",
        s3_access_key="bifrost", s3_secret_key="bifrost123", s3_region="us-east-1", redis_url="")


@pytest.fixture
def manager(mock_settings):
    """Create a GitRepoManager with mock settings."""
    return GitRepoManager(settings=mock_settings)








class TestHasGitDir:
    """Tests for has_git_dir existence check."""

    @pytest.mark.asyncio
    async def test_returns_true_when_git_head_exists(self, manager):
        with patch("src.services.repo_storage.RepoStorage.exists", new_callable=AsyncMock, return_value=True) as mock_exists:
            result = await manager.has_git_dir()
            assert result is True
            mock_exists.assert_awaited_once_with(".git/HEAD")

    @pytest.mark.asyncio
    async def test_returns_false_when_no_git_dir(self, manager):
        with patch("src.services.repo_storage.RepoStorage.exists", new_callable=AsyncMock, return_value=False) as mock_exists:
            result = await manager.has_git_dir()
            assert result is False
            mock_exists.assert_awaited_once_with(".git/HEAD")




class TestSyncDown:
    """Tests for sync_down operation."""


    @pytest.mark.asyncio
    @pytest.mark.parametrize("restore", ["workspace", "checkpoint"])
    async def test_empty_repository_survives_file_only_storage_round_trip(self, manager, tmp_path, restore):
        import shutil

        from git import Repo

        source = tmp_path / "source"
        Repo.init(source, initial_branch="main")
        target = tmp_path / "fresh-worker"

        async def download_files(_prefix, _target):
            for path in source.rglob("*"):
                if path.is_file():
                    destination = target / path.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
            assert not (target / ".git" / "objects").exists()
            assert not (target / ".git" / "refs").exists()

        with patch.object(manager._mirror, "download", side_effect=download_files), \
             patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            if restore == "workspace":
                await manager.sync_down(target)
            else:
                await manager.restore_workspace_checkpoint(str(uuid4()), target)

        restored = Repo(target)
        assert restored.git.symbolic_ref("HEAD") == "refs/heads/main"
        assert not restored.head.is_valid()

    @pytest.mark.asyncio
    async def test_calls_selected_mirror_with_workspace_prefix(self, manager, tmp_path):
        with patch.object(manager._mirror, "download", new_callable=AsyncMock) as mock_run, \
             patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            await manager.sync_down(tmp_path)
            mock_run.assert_awaited_once()
            mock_run.assert_awaited_once_with("_repo/", tmp_path)

    @pytest.mark.asyncio
    async def test_creates_target_dir(self, manager):
        import tempfile
        target = Path(tempfile.mkdtemp()) / "subdir"
        with patch.object(manager._mirror, "download", new_callable=AsyncMock), \
             patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            await manager.sync_down(target)
            assert target.exists()
        # Cleanup
        import shutil
        shutil.rmtree(target.parent)


class TestSyncUp:
    """Tests for sync_up operation."""

    @pytest.mark.asyncio
    async def test_calls_aws_sync_with_delete(self, manager, tmp_path):
        with patch.object(manager._mirror, "upload", new_callable=AsyncMock) as mock_run, \
             patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            await manager.sync_up(tmp_path)
            mock_run.assert_awaited_once()
            mock_run.assert_awaited_once_with(tmp_path, "_repo/")


class TestStorageBaseline:
    """A working dir is only trusted while S3 _repo/ still matches its last sync."""

    @pytest.mark.asyncio
    async def test_empty_worker_hydrates_before_checking_freshness(self, manager, tmp_path):
        async def transfer(_prefix, _target):
            (tmp_path / ".git").mkdir()

        with patch.object(manager._mirror, "download", side_effect=transfer) as transfer_mock, \
             patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            await manager.ensure_storage_unchanged(tmp_path, "main")
            transfer_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_worker_rejects_write_during_hydration(self, manager, tmp_path):
        async def transfer(_prefix, _target):
            (tmp_path / ".git").mkdir()

        with patch.object(manager._mirror, "download", side_effect=transfer), \
             patch.object(manager, "_storage_fingerprint", AsyncMock(side_effect=["before", "after"])):
            with pytest.raises(WorkspaceStorageChanged, match="Run Fetch"):
                await manager.ensure_storage_unchanged(tmp_path, "main")

    @pytest.mark.asyncio
    async def test_nonempty_worker_is_not_hydrated(self, manager, tmp_path):
        local = tmp_path / "local-only.txt"
        local.write_text("do not overwrite")
        with patch.object(manager, "sync_down", new_callable=AsyncMock) as transfer:
            with pytest.raises(WorkspaceStorageChanged, match="Nothing was changed"):
                await manager.ensure_storage_unchanged(tmp_path, "main")
            transfer.assert_not_awaited()
        assert local.read_text() == "do not overwrite"

    @pytest.mark.asyncio
    async def test_empty_worker_without_persisted_git_is_refused(self, manager, tmp_path):
        with patch.object(manager._mirror, "download", new_callable=AsyncMock), \
             patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            with pytest.raises(WorkspaceStorageChanged, match="Run Fetch"):
                await manager.ensure_storage_unchanged(tmp_path, "main")

    @pytest.mark.asyncio
    async def test_sync_down_trusts_the_listing_taken_before_transfer(self, manager, tmp_path):
        (tmp_path / ".git").mkdir()
        fingerprints = iter(["before-transfer", "after-transfer"])

        async def fingerprint():
            return next(fingerprints)

        with patch.object(manager._mirror, "download", new_callable=AsyncMock), \
             patch.object(manager, "_storage_fingerprint", side_effect=fingerprint):
            await manager.sync_down(tmp_path)
            # A write that landed during the transfer must not be trusted.
            with pytest.raises(WorkspaceStorageChanged, match="Run Fetch"):
                await manager.ensure_storage_unchanged(tmp_path, "main")

    @pytest.mark.asyncio
    async def test_sync_up_trusts_the_listing_taken_after_transfer(self, manager, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch.object(manager._mirror, "upload", new_callable=AsyncMock), \
             patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            await manager.sync_up(tmp_path)
            await manager.ensure_storage_unchanged(tmp_path, "main")

    @pytest.mark.asyncio
    async def test_working_dir_without_a_baseline_is_refused(self, manager, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            with pytest.raises(WorkspaceStorageChanged, match="Nothing was changed"):
                await manager.ensure_storage_unchanged(tmp_path, "main")

    @pytest.mark.asyncio
    async def test_failed_transfer_leaves_no_baseline(self, manager, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")), \
             patch.object(manager._mirror, "upload", AsyncMock(return_value=None)):
            await manager.sync_up(tmp_path)
        with patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")), \
             patch.object(manager._mirror, "upload", AsyncMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(RuntimeError):
                await manager.sync_up(tmp_path)
            with pytest.raises(WorkspaceStorageChanged):
                await manager.ensure_storage_unchanged(tmp_path, "main")


class TestRefusalNamesUnsyncedWork:
    """Fetch mirrors S3 over the working dir, so the refusal names what it discards."""

    @staticmethod
    def _repo(path: Path):
        from git import Repo

        repo = Repo.init(str(path), initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("user", "name", "Test")
            config.set_value("user", "email", "test@example.com")
        return repo

    @staticmethod
    def _commit(repo, name: str, content: str, message: str) -> None:
        (Path(repo.working_dir) / name).write_text(content)
        repo.index.add([name])
        repo.index.commit(message)

    async def _refusal(self, manager, work_dir: Path) -> str:
        with patch.object(manager, "_storage_fingerprint", AsyncMock(return_value="fp")):
            with pytest.raises(WorkspaceStorageChanged) as error:
                await manager.ensure_storage_unchanged(work_dir, "main")
        message = str(error.value)
        assert "Nothing was changed" in message
        return message

    @pytest.mark.asyncio
    async def test_no_warning_when_everything_is_synced(self, manager, tmp_path):
        repo = self._repo(tmp_path)
        self._commit(repo, "a.txt", "a", "published")
        repo.git.update_ref("refs/remotes/origin/main", repo.head.commit.hexsha)

        message = await self._refusal(manager, tmp_path)

        assert "You have" not in message

    @pytest.mark.asyncio
    async def test_names_local_commits_not_yet_synced(self, manager, tmp_path):
        repo = self._repo(tmp_path)
        self._commit(repo, "a.txt", "a", "published")
        repo.git.update_ref("refs/remotes/origin/main", repo.head.commit.hexsha)
        self._commit(repo, "b.txt", "b", "local only")

        message = await self._refusal(manager, tmp_path)

        assert "You have 1 local commit not yet synced" in message
        assert "redo it" in message

    @pytest.mark.asyncio
    async def test_counts_every_commit_when_never_fetched(self, manager, tmp_path):
        repo = self._repo(tmp_path)
        self._commit(repo, "a.txt", "a", "first")
        self._commit(repo, "b.txt", "b", "second")

        message = await self._refusal(manager, tmp_path)

        assert "You have 2 local commits not yet synced" in message

    @pytest.mark.asyncio
    async def test_names_an_unfinished_merge(self, manager, tmp_path):
        from git import GitCommandError

        repo = self._repo(tmp_path)
        self._commit(repo, "a.txt", "base", "base")
        repo.create_head("other")
        self._commit(repo, "a.txt", "ours", "ours")
        repo.heads.other.checkout()
        self._commit(repo, "a.txt", "theirs", "theirs")
        repo.heads.main.checkout()
        with pytest.raises(GitCommandError):
            repo.git.merge("other")

        message = await self._refusal(manager, tmp_path)

        assert "You have an unfinished merge with conflict resolutions" in message


class TestCheckout:
    """Tests for checkout context manager lifecycle (persistent working dir)."""

    @pytest.mark.asyncio
    async def test_syncs_down_yields_persistent_dir_syncs_up(self, manager):
        call_order = []

        async def mock_sync_down(target):
            call_order.append(("sync_down", target))

        async def mock_sync_up(source):
            call_order.append(("sync_up", source))

        with patch.object(manager, "sync_down", side_effect=mock_sync_down), \
             patch.object(manager, "sync_up", side_effect=mock_sync_up), \
             patch.object(manager, "_acquire_lock") as mock_lock:
            # Mock the lock as an async context manager that does nothing
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            async with manager.checkout() as work_dir:
                assert work_dir.exists()
                assert work_dir.is_dir()
                call_order.append(("yield", work_dir))

        # Verify call order: sync_down -> yield -> sync_up
        assert len(call_order) == 3
        assert call_order[0][0] == "sync_down"
        assert call_order[1][0] == "yield"
        assert call_order[2][0] == "sync_up"

        # All received the same persistent path
        assert call_order[0][1] == call_order[1][1]
        assert call_order[2][1] == call_order[1][1]

        # Persistent dir is NOT cleaned up (by design)
        assert work_dir == manager.work_dir

    @pytest.mark.asyncio
    async def test_persistent_dir_survives_exception(self, manager):
        with patch.object(manager, "sync_down", new_callable=AsyncMock), \
             patch.object(manager, "sync_up", new_callable=AsyncMock), \
             patch.object(manager, "_acquire_lock") as mock_lock:
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="test error"):
                async with manager.checkout() as work_dir:
                    raise ValueError("test error")
            # Persistent dir still exists (not deleted on error)
            assert work_dir == manager.work_dir

    @pytest.mark.asyncio
    async def test_sync_up_not_called_on_sync_down_failure(self, manager):
        """If sync_down fails, sync_up should not be called."""
        with patch.object(
            manager, "sync_down",
            side_effect=RuntimeError("download failed"),
        ), \
             patch.object(manager, "sync_up", new_callable=AsyncMock) as mock_up, \
             patch.object(manager, "_acquire_lock") as mock_lock:
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="download failed"):
                async with manager.checkout():
                    pass  # pragma: no cover
            mock_up.assert_not_awaited()


@pytest.mark.parametrize("writer_fails", [False, True])
async def test_isolated_checkout_publishes_without_replacing_pending_work(
    manager, tmp_path, monkeypatch, writer_fails,
):
    from src.services.git_workspace_state import GitWorkspaceState, tree_fingerprint

    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "draft.txt").write_text("unacknowledged local work")
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", pending)
    state = GitWorkspaceState(manager)
    state.record(pending, "acknowledged-etag", tree_fingerprint(pending))
    receipt = state._receipt_path(pending).read_bytes()

    async def download(target):
        target.mkdir(parents=True)
        (target / "draft.txt").write_text("published content")
        await manager._record_source_receipt(target)

    uploaded = []

    async def upload(source):
        uploaded.append((source / "draft.txt").read_text())

    monkeypatch.setattr(manager, "sync_down", download)
    monkeypatch.setattr(manager, "sync_up", upload)
    views = []

    async def write():
        async with manager.checkout(isolated=True) as view:
            views.append(view)
            (view / "draft.txt").write_text("regenerated published content")
            if writer_fails:
                raise RuntimeError("writer failed")

    if writer_fails:
        with pytest.raises(RuntimeError, match="writer failed"):
            await write()
    else:
        await write()
    assert uploaded == ([] if writer_fails else ["regenerated published content"])
    assert (pending / "draft.txt").read_text() == "unacknowledged local work"
    assert state._receipt_path(pending).read_bytes() == receipt
    assert not views[0].parent.exists()


@pytest.mark.parametrize("reader_fails", [False, True])
async def test_readonly_checkout_preserves_unacknowledged_local_work(
    manager, tmp_path, monkeypatch, reader_fails,
):
    from src.services.git_workspace_state import GitWorkspaceState, tree_fingerprint

    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "draft.txt").write_text("unacknowledged local work")
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", pending)
    state = GitWorkspaceState(manager)
    state.record(pending, "acknowledged-etag", tree_fingerprint(pending))
    receipt = state._receipt_path(pending).read_bytes()

    async def published_snapshot(target):
        target.mkdir(parents=True, exist_ok=True)
        (target / "draft.txt").write_text("published content")
        await manager._record_source_receipt(target)

    monkeypatch.setattr(manager, "sync_down", published_snapshot)
    views = []

    async def read():
        async with manager.checkout_readonly() as view:
            views.append(view)
            assert (view / "draft.txt").read_text() == "published content"
            if reader_fails:
                raise RuntimeError("reader failed")
            return view

    if reader_fails:
        with pytest.raises(RuntimeError, match="reader failed"):
            await read()
    else:
        await read()
    assert (pending / "draft.txt").read_text() == "unacknowledged local work"
    assert state._receipt_path(pending).read_bytes() == receipt
    assert not views[0].parent.exists()  # includes the snapshot's sidecar receipt


def test_checkpoint_prefix_is_uuid_scoped(manager):
    checkpoint = str(uuid4())
    assert manager._checkpoint_prefix(checkpoint) == f"_workspace_sync_checkpoints/{checkpoint}/"
    with pytest.raises(ValueError, match="Invalid workspace checkpoint ID"):
        manager._checkpoint_prefix("../../repo")


def test_baseline_hash_uses_canonical_versions_without_stripping_opaque_bytes():
    import hashlib
    from src.services.git_repo_manager import workspace_version_fingerprint

    assert workspace_version_fingerprint({"file": "canonical-v1"}) == hashlib.sha256(b"file\0canonical-v1\0").hexdigest()
    token = '"opaque-token-is-not-an-http-header"'
    assert workspace_version_fingerprint({"file": token}) == hashlib.sha256(b"file\0" + token.encode() + b"\0").hexdigest()


async def test_sync_up_supplies_canonical_baseline_guard_before_provider_upload(manager, tmp_path):
    from src.services.git_repo_manager import STORAGE_BASELINE_FILE, workspace_version_fingerprint
    from src.core.storage.contracts import ObjectVersion
    (tmp_path / ".git").mkdir()
    original = {"file": "old-version"}
    (tmp_path / ".git" / STORAGE_BASELINE_FILE).write_text(workspace_version_fingerprint(original))
    async def changed_upload(source, prefix, *, validate_snapshot):
        assert source == tmp_path and prefix == "_repo/"
        validate_snapshot({"file": ObjectVersion("concurrent-writer")})
    with patch.object(manager._mirror, "upload", side_effect=changed_upload):
        with pytest.raises(WorkspaceStorageChanged, match="before upload"):
            await manager.sync_up(tmp_path)
    assert not (tmp_path / ".git" / STORAGE_BASELINE_FILE).exists()
