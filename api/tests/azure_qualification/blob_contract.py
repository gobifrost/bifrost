"""Explicit-selection real Azure lane; never discovered by the default suite.

Do not run without approval for the named existing account/container and costs.
No container, identity, key or grant is created. Local Azurite is a separate lane.
"""

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio

from src.config import Settings
from src.core.storage import object_pages
from src.core.storage.azure import AzureObjectStore
from src.core.storage.contracts import (
    ContentProperties, StorageError, WriteOptions,
)
from src.services.app_storage import AppStorageService


@pytest_asyncio.fixture
async def azure():
    endpoint = os.environ.get("BIFROST_AZURE_QUALIFICATION_ACCOUNT_URL")
    container = os.environ.get("BIFROST_AZURE_QUALIFICATION_CONTAINER")
    if not endpoint or not container:
        raise RuntimeError(
            "Real Azure qualification requires an explicitly approved endpoint and existing container"
        )
    settings = Settings(
        _env_file=None,
        object_storage_provider="azure_blob",
        azure_blob_account_url=endpoint,
        azure_blob_container=container,
        s3_bucket=None,
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_public_endpoint_url=None,
        s3_region="us-east-1",
    )
    assert not settings.s3_bucket
    prefix = f"_adapter_qualification/{uuid4().hex}/"
    async with AzureObjectStore.from_settings(settings) as store:
        yield settings, store, prefix
        listed = [item async for page in object_pages(store, prefix) for item in page.objects]
        if listed:
            await store.delete_many([item.key for item in listed])


@pytest.mark.asyncio
async def test_copy_pins_source_version_and_preserves_metadata(azure):
    import gzip

    settings, store, prefix = azure
    source, destination = prefix + "source", prefix + "destination"
    metadata = {"bifrost-move-row": "fixture", "Case": "value"}
    data = gzip.compress(b"original")
    properties = ContentProperties(
        content_type="text/plain", content_encoding="gzip", content_language="en",
        content_disposition='attachment; filename="contract.gz"',
        cache_control="no-store",
    )
    await store.write(source, data, options=WriteOptions(properties=properties, metadata=metadata))
    head = await store.stat(source)
    copied = await store.copy(head, destination)
    snapshot = await store.stat(destination)
    assert snapshot.version == copied.version
    assert snapshot.metadata == {key.lower(): value for key, value in metadata.items()}
    assert snapshot.properties == properties
    async with store.open_read(destination) as read:
        assert await read.body.read() == data
    await store.write(source, b"changed")
    with pytest.raises(StorageError) as raised:
        await store.copy(head, prefix + "stale")
    assert raised.value.kind == "precondition_failed"


@pytest.mark.asyncio
async def test_independent_conditional_writes_have_one_winner(azure):
    _, store, prefix = azure
    key = prefix + "race"

    async def create(data):
        return await store.write(key, data, options=WriteOptions(create_only=True))

    results = await asyncio.gather(create(b"first"), create(b"second"), return_exceptions=True)
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(failures) == 1 and failures[0].kind == "precondition_failed"
    async with store.open_read(key) as read:
        assert await read.body.read() in (b"first", b"second")


@pytest.mark.asyncio
async def test_app_preview_publish_uses_blob_without_s3(azure, monkeypatch):
    import json

    settings, store, _ = azure
    assert settings.s3_bucket is None and settings.s3_endpoint_url is None

    def forbidden_s3(_settings):
        raise AssertionError("Blob App storage attempted to construct the S3 adapter")

    monkeypatch.setattr("src.core.storage.s3.S3ObjectStore.from_settings", forbidden_s3)
    app_id = str(uuid4())
    storage = AppStorageService(settings)
    try:
        await storage.write_preview_file(app_id, "bundle.js", b"export default 1")
        await storage.write_preview_file(
            app_id, "manifest.json",
            json.dumps({"entry": "bundle.js", "outputs": ["bundle.js"]}).encode(),
        )
        assert await storage.publish(app_id) == 2
        # Independent Blob readback catches both write and read service bypasses.
        async with store.open_read(f"_apps/{app_id}/live/bundle.js") as read:
            assert await read.body.read() == b"export default 1"
        async with store.open_read(f"_apps/{app_id}/live/manifest.json") as read:
            assert json.loads(await read.body.read()) == {
                "entry": "bundle.js", "outputs": ["bundle.js"],
            }
    finally:
        await store.delete_many([f"_apps/{app_id}/live/bundle.js", f"_apps/{app_id}/live/manifest.json"])


@pytest.mark.asyncio
async def test_native_git_mirror_and_checkpoint_recover_local_bytes(azure, tmp_path):
    from git import Repo

    from src.services.git_repo_manager import GitRepoManager

    settings, _, prefix = azure
    source, target = tmp_path / "source", tmp_path / "restored"
    Repo.init(source, initial_branch="main")
    (source / "file").write_bytes(b"uncommitted fixture")
    manager = GitRepoManager(settings)
    checkpoint = await manager.checkpoint_workspace(source)
    try:
        await manager.restore_workspace_checkpoint(checkpoint, target)
        assert (target / "file").read_bytes() == b"uncommitted fixture"
        assert Repo(target).git.symbolic_ref("HEAD") == "refs/heads/main"
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)


@pytest.mark.asyncio
async def test_parent_synchronous_reader_uses_selected_container(azure):
    from src.core.storage import sync_object_store

    settings, store, prefix = azure
    key, data = prefix + "parent-module.py", b"VALUE = 'fixture'\n"
    await store.write(key, data)

    def read():
        reader = sync_object_store(settings)
        try:
            return reader.read(key)
        finally:
            reader.close()

    assert await asyncio.to_thread(read) == data
