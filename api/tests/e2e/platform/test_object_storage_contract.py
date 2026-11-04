"""Mandatory real local backend matrix. No environment-dependent parametrization.

Start the normal worktree test stack before selecting this file.
Neither backend uses cloud credentials or pre-existing objects.
"""

import asyncio
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from src.core.azure_blob_transport import SyncAzureBlobTransport
from src.core.s3_transport import S3Error, S3NoSuchKey, SyncS3Transport
from src.core.storage_transport import storage_transport
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SolutionStorage
from src.services.file_storage.s3_client import S3StorageClient
from src.services.app_storage import AppStorageService
from tests.storage_contract import LOCAL_ACCOUNT_KEY, local_settings


def test_local_settings_override_inherited_backend_configuration(monkeypatch):
    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "azure_blob")
    for name in (
        "BUCKET",
        "ENDPOINT_URL",
        "ACCESS_KEY",
        "SECRET_KEY",
        "REGION",
        "PUBLIC_ENDPOINT_URL",
    ):
        monkeypatch.setenv(f"BIFROST_S3_{name}", "inherited-fixture-only")
    s3, blob = local_settings("s3"), local_settings("azure_blob")
    assert s3.object_storage_provider == "s3"
    assert s3.s3_endpoint_url == "http://seaweedfs:8333"
    assert blob.object_storage_provider == "azure_blob"
    assert not blob.s3_configured
    assert (
        blob.s3_bucket,
        blob.s3_endpoint_url,
        blob.s3_access_key,
        blob.s3_secret_key,
        blob.s3_public_endpoint_url,
    ) == (None,) * 5
    assert blob.s3_region == "us-east-1"


@pytest.mark.asyncio
async def test_object_bytes_metadata_conditions_and_delete(backend):
    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "space + unicode-é.txt"
    await client.put_object(
        Bucket=bucket,
        Key=key,
        Body=b"first",
        ContentType="text/plain",
        CacheControl="no-cache",
        Metadata={"Purpose": "contract"},
        IfNoneMatch="*",
    )
    head = await client.head_object(Bucket=bucket, Key=key)
    listing = await client.list_objects_v2(Bucket=bucket, Prefix=key)
    assert listing["Contents"][0]["ETag"] == head["ETag"]
    assert head["ContentLength"] == 5
    assert head["Metadata"] == {"purpose": "contract"}
    assert head["CacheControl"] == "no-cache"
    assert head["ContentType"] == "text/plain"
    with pytest.raises(S3Error):
        await client.put_object(
            Bucket=bucket, Key=key, Body=b"collision", IfNoneMatch="*"
        )
    await client.put_object(Bucket=bucket, Key=key, Body=b"first", IfMatch=head["ETag"])
    with pytest.raises(S3Error):
        await client.put_object(
            Bucket=bucket, Key=key, Body=b"stale", IfMatch='"missing-version"'
        )
    response = await client.get_object(Bucket=bucket, Key=key)
    async with response["Body"] as body:
        assert await body.read(2) == b"fi"
        assert await body.read() == b"rst"
    await client.delete_object(Bucket=bucket, Key=key)
    await client.delete_object(Bucket=bucket, Key=key)
    with pytest.raises(S3NoSuchKey):
        await client.get_object(Bucket=bucket, Key=key)


@pytest.mark.asyncio
async def test_content_encoding_preserves_stored_bytes_and_headers(backend):
    import gzip

    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "compressed"
    data = gzip.compress(b"exact stored bytes")
    headers = {
        "ContentType": "text/plain",
        "ContentEncoding": "gzip",
        "ContentLanguage": "en",
        "ContentDisposition": 'attachment; filename="contract.gz"',
        "CacheControl": "no-store",
    }
    await client.put_object(Bucket=bucket, Key=key, Body=data, **headers)
    head = await client.head_object(Bucket=bucket, Key=key)
    assert {name: head[name] for name in headers} == headers
    response = await client.get_object(Bucket=bucket, Key=key)
    assert await response["Body"].read() == data
    assert (
        b"".join(
            [
                chunk
                async for chunk in S3StorageClient(settings).iter_object_chunks(
                    key, chunk_size=3
                )
            ]
        )
        == data
    )
    if settings.object_storage_provider == "azure_blob":

        def read_sync():
            reader = SyncAzureBlobTransport(settings)
            try:
                return reader.get_object(Bucket=bucket, Key=key)["Body"].read()
            finally:
                reader.close()

        assert await asyncio.to_thread(read_sync) == data


@pytest.mark.parametrize("backend", ["azure_blob"], indirect=True)
@pytest.mark.asyncio
async def test_blob_batch_delete_reports_lease_failure_and_continues(backend):
    from azure.core.credentials import AzureNamedKeyCredential
    from azure.storage.blob.aio import BlobServiceClient

    settings, client, prefix = backend
    bucket = settings.storage_bucket
    before, leased, after, missing = [
        prefix + name for name in ("before", "leased", "after", "missing")
    ]
    for key in (before, leased, after):
        await client.put_object(Bucket=bucket, Key=key, Body=b"fixture")
    async with BlobServiceClient(
        settings.azure_blob_account_url,
        credential=AzureNamedKeyCredential("bifrostcontract", LOCAL_ACCOUNT_KEY),
    ) as native:
        lease = await native.get_blob_client(bucket, leased).acquire_lease()
        try:
            response = await client.delete_objects(
                Bucket=bucket,
                Delete={
                    "Objects": [
                        {"Key": key} for key in (before, leased, missing, after)
                    ]
                },
            )
            assert response["Deleted"] == [
                {"Key": key} for key in (before, missing, after)
            ]
            assert response["Errors"] == [
                {
                    "Key": leased,
                    "Code": "PreconditionFailed",
                    "Message": "Azure Blob operation failed",
                }
            ]
            assert (
                await (await client.get_object(Bucket=bucket, Key=leased))[
                    "Body"
                ].read()
                == b"fixture"
            )
            assert [
                item["Key"]
                for item in (
                    await client.list_objects_v2(Bucket=bucket, Prefix=prefix)
                )["Contents"]
            ] == [leased]
        finally:
            await lease.release()
    await client.delete_object(Bucket=bucket, Key=leased)


@pytest.mark.asyncio
async def test_paginated_listing_preserves_keys_and_directories(backend):
    settings, client, prefix = backend
    bucket = settings.storage_bucket
    keys = [prefix + name for name in ("one", "two", "nested/a", "nested/b", "z+ é")]
    for key in keys:
        await client.put_object(Bucket=bucket, Key=key, Body=b"value")
    pages = [
        page
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix, MaxKeys=2
        )
    ]
    assert len(pages) >= 3
    assert {item["Key"] for page in pages for item in page["Contents"]} == set(keys)
    pages = [
        page
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix, Delimiter="/", MaxKeys=2
        )
    ]
    assert {item["Key"] for page in pages for item in page["Contents"]} == {
        keys[0],
        keys[1],
        keys[4],
    }
    assert {
        item["Prefix"] for page in pages for item in page.get("CommonPrefixes", [])
    } == {prefix + "nested/"}


@pytest.mark.asyncio
async def test_multipart_completion_refuses_existing_destination(backend):
    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "multipart"
    upload = (await client.create_multipart_upload(Bucket=bucket, Key=key))["UploadId"]
    try:
        part = await client.upload_part(
            Bucket=bucket, Key=key, UploadId=upload, PartNumber=1, Body=b"part"
        )
        await client.put_object(Bucket=bucket, Key=key, Body=b"winner")
        with pytest.raises(S3Error):
            await client.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload,
                MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
                IfNoneMatch="*",
            )
        response = await client.get_object(Bucket=bucket, Key=key)
        assert await response["Body"].read() == b"winner"
    finally:
        await client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)


@pytest.mark.asyncio
async def test_multipart_completion_activates_exact_parts_and_metadata(backend):
    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "committed"
    upload = (
        await client.create_multipart_upload(
            Bucket=bucket,
            Key=key,
            Metadata={"bifrost-move-row": "receipt"},
            ContentType="text/plain",
        )
    )["UploadId"]
    part = await client.upload_part(
        Bucket=bucket, Key=key, UploadId=upload, PartNumber=1, Body=b"committed bytes"
    )
    await client.complete_multipart_upload(
        Bucket=bucket,
        Key=key,
        UploadId=upload,
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
        IfNoneMatch="*",
    )
    head = await client.head_object(Bucket=bucket, Key=key)
    assert head["Metadata"] == {"bifrost-move-row": "receipt"}
    response = await client.get_object(Bucket=bucket, Key=key)
    assert await response["Body"].read(-1) == b"committed bytes"


@pytest.mark.asyncio
async def test_completed_upload_etag_survives_a_new_client_and_conditions(backend):
    """HEAD/GET after completion retain one opaque token across clients.

    The existing narrow S3 facade returns no completion metadata, so obtain
    the conditional-write token through its supported HEAD contract.
    """
    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "persistent-multipart-etag"
    upload = await client.create_multipart_upload(Bucket=bucket, Key=key)
    part = await client.upload_part(
        Bucket=bucket, Key=key, UploadId=upload["UploadId"], PartNumber=1, Body=b"original",
    )
    await client.complete_multipart_upload(
        Bucket=bucket, Key=key, UploadId=upload["UploadId"],
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
    )
    committed = await client.head_object(Bucket=bucket, Key=key)
    async with storage_transport(settings) as reader:
        head = await reader.head_object(Bucket=bucket, Key=key)
        response = await reader.get_object(Bucket=bucket, Key=key)
        try:
            assert committed["ETag"] == head["ETag"] == response["ETag"]
            assert await response["Body"].read() == b"original"
        finally:
            await response["Body"].close()
        await reader.put_object(Bucket=bucket, Key=key, Body=b"replacement", IfMatch=committed["ETag"])
        with pytest.raises(S3Error) as rejected:
            await reader.put_object(Bucket=bucket, Key=key, Body=b"stale", IfMatch=committed["ETag"])
        assert rejected.value.status_code == 412


@pytest.mark.asyncio
async def test_signed_http_round_trip(backend):
    settings, client, prefix = backend
    params = {"Bucket": settings.storage_bucket, "Key": prefix + "signed+ é"}
    put = await client.generate_presigned_url(
        "put_object", Params={**params, "ContentType": "text/plain"}, ExpiresIn=60
    )
    get = await client.generate_presigned_url(
        "get_object",
        Params={
            **params,
            "ResponseContentDisposition": 'attachment; filename="contract.txt"',
        },
        ExpiresIn=60,
    )
    async with httpx.AsyncClient(trust_env=False) as http:
        response = await http.put(
            put,
            content=b"browser",
            headers=S3StorageClient(settings).presigned_upload_headers("text/plain"),
        )
        response.raise_for_status()
        response = await http.get(get)
        response.raise_for_status()
        assert response.content == b"browser"
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="contract.txt"'
        )


@pytest.mark.asyncio
async def test_streaming_wrapper_and_synchronous_import_reader(backend, monkeypatch):
    settings, client, prefix = backend
    key = prefix + "stream"
    data = b"a" * (3 * 1024**2) + b"b" * (3 * 1024**2)
    parts = []
    original_upload_part = type(client).upload_part

    async def record_part(self, **kwargs):
        result = await original_upload_part(self, **kwargs)
        parts.append((kwargs["PartNumber"], len(kwargs["Body"])))
        return result

    monkeypatch.setattr(type(client), "upload_part", record_part)

    async def chunks():
        yield data[: 3 * 1024**2]
        yield data[3 * 1024**2 :]

    digest, size = await S3StorageClient(settings).put_object_from_chunks(
        key, chunks(), part_size=5 * 1024**2
    )
    assert size == len(data) and digest == hashlib.sha256(data).hexdigest()
    assert parts == [(1, 5 * 1024**2), (2, 1024**2)]
    assert (
        b"".join(
            [
                chunk
                async for chunk in S3StorageClient(settings).iter_object_chunks(
                    key, chunk_size=1024**2
                )
            ]
        )
        == data
    )

    def read_sync():
        if settings.object_storage_provider == "azure_blob":
            reader = SyncAzureBlobTransport(settings)
        else:
            reader = SyncS3Transport(
                endpoint_url=settings.s3_endpoint_url,
                access_key_id=settings.s3_access_key,
                secret_access_key=settings.s3_secret_key,
                region=settings.s3_region,
            )
        try:
            return reader.get_object(Bucket=settings.storage_bucket, Key=key)[
                "Body"
            ].read()
        finally:
            reader.close()

    assert await asyncio.to_thread(read_sync) == data


@pytest.mark.asyncio
async def test_cancelled_stream_aborts_upload_without_replacing_committed_bytes(
    backend, monkeypatch
):
    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "cancelled-stream"
    before = await client.put_object(Bucket=bucket, Key=key, Body=b"committed")
    staged = asyncio.Event()
    created, abort_attempts = [], []
    original_create = type(client).create_multipart_upload
    original_abort = type(client).abort_multipart_upload

    async def create(self, **kwargs):
        result = await original_create(self, **kwargs)
        created.append(result["UploadId"])
        return result

    async def abort(self, **kwargs):
        abort_attempts.append(kwargs["UploadId"])
        return await original_abort(self, **kwargs)

    monkeypatch.setattr(type(client), "create_multipart_upload", create)
    monkeypatch.setattr(type(client), "abort_multipart_upload", abort)

    async def chunks():
        yield b"replacement" * (5 * 1024**2 // 11 + 1)
        # Resumed only after the wrapper has staged its first 5 MiB part.
        staged.set()
        await asyncio.Event().wait()

    storage = S3StorageClient(settings)
    writing = asyncio.create_task(
        storage.put_object_from_chunks(key, chunks(), part_size=5 * 1024**2)
    )
    ready = asyncio.create_task(staged.wait())
    try:
        done, _ = await asyncio.wait(
            {writing, ready}, return_when=asyncio.FIRST_COMPLETED
        )
        if writing in done:
            pytest.fail(f"stream finished before the cancellation checkpoint: {writing.result()}")
        writing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.gather(writing)
        assert len(created) == 1
        assert abort_attempts == created
        assert (await client.head_object(Bucket=bucket, Key=key))["ETag"] == before[
            "ETag"
        ]
        assert (
            await (await client.get_object(Bucket=bucket, Key=key))["Body"].read()
            == b"committed"
        )
    finally:
        writing.cancel()
        ready.cancel()
        await asyncio.gather(writing, ready, return_exceptions=True)
        # The pre-fix failure never attempted abort. Clean that S3 upload
        # explicitly; Blob's owned key teardown also removes its staged blocks.
        if settings.object_storage_provider == "s3" and created and not abort_attempts:
            async with storage.get_client() as cleanup:
                await original_abort(
                    cleanup, Bucket=bucket, Key=key, UploadId=created[0]
                )


@pytest.mark.asyncio
async def test_empty_object_copy_preserves_metadata(backend):
    settings, client, prefix = backend
    bucket, source, destination = (
        settings.storage_bucket,
        prefix + "empty",
        prefix + "copied",
    )
    await client.put_object(
        Bucket=bucket,
        Key=source,
        Body=b"",
        Metadata={"marker": "empty"},
        ContentType="text/plain",
    )
    head = await client.head_object(Bucket=bucket, Key=source)
    await client.copy_object(
        Bucket=bucket,
        Key=destination,
        CopySource={"Bucket": bucket, "Key": source},
        CopySourceIfMatch=head["ETag"],
    )
    copied = await client.head_object(Bucket=bucket, Key=destination)
    assert copied["ContentLength"] == 0 and copied["Metadata"] == {"marker": "empty"}
    response = await client.get_object(Bucket=bucket, Key=destination)
    assert await response["Body"].read() == b""


@pytest.mark.asyncio
async def test_independent_conditional_completions_have_one_winner(backend):
    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "completion-race"
    async with storage_transport(settings) as other:

        async def prepare(transport, content):
            upload = await transport.create_multipart_upload(Bucket=bucket, Key=key)
            part = await transport.upload_part(
                Bucket=bucket,
                Key=key,
                UploadId=upload["UploadId"],
                PartNumber=1,
                Body=content,
            )
            return upload["UploadId"], {
                "Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]
            }

        first, second = await asyncio.gather(
            prepare(client, b"first"), prepare(other, b"second")
        )
        results = await asyncio.gather(
            client.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=first[0],
                MultipartUpload=first[1],
                IfNoneMatch="*",
            ),
            other.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=second[0],
                MultipartUpload=second[1],
                IfNoneMatch="*",
            ),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        assert len(failures) == 1, results
        assert isinstance(failures[0], S3Error) and failures[0].status_code == 412
        response = await client.get_object(Bucket=bucket, Key=key)
        assert await response["Body"].read() == (
            b"second" if isinstance(results[0], BaseException) else b"first"
        )
        for result, transport, upload in zip(
            results, (client, other), (first, second), strict=True
        ):
            if isinstance(result, BaseException):
                await transport.abort_multipart_upload(
                    Bucket=bucket, Key=key, UploadId=upload[0]
                )


@pytest.mark.asyncio
async def test_multipart_completion_checks_current_and_stale_etag(backend):
    settings, client, prefix = backend
    bucket, key = settings.storage_bucket, prefix + "conditional-replacement"
    old = await client.put_object(Bucket=bucket, Key=key, Body=b"old")
    for content, succeeds in ((b"new", True), (b"stale", False)):
        upload = await client.create_multipart_upload(Bucket=bucket, Key=key)
        part = await client.upload_part(
            Bucket=bucket,
            Key=key,
            UploadId=upload["UploadId"],
            PartNumber=1,
            Body=content,
        )
        arguments = dict(
            Bucket=bucket,
            Key=key,
            UploadId=upload["UploadId"],
            MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
            IfMatch=old["ETag"],
        )
        if succeeds:
            await client.complete_multipart_upload(**arguments)
        else:
            try:
                with pytest.raises(S3Error) as raised:
                    await client.complete_multipart_upload(**arguments)
                assert raised.value.status_code == 412
            finally:
                await client.abort_multipart_upload(
                    Bucket=bucket, Key=key, UploadId=upload["UploadId"]
                )
    response = await client.get_object(Bucket=bucket, Key=key)
    assert await response["Body"].read() == b"new"


@pytest.mark.parametrize("backend", ["s3"], indirect=True)
@pytest.mark.asyncio
async def test_s3_nonempty_server_side_copy_preserves_source_version(backend):
    settings, client, prefix = backend
    bucket, source, destination = (
        settings.storage_bucket,
        prefix + "source",
        prefix + "copied",
    )
    await client.put_object(
        Bucket=bucket, Key=source, Body=b"original", Metadata={"marker": "source"}
    )
    head = await client.head_object(Bucket=bucket, Key=source)
    await client.copy_object(
        Bucket=bucket,
        Key=destination,
        CopySource={"Bucket": bucket, "Key": source},
        CopySourceIfMatch=head["ETag"],
    )
    response = await client.get_object(Bucket=bucket, Key=destination)
    assert await response["Body"].read() == b"original"
    assert (await client.head_object(Bucket=bucket, Key=destination))["Metadata"] == {
        "marker": "source"
    }
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
async def test_app_preview_is_stored_in_selected_transport(backend, monkeypatch):

    settings, client, _ = backend
    app_id = str(uuid4())
    if settings.object_storage_provider == "azure_blob":
        assert not settings.s3_configured

        def forbidden_s3(**_kwargs):
            raise AssertionError("Blob App storage attempted to construct S3 transport")

        monkeypatch.setattr("src.services.app_storage.S3Transport", forbidden_s3)
    storage = AppStorageService(settings)
    key = f"_apps/{app_id}/preview/bundle.js"
    try:
        await storage.write_preview_file(app_id, "bundle.js", b"export default 1")
        response = await client.get_object(Bucket=settings.storage_bucket, Key=key)
        assert await response["Body"].read() == b"export default 1"
    finally:
        await client.delete_object(Bucket=settings.storage_bucket, Key=key)


@pytest.mark.parametrize("backend", ["s3"], indirect=True)
@pytest.mark.asyncio
async def test_s3_app_publish_objects_are_in_selected_transport(backend):
    # Direct storage service: zero builds, platform jobs, or worker executions.
    settings, client, _ = backend
    app_id = str(uuid4())
    storage = AppStorageService(settings)
    manifest = json.dumps({"entry": "bundle.js", "outputs": ["bundle.js"]}).encode()
    try:
        await storage.write_preview_file(app_id, "bundle.js", b"export default 1")
        await storage.write_preview_file(app_id, "manifest.json", manifest)
        assert await storage.publish(app_id) == 2
        for name, expected in (
            ("bundle.js", b"export default 1"),
            ("manifest.json", manifest),
        ):
            response = await client.get_object(
                Bucket=settings.storage_bucket, Key=f"_apps/{app_id}/live/{name}"
            )
            assert await response["Body"].read() == expected
    finally:
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.storage_bucket, Prefix=f"_apps/{app_id}/"
        ):
            for item in page.get("Contents", []):
                await client.delete_object(
                    Bucket=settings.storage_bucket, Key=item["Key"]
                )


@pytest.mark.asyncio
async def test_repo_and_solution_storage_use_selected_backend(backend):
    settings, client, prefix = backend
    repo, solution = RepoStorage(settings), SolutionStorage(uuid4(), settings)
    path = prefix + "source.py"
    try:
        await repo.write(path, b"repo")
        await solution.write(path, b"solution")
        assert await repo.read(path) == b"repo"
        assert await solution.read(path) == b"solution"
        assert (
            await client.get_object(Bucket=settings.storage_bucket, Key="_repo/" + path)
        )["Body"]
    finally:
        await repo.delete(path)
        await solution.delete(path)
