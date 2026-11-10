"""Faults at the immutable-checkpoint / conditional-pointer publication seam."""

import asyncio
import json
import shutil
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextlib import asynccontextmanager
from src.core.storage.contracts import ContentProperties, ObjectInfo, ObjectVersion, StorageError, WriteResult
from src.services.git_repo_manager import GitRepoManager
from src.services.git_workspace_state import (
    GitWorkspaceState, GitWorkspaceStateError, WorkspaceState, tree_fingerprint,
)


class PointerStore:
    def __init__(self):
        self.raw = None
        self.etag = None
        self.fail = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def serialize_version(self, version):
        return f'"{version}"'

    def restore_version(self, value):
        return ObjectVersion(value[1:-1])

    @asynccontextmanager
    async def open_read(self, key):
        if self.raw is None:
            raise StorageError("not_found", provider_code="fixture")
        raw = self.raw
        async def chunks(chunk_size):
            for offset in range(0, len(raw), chunk_size):
                yield raw[offset:offset + chunk_size]
        yield SimpleNamespace(info=ObjectInfo(key, len(raw), self.restore_version(self.etag), None,
                                             ContentProperties(), {}, self.etag),
                              body=SimpleNamespace(iter_chunks=chunks))

    async def write(self, key, content, *, options):
        if self.fail == "reject":
            raise RuntimeError("conditional write rejected")
        expected = self.serialize_version(options.expected_version) if options.expected_version is not None else None
        if (self.etag is not None and expected != self.etag) or (self.etag is None and not options.create_only):
            raise StorageError("precondition_failed", provider_code="fixture", status_code=412)
        self.raw, self.etag = content, '"' + str(uuid4()) + '"'
        if self.fail == "accepted":
            raise RuntimeError("response lost after acceptance")
        return WriteResult(self.restore_version(self.etag))


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    pointer = PointerStore()
    monkeypatch.setattr(GitWorkspaceState, "_get_store", Mock(return_value=pointer))

    async def create(root):
        key = str(uuid4())
        shutil.copytree(root, snapshots / key)
        return key

    async def restore(key, target):
        shutil.copytree(snapshots / key, target, dirs_exist_ok=True)
        GitRepoManager._write_storage_baseline(target, "f" * 64)

    async def delete(key):
        shutil.rmtree(snapshots / key)

    manager = SimpleNamespace(
        _settings=SimpleNamespace(s3_bucket="fixture"),
        _mirror=SimpleNamespace(follows_symlinks=True),
        checkpoint_workspace=AsyncMock(side_effect=create),
        restore_workspace_checkpoint=AsyncMock(side_effect=restore),
        delete_workspace_checkpoint=AsyncMock(side_effect=delete),
        _write_storage_baseline=GitRepoManager._write_storage_baseline,
        _restore_git_directories=GitRepoManager._restore_git_directories,
    )
    root = tmp_path / "source"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    GitRepoManager._write_storage_baseline(root, "a" * 64)
    (root / "file.txt").write_text("first")
    return GitWorkspaceState(manager), root, snapshots, pointer


@pytest.mark.parametrize("failure", ["reject", "accepted"])
async def test_uncertain_pointer_publication_retains_both_snapshots(handoff, failure, tmp_path):
    state, root, snapshots, pointer = handoff
    await state.persist(root, None, None)
    previous, etag = await state.read()
    (root / "file.txt").write_text("second")
    pointer.fail = failure
    with pytest.raises(RuntimeError):
        await state.persist(root, previous, etag)
    assert len(list(snapshots.iterdir())) == 2
    state.manager.delete_workspace_checkpoint.assert_not_awaited()
    assert state._receipt(root)["etag"] == etag
    if failure == "accepted":
        current, current_etag = await state.read()
        successor = tmp_path / "successor"
        successor.mkdir()
        await state.restore(successor, current, current_etag)
        assert (successor / "file.txt").read_text() == "second"
        with pytest.raises(GitWorkspaceStateError, match="unsaved local work"):
            await state.restore(root, current, current_etag)


async def test_corrupt_checkpoint_never_overwrites_the_existing_checkout(handoff, tmp_path):
    state, root, snapshots, _ = handoff
    await state.persist(root, None, None)
    previous, etag = await state.read()
    successor = tmp_path / "successor"
    successor.mkdir()
    (successor / "local.txt").write_text("preserve me")
    state.record(successor, None, tree_fingerprint(successor))
    (snapshots / previous.checkpoint / "file.txt").write_text("corrupt")
    with pytest.raises(GitWorkspaceStateError, match="checkpoint changed"):
        await state.restore(successor, previous, etag)
    assert list(successor.iterdir()) == [successor / "local.txt"]
    assert (successor / "local.txt").read_text() == "preserve me"


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_restore_drains_install_before_cleaning_staging(
    handoff, tmp_path, monkeypatch, cancellations,
):
    state, root, _, _ = handoff
    await state.persist(root, None, None)
    current, etag = await state.read()
    successor = tmp_path / "successor"
    successor.mkdir()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    staging_paths = []
    install = state._install

    def blocked_install(staging, target):
        staging_paths.append(staging)
        started.set()
        try:
            release.wait()
            install(staging, target)
        finally:
            finished.set()

    monkeypatch.setattr(state, "_install", blocked_install)
    restoring = asyncio.create_task(state.restore(successor, current, etag))
    waiting = asyncio.create_task(asyncio.to_thread(started.wait))
    try:
        await asyncio.wait({restoring, waiting}, return_when=asyncio.FIRST_COMPLETED)
        if restoring.done():
            restoring.result()
            pytest.fail("Restore completed without entering the installer")
        for _ in range(cancellations):
            restoring.cancel()
            # Deliver cancellation while installation is deterministically blocked.
            await asyncio.sleep(0)
            assert not restoring.done()
            assert staging_paths[0].is_dir()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.gather(restoring)
        assert finished.is_set()
        assert (successor / "file.txt").read_text() == "first"
        assert not staging_paths[0].exists()
        assert state._receipt(successor) is None
    finally:
        release.set()
        started.set()
        await asyncio.gather(restoring, waiting, return_exceptions=True)
        if staging_paths:
            await asyncio.to_thread(finished.wait)


async def test_failed_checkpoint_does_not_change_the_pointer(handoff):
    state, root, _, pointer = handoff
    await state.persist(root, None, None)
    previous, etag = await state.read()
    raw = pointer.raw
    (root / "file.txt").write_text("second")
    state.manager.checkpoint_workspace.side_effect = RuntimeError("upload failed")
    with pytest.raises(RuntimeError, match="upload failed"):
        await state.persist(root, previous, etag)
    assert pointer.raw == raw
    assert pointer.etag == etag
    state.manager.delete_workspace_checkpoint.assert_not_awaited()


async def test_noop_does_not_create_a_checkpoint(handoff):
    state, root, snapshots, pointer = handoff
    await state.persist(root, None, None)
    previous, etag = await state.read()
    await state.persist(root, previous, etag)
    assert pointer.etag == etag
    assert len(list(snapshots.iterdir())) == 1


async def test_failed_repo_publication_can_retain_local_state_without_its_marker(handoff, tmp_path):
    state, root, _, _ = handoff
    await state.persist(root, None, None)
    previous, etag = await state.read()
    # sync_up removes this marker before uploading, and deliberately leaves it
    # absent when publication fails. The immutable pointer retains its baseline.
    (root / ".git" / "bifrost-storage-baseline").unlink()
    (root / "file.txt").write_text("not yet published")
    await state.persist(root, previous, etag)
    current, current_etag = await state.read()
    await state.restore(root, current, current_etag)
    assert (root / ".git" / "bifrost-storage-baseline").read_text() == "a" * 64
    successor = tmp_path / "successor"
    successor.mkdir()
    await state.restore(successor, current, current_etag)
    assert (successor / "file.txt").read_text() == "not yet published"
    assert (successor / ".git" / "bifrost-storage-baseline").read_text() == "a" * 64


async def test_publication_advances_the_source_baseline_even_without_git_changes(handoff):
    state, root, _, _ = handoff
    await state.persist(root, None, None)
    previous, etag = await state.read()
    GitRepoManager._write_storage_baseline(root, "b" * 64)
    await state.persist(root, previous, etag)
    current, current_etag = await state.read()
    assert current.baseline == "b" * 64
    assert current_etag != etag


async def test_s3_checkpoint_keeps_the_existing_follow_link_byte_semantics(handoff, tmp_path):
    state, root, _, _ = handoff
    external = tmp_path / "external"
    external.mkdir()
    (external / "source.txt").write_text("followed by the AWS CLI")
    (root / "linked-directory").symlink_to(external, target_is_directory=True)
    await state.persist(root, None, None)
    current, etag = await state.read()
    target = tmp_path / "target"
    target.mkdir()
    old_external = tmp_path / "old-external"
    old_external.mkdir()
    (old_external / "source.txt").write_text("do not overwrite outside the checkout")
    (target / "linked-directory").symlink_to(old_external, target_is_directory=True)
    state.record(target, None, await state.fingerprint(target))
    await state.restore(target, current, etag)
    assert (target / "linked-directory" / "source.txt").read_text() == "followed by the AWS CLI"
    assert not (target / "linked-directory").is_symlink()
    assert (external / "source.txt").read_text() == "followed by the AWS CLI"
    assert (old_external / "source.txt").read_text() == "do not overwrite outside the checkout"


def test_symlink_cycle_fails_without_unbounded_traversal(tmp_path):
    (tmp_path / "cycle").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(GitWorkspaceStateError, match="cycle"):
        tree_fingerprint(tmp_path, follow_links=True)


async def test_checkpoint_readback_precedes_pointer_publication(handoff):
    state, root, snapshots, pointer = handoff
    create = state.manager.checkpoint_workspace.side_effect

    async def mixed_snapshot(source):
        checkpoint = await create(source)
        (snapshots / checkpoint / "file.txt").write_text("transient content")
        return checkpoint

    state.manager.checkpoint_workspace.side_effect = mixed_snapshot
    with pytest.raises(GitWorkspaceStateError, match="readback"):
        await state.persist(root, None, None)
    assert pointer.raw is None
    state.manager.delete_workspace_checkpoint.assert_not_awaited()


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("checkpoint", "../../escape"),
    ("checkpoint", None), ("baseline", ""), ("fingerprint", "X" * 64),
])
def test_invalid_state_is_refused(field, value):
    data = {"checkpoint": str(uuid4()), "baseline": "a" * 64, "fingerprint": "b" * 64, "version": 1}
    data[field] = value
    with pytest.raises(GitWorkspaceStateError):
        WorkspaceState.parse(json.dumps(data).encode())


async def test_existing_quoted_receipt_does_not_rehydrate_over_unpublished_edits(handoff):
    state, root, _, pointer = handoff
    await state.persist(root, None, None)
    pointer.etag = '"legacy-opaque-v1"'
    current, witness = await state.read()
    assert witness == '"legacy-opaque-v1"'
    state.record(root, '"legacy-opaque-v1"', await state.fingerprint(root))
    state.manager.restore_workspace_checkpoint.reset_mock()
    (root / "file.txt").write_text("unpublished local edits")
    await state.restore(root, current, witness)
    assert (root / "file.txt").read_text() == "unpublished local edits"
    assert state._receipt(root)["etag"] == '"legacy-opaque-v1"'
    state.manager.restore_workspace_checkpoint.assert_not_awaited()


async def test_quoted_stale_pointer_cannot_replace_concurrent_state_or_delete_checkpoint(handoff):
    state, root, _, pointer = handoff
    await state.persist(root, None, None)
    previous, stale = await state.read()
    assert stale.startswith('"') and stale.endswith('"')
    pointer.etag = '"concurrent-writer"'
    saved = pointer.raw
    (root / "file.txt").write_text("stale writer changes")
    with pytest.raises(StorageError) as caught:
        await state.persist(root, previous, stale)
    assert caught.value.status_code == 412
    assert pointer.raw == saved and pointer.etag == '"concurrent-writer"'
    state.manager.delete_workspace_checkpoint.assert_not_awaited()
