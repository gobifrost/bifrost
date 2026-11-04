"""Selected-provider consumer contracts, with independent transport readback.

These call storage services directly: zero deploy/build/publish platform jobs,
zero worker executions, and no package installs or live customer content.
"""

import base64
import gzip
import hashlib
from uuid import uuid4

import pytest

from src.services.application_deploy_storage import ApplicationDeployStorage
from src.services.application_source_artifact import ApplicationSourceArtifactStorage
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage


async def raw_bytes(client, settings, key):
    response = await client.get_object(Bucket=settings.storage_bucket, Key=key)
    async with response["Body"] as body:
        return await body.read()


@pytest.mark.parametrize(
    "storage_class", [ApplicationDeployStorage, SolutionDeployJobStorage]
)
@pytest.mark.asyncio
async def test_deploy_input_staging_uses_selected_transport(
    backend, tmp_path, storage_class
):
    settings, client, _ = backend
    storage = storage_class(uuid4(), settings)
    source, destination = tmp_path / "source.zip", tmp_path / "download.zip"
    data = b"fixture" * 1024
    source.write_bytes(data)
    try:
        digest, size = await storage.write_path(source)
        assert (digest, size) == (hashlib.sha256(data).hexdigest(), len(data))
        assert await raw_bytes(client, settings, storage.key) == data
        assert await storage.copy_to_path(destination, expected_sha256=digest) == size
        assert destination.read_bytes() == data
    finally:
        await storage.delete()
    assert not (
        await client.list_objects_v2(Bucket=settings.storage_bucket, Prefix=storage.key)
    )["Contents"]


@pytest.mark.asyncio
async def test_retained_app_source_uses_selected_transport(backend, tmp_path):
    settings, client, _ = backend
    storage = ApplicationSourceArtifactStorage(settings)
    app_id, deployment_id = uuid4(), uuid4()
    source, destination = tmp_path / "source.zip", tmp_path / "download.zip"
    data = b"sanitized-source-fixture"
    source.write_bytes(data)
    key = storage.deployment_source_key(app_id, deployment_id)
    try:
        await storage.write_deployment_source(app_id, deployment_id, source)
        assert await raw_bytes(client, settings, key) == data
        assert await storage.deployment_source_exists(app_id, deployment_id)
        assert await storage.read_deployment_source(app_id, deployment_id) == data
        assert await storage.copy_deployment_source_to_path(
            app_id, deployment_id, destination
        ) == len(data)
        assert destination.read_bytes() == data
    finally:
        await storage.delete_application_artifacts(app_id)
    assert not await storage.deployment_source_exists(app_id, deployment_id)


@pytest.mark.asyncio
async def test_app_build_dist_replaces_legacy_assets_without_touching_deployments(
    backend,
):
    from src.services.solutions.app_build import SolutionAppBuilder

    settings, client, _ = backend
    builder = SolutionAppBuilder(settings)
    app_id, deployment_id = uuid4(), uuid4()
    try:
        await builder.upload_dist(app_id, {"index.html": b"old", "stale.js": b"old"})
        await builder.upload_deployment(
            app_id, deployment_id, {"index.html": b"versioned"}
        )
        await builder.upload_dist(
            app_id, {"index.html": b"new", "assets/app.js": b"code"}
        )
        prefix = f"_apps/{app_id}/"
        assert await raw_bytes(client, settings, prefix + "dist/index.html") == b"new"
        assert (
            await raw_bytes(client, settings, prefix + "dist/assets/app.js") == b"code"
        )
        assert sorted(await builder.list_dist(app_id)) == [
            "assets/app.js",
            "index.html",
        ]
        assert await builder.read_dist(app_id, "assets/app.js") == b"code"
        assert (
            await raw_bytes(
                client,
                settings,
                prefix + f"deployments/{deployment_id}/dist/index.html",
            )
            == b"versioned"
        )
        await builder.delete_deployment(app_id, deployment_id)
        assert await builder.list_dist(app_id, deployment_id=deployment_id) == []
        assert await builder.read_dist(app_id, "index.html") == b"new"
    finally:
        await builder.delete_all_app_artifacts(app_id)
    assert not (
        await client.list_objects_v2(
            Bucket=settings.storage_bucket, Prefix=f"_apps/{app_id}/"
        )
    )["Contents"]


@pytest.mark.asyncio
async def test_retained_solution_source_uses_selected_transport(backend, tmp_path):
    settings, client, _ = backend
    storage = SolutionSourceArtifactStorage(uuid4(), settings)
    source, destination = tmp_path / "source.zip", tmp_path / "download.zip"
    data = b"solution-source-fixture"
    source.write_bytes(data)
    try:
        await storage.write_from_path(source)
        assert await raw_bytes(client, settings, storage._key()) == data
        assert await storage.read() == data
        assert await storage.copy_to_path(destination)
        assert destination.read_bytes() == data
    finally:
        await storage.delete()
    assert await storage.read() is None


@pytest.mark.asyncio
async def test_audit_archive_stream_uses_selected_transport(backend):
    from src.services.audit_retention.store import AuditArchiveStore

    settings, client, prefix = backend
    storage = AuditArchiveStore(settings)
    key, data = prefix + "audit.gz", gzip.compress(b'{"event":"fixture"}\n')

    async def chunks():
        yield data[:5]
        yield data[5:]

    await storage.put_chunks(key, chunks())
    assert await raw_bytes(client, settings, key) == data
    assert await storage.get(key) == data
    assert b"".join([chunk async for chunk in storage.iter_chunks(key)]) == data
    assert [item async for item in storage.iter_keys(prefix)] == [key]
    await storage.delete(key)
    assert [item async for item in storage.iter_keys(prefix)] == []


@pytest.mark.asyncio
async def test_workspace_bundle_staging_uses_selected_transport(backend, tmp_path):
    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage

    settings, client, _ = backend
    storage = WorkspaceBundleStorage(uuid4(), settings)
    source, destination = tmp_path / "source.zip", tmp_path / "download.zip"
    data = b"workspace-bundle-fixture"
    source.write_bytes(data)
    try:
        digest, size = await storage.stage_package(source)
        await storage.stage_metadata({"fixture": True, "digest": digest})
        assert await raw_bytes(client, settings, storage.package_key) == data
        assert (
            await storage.copy_package_to(destination, expected_sha256=digest) == size
        )
        assert destination.read_bytes() == data
        assert await storage.load_metadata() == {"fixture": True, "digest": digest}
    finally:
        await storage.delete()
    assert not (
        await client.list_objects_v2(
            Bucket=settings.storage_bucket, Prefix=storage.root + "/"
        )
    )["Contents"]


@pytest.mark.asyncio
async def test_hosted_sdk_artifact_uses_selected_transport(backend):
    from src.services.sdk_package import HostedSdkArtifact, HostedSdkArtifactStorage

    settings, client, _ = backend
    storage = HostedSdkArtifactStorage(settings)
    data = f"export const fixture = '{uuid4()}'".encode()
    digest = hashlib.sha256(data).digest()
    artifact = HostedSdkArtifact(
        1,
        1,
        "fixture",
        digest.hex(),
        "sha256-" + base64.b64encode(digest).decode(),
        data,
    )
    key = storage._key(artifact.fingerprint)
    try:
        await storage.publish(artifact)
        assert await raw_bytes(client, settings, key) == data
        assert await storage.read(artifact.fingerprint) == data
    finally:
        await client.delete_object(Bucket=settings.storage_bucket, Key=key)


@pytest.mark.asyncio
async def test_health_probe_uses_selected_transport_and_closes_it(backend, monkeypatch):
    from src.routers import health

    settings, _, _ = backend
    pool = health.S3HealthClient()
    monkeypatch.setattr(health, "_s3_health_client", pool)
    try:
        name, result = await health.check_s3(settings)
        assert name == "s3" and result["status"] == "healthy"
        assert pool.client is not None
    finally:
        await pool.close()
    assert pool.client is None and pool.context is None


@pytest.mark.asyncio
async def test_form_upload_response_materializes_in_selected_transport(
    backend, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import httpx
    from starlette.requests import Request
    from shared.file_paths import resolve_s3_key
    from src.models import FileUploadRequest
    from src.routers import forms
    import src.services.file_storage as storage_module

    settings, client, _ = backend
    form_id = uuid4()
    form = SimpleNamespace(is_active=True, fields=[])
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: form))
    )
    # Authorization has its own HTTP tests; isolate this protocol/data boundary.
    monkeypatch.setattr(forms, "_authorize_form_runtime", AsyncMock())
    monkeypatch.setattr(forms, "_limit_embed_action", AsyncMock())
    original_storage = storage_module.FileStorageService
    monkeypatch.setattr(
        storage_module,
        "FileStorageService",
        lambda session: original_storage(session, settings),
    )
    response = await forms.generate_upload_url(
        form_id,
        Request({"type": "http", "headers": []}),
        FileUploadRequest(
            file_name="fixture.txt", content_type="text/plain", file_size=7
        ),
        SimpleNamespace(user=SimpleNamespace(embed=False), org_id=None),
        None,
        db,
    )
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers.get("x-ms-blob-type") == (
        "BlockBlob" if settings.object_storage_provider == "azure_blob" else None
    )
    key = resolve_s3_key("uploads", "global", response.blob_uri)
    try:
        async with httpx.AsyncClient() as http:
            uploaded = await http.put(
                response.upload_url, content=b"fixture", headers=response.headers
            )
            assert uploaded.status_code in {200, 201, 204}
        assert await raw_bytes(client, settings, key) == b"fixture"
    finally:
        await client.delete_object(Bucket=settings.storage_bucket, Key=key)
