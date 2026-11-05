"""Synthetic boundary tests supplement, but never replace, real service contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from src.config import Settings
from src.core.azure_blob_transport import AzureBlobBody, AzureBlobTransport
from src.core.s3_transport import S3Error, S3NoSuchKey
from src.core.storage_transport import storage_transport


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "object_storage_provider": "azure_blob",
            "azure_blob_account_url": "https://example.blob.core.windows.net",
            "azure_blob_container": "contract",
            "azure_blob_auth": "account_key",
            "azure_blob_account_key": SecretStr("local-fixture"),
            **overrides,
        },
    )


@pytest.fixture
def transport():
    client = AzureBlobTransport(settings())
    client._operation_owned = True
    client._container_client = MagicMock()
    client._service_client = SimpleNamespace(close=AsyncMock())
    return client


def test_s3_remains_default_and_azure_is_lazy():
    factory = MagicMock()
    config = Settings(_env_file=None)
    storage_transport(config, s3_factory=factory)
    factory.assert_called_once_with(
        endpoint_url=config.s3_endpoint_url,
        access_key_id=config.s3_access_key,
        secret_access_key=config.s3_secret_key,
        region=config.s3_region,
    )
    assert isinstance(storage_transport(settings()), AzureBlobTransport)


def test_managed_identity_never_accepts_an_explicit_key():
    assert not settings(azure_blob_auth="managed_identity").azure_blob_configured
    assert settings(
        azure_blob_auth="managed_identity", azure_blob_account_key=None
    ).azure_blob_configured


def test_blob_settings_do_not_serialize_keys_or_accept_url_credentials():
    from pydantic import ValidationError

    assert "azure_blob_account_key" not in settings().model_dump()
    with pytest.raises(ValidationError) as raised:
        settings(
            azure_blob_account_url="https://example.blob.core.windows.net?sig=secret"
        )
    assert "sig=secret" not in str(raised.value)


def test_parent_import_fallback_uses_blob_container_and_native_sync_reader(monkeypatch):
    from io import BytesIO
    from src.core import module_cache_sync

    fake = SimpleNamespace(
        get_object=MagicMock(return_value={"Body": BytesIO(b"source")})
    )
    constructor = MagicMock(return_value=fake)
    monkeypatch.setattr("src.core.azure_blob_transport.SyncAzureBlobTransport", constructor)
    monkeypatch.setattr("src.config.get_settings", settings)
    monkeypatch.setattr(module_cache_sync, "_s3_client", None)
    monkeypatch.setattr(module_cache_sync, "_s3_available", None)
    monkeypatch.setenv("BIFROST_OBJECT_STORAGE_PROVIDER", "azure_blob")
    monkeypatch.setenv("BIFROST_AZURE_BLOB_CONTAINER", "contract")
    monkeypatch.delenv("BIFROST_S3_BUCKET", raising=False)
    assert module_cache_sync._get_s3_module("modules/example.py") == b"source"
    constructor.assert_called_once()
    fake.get_object.assert_called_once_with(
        Bucket="contract", Key="_repo/modules/example.py"
    )


@pytest.mark.asyncio
async def test_blob_git_bulk_sync_stops_before_aws_subprocess(monkeypatch):
    from src.services.git_repo_manager import GitRepoManager

    create_process = AsyncMock()
    monkeypatch.setattr("src.services.git_repo_manager.asyncio.create_subprocess_exec", create_process)
    with pytest.raises(RuntimeError, match="AWS CLI"):
        await GitRepoManager(settings())._run_aws_cli(["aws", "s3", "sync"])
    create_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_blob_git_sync_invalidates_local_baseline(tmp_path, monkeypatch):
    from src.services.git_repo_manager import GitRepoManager, STORAGE_BASELINE_FILE

    (tmp_path / ".git").mkdir()
    baseline = tmp_path / ".git" / STORAGE_BASELINE_FILE
    baseline.write_text("retain")
    from src.services.blob_workspace_mirror import BlobWorkspaceMirror

    monkeypatch.setattr(
        BlobWorkspaceMirror, "upload", AsyncMock(side_effect=ConnectionError("offline"))
    )
    with pytest.raises(ConnectionError, match="offline"):
        await GitRepoManager(settings()).sync_up(tmp_path)
    assert not baseline.exists()


@pytest.mark.asyncio
async def test_invalid_checkpoint_is_rejected_before_workspace_storage_read(
    tmp_path, monkeypatch
):
    from src.services.git_repo_manager import GitRepoManager

    manager = GitRepoManager(settings())
    fingerprint = AsyncMock()
    monkeypatch.setattr(manager, "_storage_fingerprint", fingerprint)
    with pytest.raises(ValueError, match="Invalid workspace checkpoint ID"):
        await manager.restore_workspace_checkpoint("../escape", tmp_path)
    fingerprint.assert_not_awaited()


def test_move_marker_metadata_round_trips_without_azure_name_loss():
    values = {
        "bifrost-move-row": "row",
        "UpperCase": "é",
        "bifrost_metadata": "user value",
    }
    properties = SimpleNamespace(
        size=0,
        etag='"version"',
        last_modified=None,
        metadata=AzureBlobTransport._metadata(values),
        content_settings=SimpleNamespace(),
    )
    assert AzureBlobTransport._properties(properties)["Metadata"] == {
        key.lower(): value for key, value in values.items()
    }


@pytest.mark.asyncio
async def test_put_forwards_both_conditional_write_forms(transport):
    from azure.core import MatchConditions

    blob = transport._container_client.get_blob_client.return_value
    blob.upload_blob = AsyncMock(return_value={"etag": '"new"'})
    await transport.put_object(
        Bucket="contract", Key="key", Body=b"data", IfMatch='"old"'
    )
    assert blob.upload_blob.call_args.kwargs["etag"] == '"old"'
    assert (
        blob.upload_blob.call_args.kwargs["match_condition"]
        == MatchConditions.IfNotModified
    )
    await transport.put_object(
        Bucket="contract", Key="key", Body=b"data", IfNoneMatch="*"
    )
    assert (
        blob.upload_blob.call_args.kwargs["match_condition"]
        == MatchConditions.IfMissing
    )


@pytest.mark.parametrize("etag", [None, '"old-version"'])
@pytest.mark.asyncio
async def test_delete_does_not_replay_after_an_ambiguous_response(etag):
    from azure.core.credentials import AzureNamedKeyCredential
    from azure.core.exceptions import ServiceResponseError
    from azure.core.pipeline.transport import AsyncHttpTransport
    from azure.storage.blob.aio import BlobServiceClient, ExponentialRetry

    class LostResponse(AsyncHttpTransport):
        attempts = 0

        async def open(self):
            pass

        async def close(self):
            pass

        async def __aexit__(self, *_args):
            await self.close()

        async def send(self, request, **kwargs):
            assert request.method == "DELETE"
            assert request.headers.get("If-Match") == etag
            self.attempts += 1
            # The server may have deleted the old version before losing its
            # response. A replay must not target a subsequent replacement.
            raise ServiceResponseError("response lost after delete")

    wire = LostResponse()
    client = AzureBlobTransport(settings())
    client._service_client = BlobServiceClient(
        client.settings.azure_blob_account_url,
        credential=AzureNamedKeyCredential(
            "example", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
        ),
        transport=wire,
        retry_policy=ExponentialRetry(
            initial_backoff=0, increment_base=0, random_jitter_range=0, retry_total=1
        ),
    )
    client._container_client = client._service_client.get_container_client("contract")
    async with client:
        with pytest.raises(ConnectionError, match="Azure Blob transport failed"):
            await client.delete_object(Bucket="contract", Key="key", IfMatch=etag)
    assert wire.attempts == 1


@pytest.mark.asyncio
async def test_explicitly_rejects_expires_and_unknown_options_before_write(transport):
    for kwargs in (
        {"Expires": "Wed, 21 Oct 2030 07:28:00 GMT"},
        {"Unexpected": "ignored?"},
    ):
        with pytest.raises(ValueError):
            await transport.put_object(
                Bucket="contract", Key="key", Body=b"data", **kwargs
            )
    transport._container_client.get_blob_client.return_value.upload_blob.assert_not_called()


@pytest.mark.asyncio
async def test_missing_blob_and_missing_container_are_distinct(transport):
    from azure.core.exceptions import ResourceNotFoundError

    blob = transport._container_client.get_blob_client.return_value
    missing = ResourceNotFoundError(message="private URL")
    missing.error_code = "BlobNotFound"
    missing.status_code = 404
    blob.get_blob_properties = AsyncMock(side_effect=missing)
    with pytest.raises(S3NoSuchKey):
        await transport.head_object(Bucket="contract", Key="key")
    missing_container = ResourceNotFoundError(message="private URL")
    missing_container.error_code = "ContainerNotFound"
    missing_container.status_code = 404
    blob.get_blob_properties.side_effect = missing_container
    with pytest.raises(S3Error) as raised:
        await transport.head_object(Bucket="contract", Key="key")
    assert not isinstance(raised.value, S3NoSuchKey)
    assert "private URL" not in str(raised.value)
    assert raised.value.__suppress_context__


@pytest.mark.asyncio
async def test_authorization_denial_is_storage_unavailable_not_missing(transport):
    from azure.core.exceptions import HttpResponseError
    from src.services.file_storage.s3_client import is_storage_unavailable

    denied = HttpResponseError(message="private URL")
    denied.error_code = "AuthorizationPermissionMismatch"
    denied.status_code = 403
    blob = transport._container_client.get_blob_client.return_value
    blob.get_blob_properties = AsyncMock(side_effect=denied)
    with pytest.raises(S3Error) as raised:
        await transport.head_object(Bucket="contract", Key="key")
    assert raised.value.code == "AccessDenied"
    assert raised.value.status_code == 403
    assert is_storage_unavailable(raised.value)
    assert not isinstance(raised.value, S3NoSuchKey)


@pytest.mark.asyncio
async def test_body_reads_incrementally_and_close_stops_consumption():
    seen = []

    async def chunks():
        for chunk in (b"abc", b"def", b"ghi"):
            seen.append(chunk)
            yield chunk

    body = AzureBlobBody(SimpleNamespace(chunks=chunks), size=9)
    assert await body.read(2) == b"ab"
    assert seen == [b"abc"]
    assert await body.read(3) == b"cde"
    await body.close()
    assert await body.read() == b""
    assert seen == [b"abc", b"def"]


@pytest.mark.asyncio
async def test_raw_download_refuses_corruption_and_truncation():
    import hashlib

    async def chunks():
        yield b"corrupt"

    body = AzureBlobBody(
        SimpleNamespace(chunks=chunks),
        size=7,
        content_md5=hashlib.md5(b"healthy", usedforsecurity=False).digest(),
    )
    with pytest.raises(S3Error, match="MD5 mismatch"):
        await body.read()
    body = AzureBlobBody(SimpleNamespace(chunks=chunks), size=8)
    with pytest.raises(S3Error, match="length mismatch"):
        await body.read()


@pytest.mark.asyncio
async def test_close_releases_credential_even_when_service_close_fails(transport):
    credential = SimpleNamespace(close=AsyncMock())
    transport._credential = credential
    transport._service_client.close.side_effect = RuntimeError("close")
    with pytest.raises(RuntimeError):
        await transport.close()
    credential.close.assert_awaited_once()
    assert transport._service_client is None


@pytest.mark.asyncio
async def test_block_copy_binds_range_and_source_etag(transport):
    blob = transport._container_client.get_blob_client.return_value
    blob.stage_block_from_url = AsyncMock()
    transport.generate_presigned_url = AsyncMock(return_value="https://fixture/source")
    upload = await transport.create_multipart_upload(Bucket="contract", Key="dest")
    await transport.upload_part_copy(
        Bucket="contract",
        Key="dest",
        UploadId=upload["UploadId"],
        PartNumber=1,
        CopySource={"Bucket": "contract", "Key": "source"},
        CopySourceIfMatch='"snapshot"',
        CopySourceRange="bytes=7-12",
    )
    assert blob.stage_block_from_url.call_args.kwargs == {
        "source_offset": 7,
        "source_length": 6,
        "headers": {"x-ms-source-if-match": '"snapshot"'},
    }


@pytest.mark.asyncio
async def test_copy_preserves_metadata_and_pins_every_block(transport):
    size = 300 * 1024**2
    transport.head_object = AsyncMock(
        return_value={
            "ContentLength": size,
            "ETag": '"source"',
            "Metadata": {"bifrost-move-row": "row"},
            "ContentType": "text/plain",
            "CacheControl": "no-store",
        }
    )
    transport.create_multipart_upload = AsyncMock(return_value={"UploadId": "upload"})
    transport.upload_part_copy = AsyncMock(
        return_value={"CopyPartResult": {"ETag": "part"}}
    )
    transport.complete_multipart_upload = AsyncMock(
        return_value={"ETag": '"destination"'}
    )
    await transport.copy_object(
        Bucket="contract",
        Key="dest",
        CopySource={"Bucket": "contract", "Key": "source"},
    )
    assert transport.create_multipart_upload.call_args.kwargs["Metadata"] == {
        "bifrost-move-row": "row"
    }
    assert (
        transport.create_multipart_upload.call_args.kwargs["CacheControl"] == "no-store"
    )
    calls = transport.upload_part_copy.call_args_list
    assert len(calls) == 2
    assert all(call.kwargs["CopySourceIfMatch"] == '"source"' for call in calls)
    assert calls[0].kwargs["CopySourceRange"] == f"bytes=0-{256 * 1024**2 - 1}"
    assert calls[1].kwargs["CopySourceRange"] == f"bytes={256 * 1024**2}-{size - 1}"


@pytest.mark.asyncio
async def test_completed_upload_cannot_be_completed_twice(transport):
    import asyncio

    blob = transport._container_client.get_blob_client.return_value
    blob.stage_block = AsyncMock()
    blob.commit_block_list = AsyncMock(return_value={"etag": '"done"'})
    upload = (await transport.create_multipart_upload(Bucket="contract", Key="dest"))[
        "UploadId"
    ]
    part = await transport.upload_part(
        Bucket="contract", Key="dest", UploadId=upload, PartNumber=1, Body=b"part"
    )

    async def complete():
        return await transport.complete_multipart_upload(
            Bucket="contract",
            Key="dest",
            UploadId=upload,
            MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
            IfNoneMatch="*",
        )

    results = await asyncio.gather(complete(), complete(), return_exceptions=True)
    assert (
        sum(
            isinstance(result, S3Error) and result.code == "NoSuchUpload"
            for result in results
        )
        == 1
    )
    blob.commit_block_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_abort_does_not_delete_committed_destination(transport):
    upload = (await transport.create_multipart_upload(Bucket="contract", Key="dest"))[
        "UploadId"
    ]
    await transport.abort_multipart_upload(
        Bucket="contract", Key="dest", UploadId=upload
    )
    transport._container_client.delete_blob.assert_not_called()


@pytest.mark.asyncio
async def test_sas_accepts_seven_days_and_rejects_larger_lifetime(transport):
    transport._generate_blob_sas = AsyncMock(return_value="fixture-sas")
    transport._container_client.get_blob_client.return_value.url = (
        "https://example.blob.core.windows.net/contract/key"
    )
    url = await transport.generate_presigned_url(
        "get_object", Params={"Bucket": "contract", "Key": "key"}, ExpiresIn=604800
    )
    assert url.endswith("?fixture-sas")
    assert transport._generate_blob_sas.call_args.kwargs["expires_in"] == 604800
    with pytest.raises(ValueError, match="604800"):
        await transport.generate_presigned_url(
            "get_object", Params={"Bucket": "contract", "Key": "key"}, ExpiresIn=604801
        )
