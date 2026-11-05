import asyncio

import pytest

from src.services.blob_workspace_mirror import _files, _run_transfers, _safe_path


def test_workspace_fingerprint_agrees_for_transport_and_repo_metadata_tokens():
    from src.services.git_repo_manager import workspace_version_fingerprint

    assert workspace_version_fingerprint(
        {"file": '"OpaqueVersion"'}
    ) == workspace_version_fingerprint({"file": "OpaqueVersion"})
    assert workspace_version_fingerprint(
        {"file": "OpaqueVersion"}
    ) != workspace_version_fingerprint({"file": "opaqueversion"})


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a/../b", "a\\b", "a//b", "a/./b", "C:/file", ""]
)
def test_remote_keys_cannot_escape_or_alias_workspace(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe"):
        _safe_path(tmp_path, name)


def test_remote_keys_cannot_write_through_local_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        _safe_path(root, "link/file")
    with pytest.raises(ValueError, match="symbolic"):
        _files(root)


def test_local_tree_includes_git_objects_and_dotfiles(tmp_path):
    for name in (".git/objects/ab/cd", ".env.example", "file"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"content")
    assert set(_files(tmp_path)) == {".git/objects/ab/cd", ".env.example", "file"}


@pytest.mark.asyncio
async def test_failed_transfer_drains_cancelled_siblings_before_return():
    started, drained = asyncio.Event(), asyncio.Event()

    async def sibling():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()

    async def failure():
        await started.wait()
        raise ConnectionError("transfer failed")

    with pytest.raises(ConnectionError, match="transfer failed"):
        await _run_transfers((sibling(), failure()))
    assert drained.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_snapshot_drains_copy_before_closing_file(
    tmp_path, monkeypatch, cancellations,
):
    from threading import Event
    from src.config import Settings
    from src.services.blob_workspace_mirror import BlobWorkspaceMirror

    path = tmp_path / "source"
    path.write_bytes(b"snapshot")
    mirror = BlobWorkspaceMirror(
        Settings(
            _env_file=None,
            object_storage_provider="azure_blob",
            azure_blob_container="fixture",
            s3_bucket=None,
            s3_endpoint_url=None,
            s3_access_key=None,
            s3_secret_key=None,
        )
    )
    started, release, finished = asyncio.Event(), Event(), Event()
    loop = asyncio.get_running_loop()
    streams = []
    original = mirror._copy_snapshot

    def copying(source, destination):
        streams.append(destination)
        loop.call_soon_threadsafe(started.set)
        try:
            release.wait()
            assert not destination.closed
            return original(source, destination)
        finally:
            finished.set()

    monkeypatch.setattr(mirror, "_copy_snapshot", copying)

    async def owner():
        async with mirror._upload_snapshot(path):
            pytest.fail("cancelled snapshot must not yield an upload body")

    task = asyncio.create_task(owner())
    waiting = asyncio.create_task(started.wait())
    try:
        await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            task.result()
            pytest.fail("Snapshot owner completed without starting its copy")
        for _ in range(cancellations):
            task.cancel()
            # Flush cancellation through the task/gather callback chain while
            # the thread remains blocked; these are scheduling, not timed waits.
            for _ in range(3):
                delivered = asyncio.Event()
                loop.call_soon(delivered.set)
                await delivered.wait()
            assert not task.done()
            assert not streams[0].closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.gather(task)
        assert streams[0].closed
        assert finished.is_set()
    finally:
        release.set()
        waiting.cancel()
        await asyncio.gather(task, waiting, return_exceptions=True)
        if streams:
            await asyncio.to_thread(finished.wait)
