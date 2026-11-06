"""The neutral read contract against the native local S3 service."""

from uuid import uuid4

import pytest

from src.config import get_settings
from src.core.s3_transport import S3Transport
from src.core.storage import object_store
from src.core.storage.contracts import ContentProperties, StorageError, WriteOptions


@pytest.mark.e2e
async def test_signed_request_headers_are_in_the_served_api_schema():
    """Keep client generation bound to the running API's signed-request DTOs."""
    from pathlib import Path
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get("http://api:8000/openapi.json")
        response.raise_for_status()
    schemas = response.json()["components"]["schemas"]
    for name in ("SignedUrlResponse", "SignedUrlBatchResult", "FileUploadResponse"):
        assert "headers" in schemas[name]["properties"]
    Path("/tmp/bifrost/signed-header-openapi.json").write_bytes(response.content)


@pytest.mark.e2e
async def test_configured_store_reads_metadata_and_refuses_stale_versions():
    settings = get_settings()
    key = f"tests/object-storage/{uuid4().hex}/source.bin"
    async with S3Transport(
        endpoint_url=settings.s3_endpoint_url, access_key_id=settings.s3_access_key,
        secret_access_key=settings.s3_secret_key, region=settings.s3_region,
    ) as native:
        try:
            await native.put_object(
                Bucket=settings.s3_bucket, Key=key, Body=b"first content",
                ContentType="application/octet-stream", Metadata={"fixture": "neutral"},
            )
            async with object_store(settings) as store:
                await store.probe()
                info = await store.stat(key)
                async with store.open_read(key, expected_version=info.version) as read:
                    assert read.info.metadata == {"fixture": "neutral"}
                    assert await read.body.read() == b"first content"
                await native.put_object(Bucket=settings.s3_bucket, Key=key, Body=b"newer content")
                with pytest.raises(StorageError) as caught:
                    async with store.open_read(key, expected_version=info.version):
                        pytest.fail("Stale reads must refuse before exposing a body")
                assert caught.value.kind == "precondition_failed"
        finally:
            await native.delete_object(Bucket=settings.s3_bucket, Key=key)


@pytest.mark.e2e
async def test_neutral_conditional_writes_and_signed_access_round_trip():
    import httpx

    from src.core.storage import object_pages

    prefix = f"tests/neutral-write/{uuid4().hex}/"
    key, browser_key = prefix + "source", prefix + "browser"
    async with object_store(get_settings()) as store:
        try:
            first = await store.write(key, b"first", options=WriteOptions(create_only=True))
            with pytest.raises(StorageError) as duplicate:
                await store.write(key, b"duplicate", options=WriteOptions(create_only=True))
            assert duplicate.value.kind == "precondition_failed"
            second = await store.write(key, b"second", options=WriteOptions(
                expected_version=first.version,
                properties=ContentProperties(content_type="text/plain"), metadata={"test": "neutral"},
            ))
            assert second.version != first.version
            with pytest.raises(StorageError) as stale:
                await store.write(key, b"stale", options=WriteOptions(expected_version=first.version))
            assert stale.value.kind == "precondition_failed"
            ticket = await store.signed_request(browser_key, "PUT", content_type="text/plain")
            async with httpx.AsyncClient() as http:
                uploaded = await http.request(ticket.method, ticket.url, headers=ticket.headers, content=b"browser bytes")
                assert uploaded.status_code in {200, 201}  # S3 PUT / Blob Put Blob.
                download = await store.signed_request(browser_key, "GET")
                downloaded = await http.request(download.method, download.url, headers=download.headers)
                assert downloaded.status_code == 200 and downloaded.content == b"browser bytes"
            keys = [item.key async for page in object_pages(store, prefix, limit=1) for item in page.objects]
            assert sorted(keys) == sorted([key, browser_key])
            assert (await store.stat(key)).metadata == {"test": "neutral"}
        finally:
            await store.delete(key)
            await store.delete(browser_key)


@pytest.mark.e2e
async def test_workspace_and_solution_sources_remain_isolated():
    from src.services.repo_storage import RepoStorage
    from src.services.solutions.storage import SolutionStorage

    path = f"tests/neutral-source/{uuid4().hex}/module.py"
    repo = RepoStorage()
    solution = SolutionStorage(uuid4())
    try:
        await repo.write(path, b"workspace")
        await solution.write(path, b"solution")
        assert await repo.read(path) == b"workspace"
        assert await solution.read(path) == b"solution"
        assert (await repo.head(path)).version == (await repo.list_with_metadata(path))[path].version
        assert (await solution.list_with_metadata(path))[path].size == len(b"solution")
        assert await repo.content_hash(path) == repo.compute_hash(b"workspace")
        assert await repo.prefix_exists(path.rsplit("/", 1)[0])
    finally:
        await repo.delete(path)
        await solution.delete(path)

@pytest.mark.e2e
async def test_streamed_upload_and_conditional_copy_preserve_bytes_and_refuse_collisions():
    import hashlib

    from src.core.storage.contracts import CopyConditionFailed, CopyOptions

    prefix = f"tests/neutral-transfers/{uuid4().hex}/"
    source_key, destination = prefix + "source", prefix + "copy"
    content = b"multipart fixture\n" * (9 * 1024 * 1024 // 18)
    async def chunks():
        for offset in range(0, len(content), 1024 * 1024):
            yield content[offset:offset + 1024 * 1024]
    async with object_store(get_settings()) as store:
        try:
            uploaded = await store.write_stream(source_key, chunks(), options=WriteOptions(
                properties=ContentProperties(content_type="text/plain", cache_control="private"),
                metadata={"fixture": "stream"}, create_only=True,
            ))
            assert uploaded.size == len(content) and uploaded.sha256 == hashlib.sha256(content).hexdigest()
            snapshot = await store.stat(source_key)
            assert snapshot.version == uploaded.version
            copied = await store.copy(snapshot, destination, options=CopyOptions(create_only=True))
            async with store.open_read(destination, expected_version=copied.version) as read:
                assert read.info.metadata == {"fixture": "stream"}
                assert read.info.properties.cache_control == "private"
                assert await read.body.read() == content
            with pytest.raises(CopyConditionFailed) as collision:
                await store.copy(snapshot, destination, options=CopyOptions(create_only=True))
            assert collision.value.side == "destination"
            await store.write(source_key, b"newer")
            with pytest.raises(CopyConditionFailed) as stale:
                await store.copy(snapshot, destination, options=CopyOptions(expected_version=copied.version))
            assert stale.value.side == "source"
        finally:
            await store.delete(source_key)
            await store.delete(destination)

@pytest.mark.e2e
async def test_app_publish_promotes_declared_artifacts_and_retained_source_round_trips(tmp_path):
    import json

    from src.services.app_storage import AppStorageService
    from src.services.solutions.source_artifact import SolutionSourceArtifactStorage

    app_id, solution_id = str(uuid4()), uuid4()
    apps = AppStorageService()
    source = SolutionSourceArtifactStorage(solution_id)
    archive = tmp_path / "source.zip"
    recovered = tmp_path / "recovered.zip"
    archive.write_bytes(b"retained source bytes")
    preview = f"_apps/{app_id}/preview/"
    live = f"_apps/{app_id}/live/"
    async with object_store(get_settings()) as store:
        try:
            await source.write_from_path(archive)
            assert await source.copy_to_path(recovered)
            assert recovered.read_bytes() == archive.read_bytes()
            await apps.write_preview_file(app_id, "entry.js", b"current app")
            await apps.write_preview_file(app_id, "stale.js", b"stale app")
            manifest = json.dumps({"entry": "entry.js", "outputs": ["entry.js"]}).encode()
            await apps.write_preview_file(app_id, "manifest.json", manifest)
            assert await apps.publish(app_id) == 2
            assert await apps.read_file(app_id, "live", "entry.js") == b"current app"
            assert await apps.read_file(app_id, "live", "manifest.json") == manifest
            assert sorted(await apps.list_files(app_id, "live")) == ["entry.js", "manifest.json"]
            assert "stale.js" not in await apps.list_files(app_id, "preview")
        finally:
            await source.delete()
            await store.delete_many([preview + name for name in ("entry.js", "stale.js", "manifest.json")]
                                    + [live + name for name in ("entry.js", "manifest.json")])

@pytest.mark.e2e
async def test_versioned_app_build_provenance_and_cleanup_use_shared_storage():
    from src.services.solutions.app_build import SolutionAppBuilder

    app_id, deployment = uuid4(), uuid4()
    builder = SolutionAppBuilder()
    dist = {"index.html": b"<html>fixture</html>", "assets/binary.bin": b"\x00\xff"}
    try:
        await builder.upload_deployment(app_id, deployment, dist)
        await builder.write_build_identity(app_id, deployment, "fixture-inputs", dist)
        assert await builder.can_reuse(app_id, deployment, "fixture-inputs")
        assert not await builder.can_reuse(app_id, deployment, "different-inputs")
        assert sorted(await builder.list_dist(app_id, deployment_id=deployment)) == sorted(dist)
        assert await builder.read_dist(app_id, "assets/binary.bin", deployment_id=deployment) == b"\x00\xff"
        await builder.delete_deployment(app_id, deployment)
        assert not await builder.can_reuse(app_id, deployment, "fixture-inputs")
        assert await builder.list_dist(app_id, deployment_id=deployment) == []
    finally:
        await builder.delete_all_app_artifacts(app_id)

@pytest.mark.e2e
async def test_sync_parent_reader_uses_same_namespace_and_bytes():
    import asyncio
    from src.core.storage import sync_object_store

    settings = get_settings()
    key = f"_solutions/{uuid4()}/modules/parent.py"
    async with object_store(settings) as writer:
        try:
            await writer.write(key, b"PARENT_VALUE = 42\n")
            def read():
                reader = sync_object_store(settings)
                try:
                    return reader.read(key)
                finally:
                    reader.close()
            assert await asyncio.to_thread(read) == b"PARENT_VALUE = 42\n"
        finally:
            await writer.delete(key)

@pytest.mark.e2e
async def test_independent_app_source_and_staging_integrity_round_trip(tmp_path):
    import hashlib
    from src.services.application_deploy_storage import ApplicationDeployStorage, ApplicationDeployInputIntegrityError
    from src.services.application_source_artifact import ApplicationSourceArtifactStorage

    staged = ApplicationDeployStorage(uuid4())
    retained = ApplicationSourceArtifactStorage()
    app_id, deployment = uuid4(), uuid4()
    path, recovered = tmp_path / "input.zip", tmp_path / "recovered.zip"
    content = b"\x00\xffretained app source"
    path.write_bytes(content)
    try:
        digest, size = await staged.write_path(path)
        assert (digest, size) == (hashlib.sha256(content).hexdigest(), len(content))
        assert await staged.copy_to_path(recovered, expected_sha256=digest) == size
        assert recovered.read_bytes() == content
        with pytest.raises(ApplicationDeployInputIntegrityError):
            await staged.copy_to_path(recovered, expected_sha256="0" * 64)
        assert not recovered.exists()
        assert await retained.write_deployment_source(app_id, deployment, path) == (digest, size)
        assert await retained.deployment_source_exists(app_id, deployment)
        assert await retained.read_deployment_source(app_id, deployment) == content
        assert await retained.copy_deployment_source_to_path(app_id, deployment, recovered) == size
        assert recovered.read_bytes() == content
        await retained.delete_application_artifacts(app_id)
        assert not await retained.deployment_source_exists(app_id, deployment)
        with pytest.raises(FileNotFoundError):
            _ = [chunk async for chunk in retained.iter_deployment_source(app_id, deployment)]
    finally:
        await staged.delete()
        await retained.delete_application_artifacts(app_id)

@pytest.mark.e2e
async def test_git_pointer_reads_legacy_wire_witness_and_refuses_stale_update():
    import json
    from types import SimpleNamespace
    from src.services.git_workspace_state import GitWorkspaceState, STATE_KEY

    settings = get_settings()
    original = {"checkpoint": str(uuid4()), "baseline": "a" * 64, "fingerprint": "b" * 64, "version": 1}
    async with object_store(settings) as store:
        try:
            # A pre-refactor writer publishes native S3 bytes and a raw quoted witness.
            async with S3Transport(endpoint_url=settings.s3_endpoint_url, access_key_id=settings.s3_access_key,
                                   secret_access_key=settings.s3_secret_key, region=settings.s3_region) as native:
                legacy = await native.put_object(Bucket=settings.s3_bucket, Key=STATE_KEY, Body=json.dumps(original).encode())
            state, witness = await GitWorkspaceState(SimpleNamespace(_settings=settings)).read()
            assert state.checkpoint == original["checkpoint"] and witness == legacy["ETag"]
            assert witness.startswith('"') and witness.endswith('"')
            await store.write(STATE_KEY, b"concurrent update")
            with pytest.raises(StorageError) as caught:
                await store.write(STATE_KEY, b"stale update", options=WriteOptions(expected_version=store.restore_version(witness)))
            assert caught.value.kind == "precondition_failed"
            async with store.open_read(STATE_KEY) as read:
                assert await read.body.read() == b"concurrent update"
        finally:
            await store.delete(STATE_KEY)


@pytest.mark.e2e
async def test_conditioned_delete_refuses_replacing_writer_and_preserves_its_bytes():
    key = f"tests/conditional-delete/{uuid4().hex}"
    async with object_store(get_settings()) as store:
        try:
            original = await store.write(key, b"original")
            replacement = await store.write(key, b"replacement")
            with pytest.raises(StorageError) as stale:
                await store.delete(key, expected_version=original.version)
            assert stale.value.kind == "precondition_failed"
            async with store.open_read(key) as read:
                assert await read.body.read() == b"replacement"
            await store.delete(key, expected_version=replacement.version)
            with pytest.raises(StorageError) as missing:
                await store.stat(key)
            assert missing.value.kind == "not_found"
        finally:
            await store.delete(key)


@pytest.mark.e2e
async def test_provider_checkpoint_restores_git_identity_and_uncommitted_bytes(tmp_path):
    from git import Repo
    from src.services.git_repo_manager import GitRepoManager

    source, restored = tmp_path / "pending-source", tmp_path / "new-executor"
    source.mkdir()
    repo = Repo.init(source, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", "Storage Fixture")
        config.set_value("user", "email", "storage-fixture@example.invalid")
    (source / "tracked.txt").write_text("committed")
    repo.index.add(["tracked.txt"])
    committed = repo.index.commit("fixture baseline").hexsha
    (source / "tracked.txt").write_text("uncommitted edits")
    (source / "untracked.bin").write_bytes(b"\x00\xffpending")
    manager = GitRepoManager(get_settings())
    checkpoint = await manager.checkpoint_workspace(source)
    try:
        await manager.restore_workspace_checkpoint(checkpoint, restored)
        assert Repo(restored).head.commit.hexsha == committed
        assert Repo(restored).is_dirty(untracked_files=True)
        assert (restored / "tracked.txt").read_text() == "uncommitted edits"
        assert (restored / "untracked.bin").read_bytes() == b"\x00\xffpending"
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)


@pytest.mark.e2e
async def test_encoded_keys_and_compressed_representation_survive_all_read_paths():
    import asyncio
    import gzip
    import httpx
    from src.core.storage import object_pages, sync_object_store
    settings = get_settings()
    prefix = f"tests/encoded-body/{uuid4().hex}/"
    key = prefix + "space + percent% café 日本語.json.gz"
    plain = b'{"fixture":true}'
    encoded = gzip.compress(plain, mtime=0)
    properties = ContentProperties(content_type="application/json", content_encoding="gzip", content_language="en",
                                   content_disposition="attachment", cache_control="private, max-age=60")
    async with object_store(settings) as store:
        try:
            written = await store.write(key, encoded, options=WriteOptions(properties=properties))
            async with store.open_read(key, expected_version=written.version) as read:
                assert read.info.properties == properties
                assert await read.body.read() == encoded
            assert [item.key async for page in object_pages(store, prefix, limit=1) for item in page.objects] == [key]
            def parent_read():
                reader = sync_object_store(settings)
                try:
                    return reader.read(key)
                finally:
                    reader.close()
            assert await asyncio.to_thread(parent_read) == encoded
            signed = await store.signed_request(key, "GET", response_content_disposition='attachment; filename="fixture.json"')
            async with httpx.AsyncClient() as client:
                downloaded = await client.request(signed.method, signed.url, headers=signed.headers)
                assert downloaded.status_code == 200 and downloaded.content == plain
                assert downloaded.headers["content-disposition"] == 'attachment; filename="fixture.json"'
        finally:
            await store.delete(key)
