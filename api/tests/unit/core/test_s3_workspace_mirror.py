"""Native S3 workspace transfers stay behind the object-store contract."""
import hashlib
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.config import Settings
from src.core.storage.contracts import (
    ContentProperties,
    ObjectInfo,
    ObjectPage,
    ObjectRead,
    ObjectSummary,
    ObjectVersion,
    StorageConfigurationError,
    StorageError,
    WriteOptions,
    WriteResult,
)
from src.core.storage.s3_mirror import S3WorkspaceMirror, S3WorkspaceChanged


@pytest.fixture
def mock_settings():
    return Settings(_env_file=None, s3_bucket="bifrost-local", s3_endpoint_url="http://seaweedfs:8333",
                    s3_access_key="bifrost", s3_secret_key="bifrost123", s3_region="us-east-1", redis_url="")


@pytest.fixture
def mirror(mock_settings):
    return S3WorkspaceMirror(mock_settings)


def _at(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc)


class FakeBody:
    def __init__(self, content):
        self._content = content
        self.closed = False

    async def read(self, amt=None):
        return self._content if amt is None else self._content[:amt]

    async def iter_chunks(self, chunk_size=8 * 1024 * 1024):
        yield self._content

    async def close(self):
        self.closed = True


class FakeStore:
    """In-memory S3ObjectStore double tracking conditional writes."""

    def __init__(self, objects=None):
        # key -> (bytes, version, modified_at)
        self.objects = dict(objects or {})
        self.writes = []
        self.streams = []
        self.deletes = []
        self.deleted_many = []
        self.list_hook: Callable[[], Awaitable[None]] | None = None
        self.write_hook: Callable[[], Awaitable[None]] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def _page(self, prefix):
        return ObjectPage(
            tuple(
                ObjectSummary(key, len(content), version, modified)
                for key, (content, version, modified) in sorted(self.objects.items())
                if key.startswith(prefix)
            ),
            (),
            None,
        )

    async def list_page(self, prefix="", **kwargs):
        page = self._page(prefix)
        if self.list_hook is not None:
            await self.list_hook()
        return page

    @asynccontextmanager
    async def open_read(self, key, **kwargs):
        content, version, modified = self.objects[key]
        expected = kwargs.get("expected_version")
        if expected is not None and expected != version:
            raise StorageError("precondition_failed", provider_code="IfMatch")
        info = ObjectInfo(key, len(content), version, modified, ContentProperties(), {}, f'"{version}"')
        yield ObjectRead(info, FakeBody(content))

    async def write(self, key, content, *, options=None):
        options = options or WriteOptions()
        self.writes.append((key, options))
        current = self.objects.get(key)
        if options.create_only and current is not None:
            raise StorageError("precondition_failed", provider_code="IfNoneMatch")
        if options.expected_version is not None and (
            current is None or current[1] != options.expected_version
        ):
            raise StorageError("precondition_failed", provider_code="IfMatch")
        version = ObjectVersion(hashlib.md5(content, usedforsecurity=False).hexdigest())
        self.objects[key] = (content, version, _at(200))
        if self.write_hook is not None:
            await self.write_hook()
        return WriteResult(version)

    async def write_stream(self, key, content, *, options=None):
        chunks = [chunk async for chunk in content]
        self.streams.append((key, options))
        return await self.write(key, b"".join(chunks), options=options)

    async def delete(self, key, *, expected_version=None):
        self.deletes.append((key, expected_version))
        current = self.objects.get(key)
        if expected_version is not None and (current is None or current[1] != expected_version):
            raise StorageError("precondition_failed", provider_code="IfMatch")
        self.objects.pop(key, None)

    async def delete_many(self, keys):
        self.deleted_many.append(list(keys))
        for key in keys:
            self.objects.pop(key, None)


def _store(store):
    return patch("src.core.storage.s3_mirror.S3ObjectStore.from_settings", return_value=store)


def _seed(content, timestamp=100, version=None):
    return (content, ObjectVersion(
        version if version is not None else hashlib.md5(content, usedforsecurity=False).hexdigest(),
    ), _at(timestamp))


class TestPrefixValidation:
    @pytest.mark.parametrize("prefix", ["", "/", "../", "_repo/../", "_repo//", "_repo\\outside/"])
    def test_native_prefix_cannot_escape_workspace_namespace(self, mirror, prefix):
        with pytest.raises(ValueError):
            mirror._prefix(prefix)

    def test_missing_bucket_is_a_configuration_error(self, mock_settings):
        mock_settings.s3_bucket = ""
        with pytest.raises(StorageConfigurationError):
            S3WorkspaceMirror(mock_settings)


async def test_download_refuses_invalid_prefix_before_touching_target(mirror, tmp_path):
    target = tmp_path / "not-created"
    with pytest.raises(ValueError):
        await mirror.download("../", target)
    assert not target.exists()


async def test_rejected_snapshot_does_not_start_native_upload(mirror, tmp_path):
    store = FakeStore({"_repo/file": _seed(b"x")})
    def validate(snapshot):
        assert snapshot == {"file": store.objects["_repo/file"][1]}
        raise RuntimeError("stale baseline")
    with _store(store):
        with pytest.raises(RuntimeError, match="stale baseline"):
            await mirror.upload(tmp_path, "_repo/", validate_snapshot=validate)
    assert store.writes == [] and store.streams == [] and store.deletes == []


class TestDownload:
    async def test_new_files_land_with_remote_timestamps(self, mirror, tmp_path):
        store = FakeStore({"_repo/note.txt": _seed(b"hello", timestamp=150)})
        with _store(store):
            await mirror.download("_repo/", tmp_path)
        target = tmp_path / "note.txt"
        assert target.read_bytes() == b"hello"
        assert int(target.stat().st_mtime) == 150

    async def test_unchanged_files_are_not_retransferred(self, mirror, tmp_path):
        local = tmp_path / "note.txt"
        local.write_bytes(b"hello")
        os.utime(local, (100, 100))
        store = FakeStore({"_repo/note.txt": _seed(b"hello", timestamp=100)})
        with _store(store):
            with patch.object(store, "open_read", wraps=store.open_read) as reads:
                await mirror.download("_repo/", tmp_path)
        reads.assert_not_called()
        assert local.read_bytes() == b"hello"

    async def test_same_size_edit_is_detected_by_content_hash(self, mirror, tmp_path):
        local = tmp_path / "note.txt"
        local.write_bytes(b"v2!")
        os.utime(local, (100, 100))
        store = FakeStore({"_repo/note.txt": _seed(b"v1!", timestamp=100)})
        with _store(store):
            await mirror.download("_repo/", tmp_path)
        assert local.read_bytes() == b"v1!"

    async def test_multipart_etag_always_retransfers(self, mirror, tmp_path):
        local = tmp_path / "big.bin"
        local.write_bytes(b"v1")
        os.utime(local, (100, 100))
        store = FakeStore({"_repo/big.bin": _seed(b"v1", timestamp=100, version="opaque-multipart-2")})
        with _store(store):
            with patch.object(store, "open_read", wraps=store.open_read) as reads:
                await mirror.download("_repo/", tmp_path)
        reads.assert_called_once()
        assert local.read_bytes() == b"v1"

    async def test_stale_local_files_and_empty_dirs_are_removed(self, mirror, tmp_path):
        stale = tmp_path / "old" / "gone.txt"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
        keep = tmp_path / "note.txt"
        keep.write_bytes(b"hello")
        os.utime(keep, (100, 100))
        store = FakeStore({"_repo/note.txt": _seed(b"hello", timestamp=100)})
        with _store(store):
            await mirror.download("_repo/", tmp_path)
        assert not stale.exists()
        assert not (tmp_path / "old").exists()
        assert keep.read_bytes() == b"hello"

    async def test_conflicting_remote_file_and_directory_refused(self, mirror, tmp_path):
        store = FakeStore({
            "_repo/a": _seed(b"file"),
            "_repo/a/b": _seed(b"nested"),
        })
        with _store(store):
            with pytest.raises(ValueError, match="Conflicting"):
                await mirror.download("_repo/", tmp_path)
        assert list(tmp_path.iterdir()) == []

    async def test_remote_change_during_download_raises(self, mirror, tmp_path):
        store = FakeStore({"_repo/note.txt": _seed(b"hello", timestamp=150)})

        async def intrude():
            store.objects["_repo/other.txt"] = _seed(b"late")

        store.list_hook = intrude
        with _store(store):
            with pytest.raises(S3WorkspaceChanged):
                await mirror.download("_repo/", tmp_path)

    async def test_missing_remote_timestamp_is_a_backend_error(self, mirror, tmp_path):
        store = FakeStore({"_repo/note.txt": (b"hello", ObjectVersion("abc"), None)})
        with _store(store):
            with pytest.raises(StorageError) as caught:
                await mirror.download("_repo/", tmp_path)
        assert caught.value.provider_code == "MissingWorkspaceTimestamp"

    async def test_symlinked_directories_restore_as_real_directories(self, mirror, tmp_path):
        external = tmp_path / "external"
        external.mkdir()
        (external / "source.txt").write_bytes(b"through the link")
        store = FakeStore({"_repo/link/source.txt": _seed(b"through the link", timestamp=150)})
        target = tmp_path / "target"
        target.mkdir()
        (target / "link").symlink_to(external, target_is_directory=True)
        with _store(store):
            await mirror.download("_repo/", target)
        assert (target / "link" / "source.txt").read_bytes() == b"through the link"


class TestUpload:
    async def test_new_files_upload_create_only(self, mirror, tmp_path):
        (tmp_path / "note.txt").write_bytes(b"hello")
        store = FakeStore()
        with _store(store):
            await mirror.upload(tmp_path, "_repo/")
        assert store.writes == [("_repo/note.txt", WriteOptions(create_only=True))]
        assert store.objects["_repo/note.txt"][0] == b"hello"

    async def test_unchanged_files_are_not_reuploaded(self, mirror, tmp_path):
        (tmp_path / "note.txt").write_bytes(b"hello")
        store = FakeStore({"_repo/note.txt": _seed(b"hello", timestamp=100)})
        with _store(store):
            with patch.object(store, "write", wraps=store.write) as writes:
                await mirror.upload(tmp_path, "_repo/")
        writes.assert_not_called()
        assert store.deletes == []

    async def test_changed_files_upload_with_expected_version(self, mirror, tmp_path):
        (tmp_path / "note.txt").write_bytes(b"new!!")
        remote_version = ObjectVersion(hashlib.md5(b"old!!", usedforsecurity=False).hexdigest())
        store = FakeStore({"_repo/note.txt": (b"old!!", remote_version, _at(100))})
        with _store(store):
            await mirror.upload(tmp_path, "_repo/")
        assert store.writes == [("_repo/note.txt", WriteOptions(expected_version=remote_version))]
        assert store.objects["_repo/note.txt"][0] == b"new!!"

    async def test_multipart_remote_uses_mtime_for_skip_decision(self, mirror, tmp_path):
        current = tmp_path / "big.bin"
        current.write_bytes(b"v1")
        os.utime(current, (50, 50))
        store = FakeStore({"_repo/big.bin": (b"v1", ObjectVersion("opaque-multipart-2"), _at(100))})
        with _store(store):
            with patch.object(store, "write", wraps=store.write) as writes:
                await mirror.upload(tmp_path, "_repo/")
        writes.assert_not_called()

    async def test_removed_files_are_deleted_with_expected_version(self, mirror, tmp_path):
        remote_version = ObjectVersion("deadbeef")
        store = FakeStore({"_repo/gone.txt": (b"gone", remote_version, _at(100))})
        with _store(store):
            await mirror.upload(tmp_path, "_repo/")
        assert store.deletes == [("_repo/gone.txt", remote_version)]

    async def test_remote_change_during_upload_raises(self, mirror, tmp_path):
        (tmp_path / "note.txt").write_bytes(b"hello")
        store = FakeStore()

        async def intrude():
            store.objects["_repo/intruder.txt"] = _seed(b"race")

        store.write_hook = intrude
        with _store(store):
            with pytest.raises(S3WorkspaceChanged):
                await mirror.upload(tmp_path, "_repo/")

    async def test_missing_source_directory_is_refused(self, mirror, tmp_path):
        store = FakeStore()
        with _store(store):
            with pytest.raises(ValueError):
                await mirror.upload(tmp_path / "absent", "_repo/")

    async def test_large_files_stream_without_buffering_whole_object(self, mirror, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.storage.s3_mirror._SINGLE_PUT_LIMIT", 10)
        (tmp_path / "big.bin").write_bytes(b"x" * 64)
        store = FakeStore()
        with _store(store):
            await mirror.upload(tmp_path, "_repo/")
        assert len(store.streams) == 1
        assert store.objects["_repo/big.bin"][0] == b"x" * 64

    async def test_symlinked_directories_publish_through_the_link(self, mirror, tmp_path):
        external = tmp_path / "external"
        external.mkdir()
        (external / "source.txt").write_bytes(b"followed content")
        (tmp_path / "linked").symlink_to(external, target_is_directory=True)
        store = FakeStore()
        with _store(store):
            await mirror.upload(tmp_path, "_repo/")
        assert store.objects["_repo/linked/source.txt"][0] == b"followed content"

    async def test_directory_cycles_terminate_without_duplication(self, mirror, tmp_path):
        (tmp_path / "real.txt").write_bytes(b"real")
        (tmp_path / "cycle").symlink_to(tmp_path, target_is_directory=True)
        store = FakeStore()
        with _store(store):
            await mirror.upload(tmp_path, "_repo/")
        assert store.objects["_repo/real.txt"][0] == b"real"
        assert "_repo/cycle/real.txt" not in store.objects


class TestDelete:
    async def test_delete_removes_the_prefix_and_verifies(self, mirror):
        store = FakeStore({
            "_repo/a.txt": _seed(b"a"),
            "_repo/sub/b.txt": _seed(b"b"),
            "other/c.txt": _seed(b"c"),
        })
        with _store(store):
            await mirror.delete("_repo/")
        assert store.deleted_many == [["_repo/a.txt", "_repo/sub/b.txt"]]
        assert set(store.objects) == {"other/c.txt"}

    async def test_delete_on_empty_prefix_succeeds(self, mirror):
        store = FakeStore()
        with _store(store):
            await mirror.delete("_repo/")
        assert store.deleted_many == [[]]

    async def test_delete_refuses_invalid_prefix(self, mirror):
        store = FakeStore()
        with _store(store):
            with pytest.raises(ValueError):
                await mirror.delete("../")
        assert store.deleted_many == []
