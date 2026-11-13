"""Observable workspace sync contracts, independent of the retired AWS CLI."""
import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.config import Settings
from src.core.storage.contracts import ObjectPage, ObjectSummary, ObjectVersion
from src.core.storage.s3_mirror import S3WorkspaceMirror, _transfers


class Store:
    def __init__(self, content=None):
        self.content = content or {}
        self.reads = []
        self.writes = []
        self.deleted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def list_page(self, prefix, **kwargs):
        items = tuple(ObjectSummary(key, len(value), ObjectVersion(hashlib.md5(value).hexdigest()),
                                    datetime.fromtimestamp(100, timezone.utc))
                      for key, value in self.content.items() if key.startswith(prefix))
        return ObjectPage(items, (), None)

    @asynccontextmanager
    async def open_read(self, key, **kwargs):
        self.reads.append(key)
        async def chunks():
            yield self.content[key]
        yield SimpleNamespace(body=SimpleNamespace(iter_chunks=chunks))

    async def write_stream(self, key, chunks, **kwargs):
        self.writes.append(key)
        self.content[key] = b''.join([chunk async for chunk in chunks])

    async def delete(self, key):
        self.deleted.append(key)
        self.content.pop(key)


@pytest.fixture
def mirror():
    return S3WorkspaceMirror(Settings(_env_file=None, s3_bucket='test', redis_url=''))


@pytest.fixture
def store():
    value = Store()
    with patch('src.core.storage.s3_mirror.S3ObjectStore.from_settings', return_value=value):
        yield value


async def test_round_trip_deletion_incremental_and_equal_stat_changes(mirror, store, tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / '.git').mkdir()
    (source / '.git/HEAD').write_bytes(b'ref: refs/heads/main')
    (source / 'café + %:back\\slash.txt').write_bytes(b'v1')
    await mirror.upload(source, '_repo/')
    await mirror.download('_repo/', target)
    assert (target / '.git/HEAD').read_bytes() == b'ref: refs/heads/main'
    store.reads.clear()
    store.writes.clear()
    await mirror.upload(source, '_repo/')
    await mirror.download('_repo/', target)
    assert store.reads == store.writes == []
    (source / 'café + %:back\\slash.txt').write_bytes(b'v2')
    (source / '.git/HEAD').unlink()
    await mirror.upload(source, '_repo/')
    await mirror.download('_repo/', target)
    assert (target / 'café + %:back\\slash.txt').read_bytes() == b'v2'
    assert not (target / '.git/HEAD').exists()
    assert store.deleted == ['_repo/.git/HEAD']
    await mirror.delete('_repo/')
    assert store.content == {}


async def test_upload_follows_file_and_directory_symlinks(mirror, store, tmp_path):
    source, outside = tmp_path / 'source', tmp_path / 'outside'
    source.mkdir()
    outside.mkdir()
    (outside / 'file').write_bytes(b'linked')
    (source / 'file').symlink_to(outside / 'file')
    (source / 'directory').symlink_to(outside, target_is_directory=True)
    await mirror.upload(source, '_repo/')
    assert store.content == {'_repo/file': b'linked', '_repo/directory/file': b'linked'}


@pytest.mark.parametrize('prefix', ['', '/', '../', '_repo/../', '_repo//', '_repo\\outside/'])
async def test_invalid_prefix_does_not_touch_target(mirror, tmp_path, prefix):
    target = tmp_path / 'absent'
    with pytest.raises(ValueError):
        await mirror.download(prefix, target)
    assert not target.exists()


async def test_snapshot_rejection_precedes_all_writes_and_deletes(mirror, store, tmp_path):
    store.content['_repo/file'] = b'newer'
    def validate(snapshot):
        assert snapshot == {'file': hashlib.md5(b'newer').hexdigest()}
        raise RuntimeError('stale baseline')
    with pytest.raises(RuntimeError, match='stale baseline'):
        await mirror.upload(tmp_path, '_repo/', validate_snapshot=validate)
    assert store.writes == store.deleted == []


@pytest.mark.parametrize('name', ['../escape', '/escape', 'a/../escape', 'a//b'])
async def test_unsafe_remote_names_leave_local_files_intact(mirror, store, tmp_path, name):
    (tmp_path / 'keep').write_bytes(b'keep')
    store.content['_repo/' + name] = b'bad'
    with pytest.raises(ValueError):
        await mirror.download('_repo/', tmp_path)
    assert (tmp_path / 'keep').read_bytes() == b'keep'


async def test_failed_download_does_not_delete_or_replace_local_files(mirror, store, tmp_path):
    (tmp_path / 'keep').write_bytes(b'keep')
    store.content['_repo/new'] = b'new'
    @asynccontextmanager
    async def failing(*args, **kwargs):
        raise RuntimeError('network failed')
        yield
    store.open_read = failing
    with pytest.raises(RuntimeError, match='network failed'):
        await mirror.download('_repo/', tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {'keep': b'keep'}


@pytest.mark.parametrize('cancellations', [1, 2])
async def test_cancellation_drains_transfers_before_releasing_owner(cancellations):
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def transfer():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()
            finished.set()
    owner = asyncio.create_task(_transfers([transfer()]))
    await started.wait()
    try:
        for _ in range(cancellations):
            owner.cancel()
            await asyncio.sleep(0)
            assert not owner.done()
        release.set()
        await asyncio.wait({owner})
        with pytest.raises(asyncio.CancelledError):
            owner.result()
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)


@pytest.mark.parametrize('multipart', [False, True])
def test_incremental_identity_checks_content_including_multipart(tmp_path, multipart):
    from src.core.storage.s3_mirror import _matches
    from src.core.storage.s3_transfers import STREAM_PART_SIZE
    content = b'a' * (STREAM_PART_SIZE + 1)
    path = tmp_path / 'large'
    path.write_bytes(content)
    parts = [hashlib.md5(content[start:start + STREAM_PART_SIZE]).digest()
             for start in range(0, len(content), STREAM_PART_SIZE)]
    version = (hashlib.md5(b''.join(parts)).hexdigest() + '-2' if multipart
               else hashlib.md5(content).hexdigest())
    item = ObjectSummary('file', len(content), ObjectVersion(version), None)
    assert _matches(path, item)
    path.write_bytes(b'b' + content[1:])
    assert not _matches(path, item)


async def test_download_follows_existing_file_symlink(mirror, store, tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'old')
    (target / 'link').symlink_to(outside)
    store.content['_repo/link'] = b'new'
    await mirror.download('_repo/', target)
    assert (target / 'link').is_symlink()
    assert outside.read_bytes() == b'new'


async def test_s3_directory_markers_are_ignored_by_sync_but_removed_by_delete(mirror, store, tmp_path):
    store.content = {'_repo/': b'', '_repo/folder/': b'', '_repo/folder/file': b'file'}
    await mirror.download('_repo/', tmp_path)
    assert (tmp_path / 'folder/file').read_bytes() == b'file'
    await mirror.upload(tmp_path, '_repo/')
    assert store.deleted == []
    await mirror.delete('_repo/')
    assert store.content == {}


@pytest.mark.parametrize('cycle', [False, True])
async def test_invalid_local_link_refuses_sync_before_remote_deletion(mirror, store, tmp_path, cycle):
    store.content['_repo/keep'] = b'keep'
    (tmp_path / 'link').symlink_to(tmp_path if cycle else tmp_path / 'missing')
    with pytest.raises(ValueError, match='symbolic link'):
        await mirror.upload(tmp_path, '_repo/')
    assert store.deleted == store.writes == []


@pytest.mark.parametrize('cancellations', [1, 2])
async def test_cancelled_local_io_keeps_owner_until_thread_finishes(cancellations):
    import threading
    from src.core.storage.s3_mirror import _local
    started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    def read():
        loop.call_soon_threadsafe(started.set)
        release.wait()
        finished.set()
    owner = asyncio.create_task(_local(read))
    await started.wait()
    try:
        for _ in range(cancellations):
            owner.cancel()
            await asyncio.sleep(0)
            assert not owner.done()
        release.set()
        await asyncio.wait({owner})
        with pytest.raises(asyncio.CancelledError):
            owner.result()
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
