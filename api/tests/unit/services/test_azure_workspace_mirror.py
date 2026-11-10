import asyncio
from types import SimpleNamespace

import pytest

from src.config import Settings
from src.core.storage.azure_mirror import (
    AzureWorkspaceMirror, _files, _run_transfers, _safe_path,
)
from src.core.storage.contracts import ObjectSummary, ObjectVersion


class WorkspaceError(RuntimeError):
    pass


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "object_storage_provider": "azure_blob",
            "azure_blob_account_url": "https://example.blob.core.windows.net",
            "azure_blob_container": "contract",
            "azure_blob_auth": "managed_identity",
            "azure_blob_account_key": None,
            **overrides,
        },
    )


def test_workspace_fingerprint_is_deterministic_and_provider_opaque():
    from src.services.git_repo_manager import workspace_version_fingerprint

    versions = {"b.txt": '"v2"', "a.txt": '"v1"'}
    assert workspace_version_fingerprint(versions) == workspace_version_fingerprint(
        {"a.txt": '"v1"', "b.txt": '"v2"'}
    )
    assert workspace_version_fingerprint(versions) != workspace_version_fingerprint(
        {"a.txt": '"v1"', "b.txt": '"v3"'}
    )
    # Azure's quoted entity-tags and S3's raw tokens hash verbatim; the
    # provider canonicalizes, the fingerprint never interprets.
    assert workspace_version_fingerprint({"file": '"v"'}) != workspace_version_fingerprint(
        {"file": "v"}
    )


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


def test_mirror_rejects_bad_prefixes():
    mirror = AzureWorkspaceMirror(settings())
    for prefix in ["", "no-slash", "/leading/", "a/../b", "a\\b", "_repo"]:
        with pytest.raises(ValueError):
            mirror._prefix(prefix)
    assert mirror._prefix("_repo/") == "_repo/"


def test_mirror_requires_a_configured_container():
    from src.core.storage.contracts import StorageConfigurationError

    with pytest.raises(StorageConfigurationError):
        AzureWorkspaceMirror(settings(azure_blob_container=None))


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

    path = tmp_path / "source"
    path.write_bytes(b"snapshot")
    mirror = AzureWorkspaceMirror(settings())
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


class FakeStore:
    """In-memory ObjectStore stand-in behind the mirror's factory."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, ObjectVersion]] = {}
        self.reads = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def _page(self, prefix, cursor):
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        return SimpleNamespace(
            objects=tuple(
                ObjectSummary(key=key, size=len(self.objects[key][0]),
                              version=self.objects[key][1], modified_at=None)
                for key in keys
            ),
            prefixes=(), next_cursor=None,
        )

    async def list_page(self, prefix="", **kwargs):
        return await self._page(prefix, kwargs.get("cursor"))

    def open_read(self, key, **kwargs):
        from contextlib import asynccontextmanager

        store = self

        @asynccontextmanager
        async def read():
            content, version = store.objects[key]
            expected = kwargs.get("expected_version")
            if expected is not None and expected != version:
                from src.core.storage.contracts import StorageError

                raise StorageError("precondition_failed", provider_code="ConditionNotMet")
            store.reads += 1
            body = SimpleNamespace(
                iter_chunks=lambda chunk_size=8 * 1024 * 1024: _chunks(content),
                close=lambda: asyncio.sleep(0),
            )

            async def aclose():
                pass

            body.close = aclose
            yield SimpleNamespace(
                info=SimpleNamespace(key=key, version=version), body=body,
            )

        return read()

    async def write(self, key, content, *, options=None):
        from src.core.storage.contracts import StorageError, WriteOptions

        options = options or WriteOptions()
        current = self.objects.get(key)
        if options.create_only and current is not None:
            raise StorageError("precondition_failed", provider_code="BlobAlreadyExists")
        if options.expected_version is not None and (
            current is None or current[1] != options.expected_version
        ):
            raise StorageError("precondition_failed", provider_code="ConditionNotMet")
        version = ObjectVersion(f'"{len(self.objects)}-{key}"')
        self.objects[key] = (bytes(content), version)
        return SimpleNamespace(version=version)

    async def delete(self, key, **kwargs):
        from src.core.storage.contracts import StorageError

        current = self.objects.get(key)
        expected = kwargs.get("expected_version")
        if current is None:
            if expected is not None:
                raise StorageError("not_found", provider_code="BlobNotFound")
            return
        if expected is not None and current[1] != expected:
            raise StorageError("precondition_failed", provider_code="ConditionNotMet")
        del self.objects[key]


async def _chunks(content):
    yield content


@pytest.fixture
def backed(monkeypatch):
    backing = FakeStore()

    class Factory:
        def __init__(self, settings=None):
            pass

        def from_settings(self, settings):
            return backing

    monkeypatch.setattr("src.core.storage.azure_mirror.AzureObjectStore", Factory())
    return backing


@pytest.mark.asyncio
async def test_upload_creates_updates_and_prunes(tmp_path, backed):
    mirror = AzureWorkspaceMirror(settings())
    source = tmp_path / "source"
    (source / "sub").mkdir(parents=True)
    (source / "keep").write_bytes(b"keep")
    (source / "sub" / "new").write_bytes(b"new")
    backed.objects["_repo/stale"] = (b"stale", ObjectVersion('"old"'))
    await mirror.upload(source, "_repo/")
    assert set(backed.objects) == {"_repo/keep", "_repo/sub/new"}
    # A second upload with no local or remote changes skips every transfer.
    first = dict(backed.objects)
    await mirror.upload(source, "_repo/")
    assert backed.objects == first


@pytest.mark.asyncio
async def test_upload_honors_the_snapshot_guard(tmp_path, backed):
    mirror = AzureWorkspaceMirror(settings())
    (tmp_path / "file").write_bytes(b"data")

    def guard(snapshot):
        raise WorkspaceError("stale")

    with pytest.raises(WorkspaceError):
        await mirror.upload(tmp_path, "_repo/", validate_snapshot=guard)


@pytest.mark.asyncio
async def test_download_replaces_stale_local_files(tmp_path, backed):
    mirror = AzureWorkspaceMirror(settings())
    target = tmp_path / "target"
    target.mkdir()
    (target / "stale").write_bytes(b"stale")
    backed.objects["_repo/fresh"] = (b"fresh", ObjectVersion('"v1"'))
    await mirror.download("_repo/", target)
    assert (target / "fresh").read_bytes() == b"fresh"
    assert not (target / "stale").exists()


@pytest.mark.asyncio
async def test_delete_clears_the_prefix(tmp_path, backed):
    mirror = AzureWorkspaceMirror(settings())
    backed.objects["_repo/a"] = (b"a", ObjectVersion('"1"'))
    backed.objects["_repo/b"] = (b"b", ObjectVersion('"2"'))
    await mirror.delete("_repo/")
    assert backed.objects == {}
