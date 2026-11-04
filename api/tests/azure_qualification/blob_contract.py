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
from src.core.azure_blob_transport import AzureBlobTransport
from src.core.s3_transport import S3Error
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
    assert not settings.s3_configured
    prefix = f"_adapter_qualification/{uuid4().hex}/"
    async with AzureBlobTransport(settings) as client:
        yield settings, client, prefix
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=container, Prefix=prefix
        ):
            for item in page.get("Contents", []):
                await client.delete_object(Bucket=container, Key=item["Key"])


@pytest.mark.asyncio
async def test_copy_pins_source_version_and_preserves_metadata(azure):
    import gzip

    settings, client, prefix = azure
    bucket = settings.storage_bucket
    source, destination = prefix + "source", prefix + "destination"
    metadata = {"bifrost-move-row": "fixture", "Case": "value"}
    data = gzip.compress(b"original")
    headers = {
        "ContentType": "text/plain",
        "ContentEncoding": "gzip",
        "ContentLanguage": "en",
        "ContentDisposition": 'attachment; filename="contract.gz"',
        "CacheControl": "no-store",
    }
    await client.put_object(
        Bucket=bucket,
        Key=source,
        Body=data,
        Metadata=metadata,
        **headers,
    )
    head = await client.head_object(Bucket=bucket, Key=source)
    await client.copy_object(
        Bucket=bucket,
        Key=destination,
        CopySource={"Bucket": bucket, "Key": source},
        CopySourceIfMatch=head["ETag"],
    )
    copied = await client.head_object(Bucket=bucket, Key=destination)
    assert copied["Metadata"] == {key.lower(): value for key, value in metadata.items()}
    assert {name: copied[name] for name in headers} == headers
    response = await client.get_object(Bucket=bucket, Key=destination)
    assert await response["Body"].read() == data
    await client.put_object(Bucket=bucket, Key=source, Body=b"changed")
    with pytest.raises(S3Error) as raised:
        await client.copy_object(
            Bucket=bucket,
            Key=prefix + "stale",
            CopySource={"Bucket": bucket, "Key": source},
            CopySourceIfMatch=head["ETag"],
        )
    assert raised.value.status_code == 412


@pytest.mark.asyncio
async def test_stale_source_block_copy_is_refused(azure):
    settings, client, prefix = azure
    bucket, source, destination = (
        settings.storage_bucket,
        prefix + "source",
        prefix + "destination",
    )
    await client.put_object(Bucket=bucket, Key=source, Body=b"original")
    head = await client.head_object(Bucket=bucket, Key=source)
    upload = (await client.create_multipart_upload(Bucket=bucket, Key=destination))[
        "UploadId"
    ]
    try:
        await client.put_object(Bucket=bucket, Key=source, Body=b"changed!")
        with pytest.raises(S3Error) as raised:
            await client.upload_part_copy(
                Bucket=bucket,
                Key=destination,
                UploadId=upload,
                PartNumber=1,
                CopySource={"Bucket": bucket, "Key": source},
                CopySourceRange="bytes=0-7",
                CopySourceIfMatch=head["ETag"],
            )
        assert raised.value.status_code == 412
    finally:
        await client.abort_multipart_upload(
            Bucket=bucket, Key=destination, UploadId=upload
        )


@pytest.mark.asyncio
async def test_independent_conditional_completions_have_one_winner(azure):
    settings, client, prefix = azure
    bucket, key = settings.storage_bucket, prefix + "race"
    requests = []
    for data in (b"first", b"second"):
        upload = (await client.create_multipart_upload(Bucket=bucket, Key=key))[
            "UploadId"
        ]
        part = await client.upload_part(
            Bucket=bucket, Key=key, UploadId=upload, PartNumber=1, Body=data
        )
        requests.append((upload, part["ETag"]))

    async def complete(upload, etag):
        return await client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload,
            MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": etag}]},
            IfNoneMatch="*",
        )

    results = await asyncio.gather(
        *(complete(*request) for request in requests), return_exceptions=True
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert (
        sum(
            isinstance(result, S3Error) and result.status_code == 412
            for result in results
        )
        == 1
    )
    for upload, _ in requests:
        if upload in client._uploads:
            await client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)
    response = await client.get_object(Bucket=bucket, Key=key)
    assert await response["Body"].read() in (b"first", b"second")


@pytest.mark.asyncio
async def test_app_preview_publish_uses_blob_without_s3(azure, monkeypatch):
    import json

    settings, client, _ = azure
    assert settings.s3_bucket is None and settings.s3_endpoint_url is None

    def forbidden_s3(**_kwargs):
        raise AssertionError("Blob App storage attempted to construct S3 transport")

    monkeypatch.setattr("src.services.app_storage.S3Transport", forbidden_s3)
    app_id = str(uuid4())
    storage = AppStorageService(settings)
    try:
        await storage.write_preview_file(app_id, "bundle.js", b"export default 1")
        await storage.write_preview_file(
            app_id,
            "manifest.json",
            json.dumps({"entry": "bundle.js", "outputs": ["bundle.js"]}).encode(),
        )
        assert await storage.publish(app_id) == 2
        # Independent Blob readback catches both write and read service bypasses.
        response = await client.get_object(
            Bucket=settings.azure_blob_container, Key=f"_apps/{app_id}/live/bundle.js"
        )
        assert await response["Body"].read() == b"export default 1"
        response = await client.get_object(
            Bucket=settings.azure_blob_container,
            Key=f"_apps/{app_id}/live/manifest.json",
        )
        assert json.loads(await response["Body"].read()) == {
            "entry": "bundle.js",
            "outputs": ["bundle.js"],
        }
    finally:
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.storage_bucket, Prefix=f"_apps/{app_id}/"
        ):
            for item in page.get("Contents", []):
                await client.delete_object(
                    Bucket=settings.storage_bucket, Key=item["Key"]
                )


@pytest.mark.asyncio
async def test_native_git_mirror_and_checkpoint_recover_local_bytes(
    azure, tmp_path, monkeypatch
):
    from git import Repo
    import src.services.repo_storage as repo_module
    from src.services.blob_workspace_mirror import BlobWorkspaceMirror
    from src.services.git_repo_manager import GitRepoManager

    settings, _, prefix = azure
    source, target = tmp_path / "source", tmp_path / "restored"
    Repo.init(source, initial_branch="main")
    (source / "file").write_bytes(b"uncommitted fixture")
    mirror = BlobWorkspaceMirror(settings)
    await mirror.upload(source, prefix + "git/")
    await mirror.download(prefix + "git/", target)
    assert (target / "file").read_bytes() == b"uncommitted fixture"
    manager = GitRepoManager(settings)
    # Keep freshness metadata reads inside the approved qualification prefix.
    monkeypatch.setattr(repo_module, "REPO_PREFIX", prefix + "git/")
    checkpoint = await manager.checkpoint_workspace(source)
    try:
        (target / "file").write_bytes(b"overwritten local fixture")
        await manager.restore_workspace_checkpoint(checkpoint, target)
        assert (target / "file").read_bytes() == b"uncommitted fixture"
        assert Repo(target).git.symbolic_ref("HEAD") == "refs/heads/main"
    finally:
        await manager.delete_workspace_checkpoint(checkpoint)


@pytest.mark.asyncio
async def test_parent_synchronous_transport_reads_selected_container(azure):
    from src.core.azure_blob_transport import SyncAzureBlobTransport

    settings, client, prefix = azure
    key, data = prefix + "parent-module.py", b"VALUE = 'fixture'\n"
    await client.put_object(Bucket=settings.storage_bucket, Key=key, Body=data)

    def read():
        reader = SyncAzureBlobTransport(settings)
        try:
            return reader.get_object(Bucket=settings.storage_bucket, Key=key)[
                "Body"
            ].read()
        finally:
            reader.close()

    assert await asyncio.to_thread(read) == data


@pytest.mark.parametrize(
    "case",
    [
        "test_object_bytes_metadata_conditions_and_delete",
        "test_content_encoding_preserves_stored_bytes_and_headers",
        "test_paginated_listing_preserves_keys_and_directories",
        "test_multipart_completion_refuses_existing_destination",
        "test_multipart_completion_activates_exact_parts_and_metadata",
        "test_multipart_completion_checks_current_and_stale_etag",
        "test_signed_http_round_trip",
        "test_streaming_wrapper_and_synchronous_import_reader",
    ],
)
@pytest.mark.asyncio
async def test_shared_contract_on_real_azure(azure, monkeypatch, case):
    """Execute the same assertions used by S3/Azurite against real Blob."""
    from tests.e2e.platform import test_object_storage_contract as contracts

    contract = getattr(contracts, case)
    if case == "test_streaming_wrapper_and_synchronous_import_reader":
        await contract(azure, monkeypatch)
    else:
        await contract(azure)
