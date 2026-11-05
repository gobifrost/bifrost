"""Direct Git storage contracts: no builds, jobs, or worker executions."""

import pytest

from src.services.blob_workspace_mirror import BlobWorkspaceMirror
from src.services.git_repo_manager import GitRepoManager


def scoped_git_manager(settings, prefix, monkeypatch):
    """Route only this test process's workspace prefix; use real Blob operations."""
    import src.services.repo_storage as repo_module

    def scoped(remote_prefix):
        if remote_prefix == "_repo/":
            return prefix
        assert remote_prefix.startswith("_workspace_sync_checkpoints/")
        return remote_prefix

    class ScopedMirror(BlobWorkspaceMirror):
        async def upload(self, source, remote_prefix, **kwargs):
            await super().upload(source, scoped(remote_prefix), **kwargs)

        async def download(self, remote_prefix, target):
            await super().download(scoped(remote_prefix), target)

    monkeypatch.setattr("src.services.blob_workspace_mirror.BlobWorkspaceMirror", ScopedMirror)
    monkeypatch.setattr(repo_module, "REPO_PREFIX", prefix)
    manager = GitRepoManager(settings)

    async def forbidden_aws(_command):
        raise AssertionError("Blob Git operation attempted AWS CLI")

    monkeypatch.setattr(manager, "_run_aws_cli", forbidden_aws)
    return manager


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_download_activates_inside_a_mounted_workspace(backend, tmp_path, monkeypatch):
    """Model a volume root: rename from its parent is a cross-device operation."""
    import errno
    from pathlib import Path

    settings, client, prefix = backend
    target = tmp_path / "mounted-workspace"
    target.mkdir()
    (target / "stale").write_bytes(b"old")
    await client.put_object(Bucket=settings.storage_bucket, Key=prefix + "nested/file", Body=b"downloaded")
    replace = Path.replace

    def volume_replace(source, destination):
        destination = Path(destination)
        if destination.is_relative_to(target) and not source.is_relative_to(target):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", volume_replace)
    await BlobWorkspaceMirror(settings).download(prefix, target)
    assert (target / "nested/file").read_bytes() == b"downloaded"
    assert sorted(path.relative_to(target).as_posix() for path in target.rglob("*")) == ["nested", "nested/file"]


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_download_refuses_a_remote_staging_name_collision(backend, tmp_path, monkeypatch):
    from contextlib import contextmanager

    settings, client, prefix = backend
    target = tmp_path / "workspace"
    target.mkdir()
    (target / "unchanged").write_bytes(b"retain")
    await client.put_object(Bucket=settings.storage_bucket, Key=prefix + "collision", Body=b"remote")

    @contextmanager
    def colliding_staging(**_kwargs):
        staging = target / "collision"
        staging.mkdir()
        try:
            yield str(staging)
        finally:
            staging.rmdir()

    monkeypatch.setattr("src.services.blob_workspace_mirror.TemporaryDirectory", colliding_staging)
    with pytest.raises(ValueError, match="collides with download staging"):
        await BlobWorkspaceMirror(settings).download(prefix, target)
    assert list(target.iterdir()) == [target / "unchanged"]
    assert (target / "unchanged").read_bytes() == b"retain"


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_workspace_mirror_round_trip_and_same_size_edits(backend, tmp_path):
    settings, client, prefix = backend
    mirror = BlobWorkspaceMirror(settings)
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / ".git/objects").mkdir(parents=True)
    (source / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (source / ".git/objects/pack").write_bytes(b"git-object")
    (source / "file").write_bytes(b"first")
    await mirror.upload(source, prefix)
    await mirror.download(prefix, target)
    assert (target / ".git/objects/pack").read_bytes() == b"git-object"
    # Same-size remote edits must never be skipped based on local timestamps.
    await client.put_object(
        Bucket=settings.storage_bucket, Key=prefix + "file", Body=b"other"
    )
    (target / "stale").write_bytes(b"remove")
    await mirror.download(prefix, target)
    assert (target / "file").read_bytes() == b"other"
    assert not (target / "stale").exists()
    # Both file -> directory and directory -> file transitions are exact.
    (source / "file").unlink()
    (source / "file").mkdir()
    (source / "file/child").write_bytes(b"nested")
    await mirror.upload(source, prefix)
    await mirror.download(prefix, target)
    assert (target / "file/child").read_bytes() == b"nested"
    (source / "file/child").unlink()
    (source / "file").rmdir()
    (source / "file").write_bytes(b"flat")
    await mirror.upload(source, prefix)
    await mirror.download(prefix, target)
    assert (target / "file").read_bytes() == b"flat"


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_git_checkpoint_restores_git_and_uncommitted_bytes(
    backend, tmp_path
):
    from git import Actor, Repo

    settings, client, _ = backend
    manager = GitRepoManager(settings)
    source, target = tmp_path / "source", tmp_path / "restored"
    repo = Repo.init(source, initial_branch="main")
    (source / "file").write_bytes(b"committed")
    repo.index.add(["file"])
    repo.index.commit("fixture", author=Actor("Fixture", "fixture@example.invalid"))
    (source / "file").write_bytes(b"uncommitted")
    checkpoint = await manager.checkpoint_workspace(source)
    try:
        target.mkdir()
        (target / "stale").write_bytes(b"delete")
        await manager.restore_workspace_checkpoint(checkpoint, target)
        restored = Repo(target)
        assert restored.head.commit.hexsha == repo.head.commit.hexsha
        assert restored.is_dirty()
        assert (target / "file").read_bytes() == b"uncommitted"
        assert not (target / "stale").exists()
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)
    pages = [
        page
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.storage_bucket,
            Prefix=manager._checkpoint_prefix(checkpoint),
        )
    ]
    assert not any(page.get("Contents") for page in pages)


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_download_rejects_nonportable_remote_path_before_local_changes(
    backend, tmp_path
):
    settings, client, prefix = backend
    target = tmp_path / "workspace"
    target.mkdir()
    (target / "retain").write_bytes(b"local")
    await client.put_object(
        Bucket=settings.storage_bucket, Key=prefix + "C:/escape", Body=b"unsafe"
    )
    with pytest.raises(ValueError, match="Unsafe"):
        await BlobWorkspaceMirror(settings).download(prefix, target)
    assert (target / "retain").read_bytes() == b"local"
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_conditional_delete_cannot_remove_newer_object(backend):
    from src.core.s3_transport import S3Error

    settings, client, prefix = backend
    key = prefix + "conditional-delete"
    old = await client.put_object(Bucket=settings.storage_bucket, Key=key, Body=b"old")
    await client.put_object(Bucket=settings.storage_bucket, Key=key, Body=b"new")
    with pytest.raises(S3Error) as raised:
        await client.delete_object(
            Bucket=settings.storage_bucket, Key=key, IfMatch=old["ETag"]
        )
    assert raised.value.status_code == 412
    response = await client.get_object(Bucket=settings.storage_bucket, Key=key)
    assert await response["Body"].read() == b"new"


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_git_sync_freshness_uses_native_mirror(
    backend, tmp_path, monkeypatch
):
    from src.services.git_repo_manager import (
        STORAGE_BASELINE_FILE,
        WorkspaceStorageChanged,
    )

    settings, client, prefix = backend

    manager = scoped_git_manager(settings, prefix, monkeypatch)
    source, target = tmp_path / "source", tmp_path / "target"
    (source / ".git").mkdir(parents=True)
    (source / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (source / "file").write_bytes(b"initial")
    await manager.sync_up(source)
    assert (source / ".git" / STORAGE_BASELINE_FILE).is_file()
    await manager.sync_down(target)
    await manager.ensure_storage_unchanged(target, "main")
    await client.put_object(
        Bucket=settings.storage_bucket, Key=prefix + "file", Body=b"changed"
    )
    with pytest.raises(WorkspaceStorageChanged):
        await manager.ensure_storage_unchanged(target, "main")
    assert (target / "file").read_bytes() == b"initial"
    with pytest.raises(WorkspaceStorageChanged):
        await manager.sync_up(target)
    response = await client.get_object(
        Bucket=settings.storage_bucket, Key=prefix + "file"
    )
    assert await response["Body"].read() == b"changed"
    await manager.sync_down(target)
    await manager.ensure_storage_unchanged(target, "main")
    assert (target / "file").read_bytes() == b"changed"


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_checkpoint_retry_after_successful_storage_publish_uses_current_baseline(
    backend, tmp_path, monkeypatch
):
    from git import Repo

    settings, client, prefix = backend
    manager = scoped_git_manager(settings, prefix, monkeypatch)
    source = tmp_path / "workspace"
    Repo.init(source, initial_branch="main")
    (source / "file").write_bytes(b"initial")
    await manager.sync_up(source)
    (source / "file").write_bytes(b"publication")
    checkpoint = await manager.checkpoint_workspace(source)
    try:
        # _repo publish succeeds; a subsequent App-preview step fails. The
        # durable checkpoint still contains its pre-publication baseline.
        await manager.sync_up(source)
        await manager.ensure_storage_unchanged(source, "main")
        await manager.restore_workspace_checkpoint(checkpoint, source)
        await manager.ensure_storage_unchanged(source, "main")
        await manager.sync_up(source)
        response = await client.get_object(
            Bucket=settings.storage_bucket, Key=prefix + "file"
        )
        assert await response["Body"].read() == b"publication"
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_checkout_publishes_consecutive_workspace_changes(
    backend, tmp_path, monkeypatch
):
    from git import Repo

    settings, client, prefix = backend
    manager = scoped_git_manager(settings, prefix, monkeypatch)
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", tmp_path / "workspace")
    async with manager.checkout() as work_dir:
        Repo.init(work_dir, initial_branch="main")
        (work_dir / "file").write_bytes(b"initial")
    async with manager.checkout() as work_dir:
        assert (work_dir / "file").read_bytes() == b"initial"
        (work_dir / "file").write_bytes(b"updated")
    response = await client.get_object(
        Bucket=settings.storage_bucket, Key=prefix + "file"
    )
    assert await response["Body"].read() == b"updated"


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_checkpoint_restore_cannot_absorb_concurrent_workspace_edit(
    backend, tmp_path, monkeypatch
):
    from git import Repo
    from src.services.git_repo_manager import WorkspaceStorageChanged

    settings, client, prefix = backend
    manager = scoped_git_manager(settings, prefix, monkeypatch)
    source = tmp_path / "workspace"
    Repo.init(source, initial_branch="main")
    (source / "file").write_bytes(b"initial")
    await manager.sync_up(source)
    checkpoint = await manager.checkpoint_workspace(source)
    original_download = BlobWorkspaceMirror.download

    async def concurrent_edit(self, remote_prefix, target):
        await client.put_object(
            Bucket=settings.storage_bucket, Key=prefix + "file", Body=b"editor-change"
        )
        await original_download(self, remote_prefix, target)

    monkeypatch.setattr(BlobWorkspaceMirror, "download", concurrent_edit)
    try:
        await manager.restore_workspace_checkpoint(checkpoint, source)
        with pytest.raises(WorkspaceStorageChanged):
            await manager.sync_up(source)
        response = await client.get_object(
            Bucket=settings.storage_bucket, Key=prefix + "file"
        )
        assert await response["Body"].read() == b"editor-change"
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_parent_requirements_fallback_without_s3(backend, monkeypatch):
    import asyncio
    from src.core.azure_blob_transport import SyncAzureBlobTransport
    from src.core.requirements_cache import _read_requirements_from_s3

    settings, client, _ = backend
    key = "_repo/requirements.txt"
    await client.put_object(
        Bucket=settings.storage_bucket, Key=key, Body=b"fixture-package==1\n"
    )
    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "azure_blob")
    monkeypatch.setenv("BIFROST_AZURE_BLOB_CONTAINER", settings.azure_blob_container)
    monkeypatch.delenv("BIFROST_S3_BUCKET", raising=False)

    def read():
        reader = SyncAzureBlobTransport(settings)
        try:
            return _read_requirements_from_s3(lambda: reader)
        finally:
            reader.close()

    try:
        assert await asyncio.to_thread(read) == "fixture-package==1\n"
    finally:
        await client.delete_object(Bucket=settings.storage_bucket, Key=key)


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_download_detects_mid_transfer_edit_before_local_replacement(
    backend, tmp_path, monkeypatch
):
    from src.services.blob_workspace_mirror import BlobWorkspaceChanged

    settings, client, prefix = backend
    target = tmp_path / "workspace"
    target.mkdir()
    (target / "file").write_bytes(b"retain-local")
    await client.put_object(
        Bucket=settings.storage_bucket, Key=prefix + "file", Body=b"remote-old"
    )
    mirror = BlobWorkspaceMirror(settings)
    original_listing = mirror._listing
    calls = 0

    async def listing_with_concurrent_writer(transport, remote_prefix):
        nonlocal calls
        calls += 1
        if calls == 2:
            await client.put_object(
                Bucket=settings.storage_bucket, Key=prefix + "file", Body=b"remote-new"
            )
        return await original_listing(transport, remote_prefix)

    monkeypatch.setattr(mirror, "_listing", listing_with_concurrent_writer)
    with pytest.raises(BlobWorkspaceChanged, match="during download"):
        await mirror.download(prefix, target)
    assert (target / "file").read_bytes() == b"retain-local"
    assert not list(tmp_path.glob(".bifrost-blob-*"))


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_incremental_blob_mirror_transfers_only_changed_bytes(
    backend, tmp_path, monkeypatch
):
    from src.core.azure_blob_transport import AzureBlobTransport

    settings, client, prefix = backend
    source, target = tmp_path / "source", tmp_path / "target"
    (source / ".git/objects/pack").mkdir(parents=True)
    pack = b"fixture-pack" * (3 * 1024**2)
    (source / ".git/objects/pack/fixture.pack").write_bytes(pack)
    for number in range(64):
        (source / f"file-{number:02d}").write_bytes(b"first")
    reads, writes = [], []
    original_get, original_put = (
        AzureBlobTransport.get_object,
        AzureBlobTransport.put_object,
    )

    async def read(self, **kwargs):
        result = await original_get(self, **kwargs)
        reads.append(kwargs["Key"])
        return result

    async def write(self, **kwargs):
        result = await original_put(self, **kwargs)
        writes.append(kwargs["Key"])
        return result

    monkeypatch.setattr(AzureBlobTransport, "get_object", read)
    monkeypatch.setattr(AzureBlobTransport, "put_object", write)
    mirror = BlobWorkspaceMirror(settings)
    await mirror.upload(source, prefix)
    assert len(writes) == 65
    writes.clear()
    await mirror.upload(source, prefix)
    assert writes == []
    await mirror.download(prefix, target)
    assert len(reads) == 65
    reads.clear()
    await mirror.download(prefix, target)
    assert reads == []
    # Same length, different content; only this object should cross the network.
    (source / "file-00").write_bytes(b"other")
    await mirror.upload(source, prefix)
    assert writes == [prefix + "file-00"]
    await mirror.download(prefix, target)
    assert reads == [prefix + "file-00"]
    assert (target / "file-00").read_bytes() == b"other"
    assert (target / ".git/objects/pack/fixture.pack").read_bytes() == pack
    # A remote ETag change invalidates the hint even with unchanged object size.
    await client.put_object(
        Bucket=settings.storage_bucket, Key=prefix + "file-00", Body=b"third"
    )
    reads.clear()
    await mirror.download(prefix, target)
    assert reads == [prefix + "file-00"]
    assert (target / "file-00").read_bytes() == b"third"
    # An uncached mirror must still materialize the complete real backend state.
    reads.clear()
    await BlobWorkspaceMirror(settings).download(prefix, target)
    assert len(reads) == 65


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_upload_cache_cannot_witness_bytes_changed_during_upload(
    backend, tmp_path, monkeypatch
):
    from src.core.azure_blob_transport import AzureBlobTransport

    settings, client, prefix = backend
    source = tmp_path / "source"
    source.mkdir()
    path = source / "file"
    path.write_bytes(b"original")
    original_put = AzureBlobTransport.put_object
    changed = False

    async def mutate_during_upload(self, **kwargs):
        nonlocal changed
        if kwargs["Key"] == prefix + "file" and not changed:
            changed = True
            path.write_bytes(b"mutated!")
            try:
                return await original_put(self, **kwargs)
            finally:
                # ABA edit: the current local hash again matches the hash
                # computed before the SDK consumed its upload body.
                path.write_bytes(b"original")
        return await original_put(self, **kwargs)

    monkeypatch.setattr(AzureBlobTransport, "put_object", mutate_during_upload)
    mirror = BlobWorkspaceMirror(settings)
    await mirror.upload(source, prefix)
    await mirror.upload(source, prefix)
    result = await client.get_object(
        Bucket=settings.storage_bucket, Key=prefix + "file"
    )
    assert await result["Body"].read() == path.read_bytes() == b"original"
