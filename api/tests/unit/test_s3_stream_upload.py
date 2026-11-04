"""Multipart streams require a valid provider upload ID before sending parts."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.file_storage.s3_client import S3StorageClient
from src.config import Settings


def storage_with_response(response, monkeypatch):
    client = SimpleNamespace(
        create_multipart_upload=AsyncMock(return_value=response),
        upload_part=AsyncMock(return_value={"ETag": "part-etag"}),
        complete_multipart_upload=AsyncMock(),
        abort_multipart_upload=AsyncMock(),
        put_object=AsyncMock(),
    )
    storage = S3StorageClient(Settings(_env_file=None, s3_bucket="test-bucket"))

    @asynccontextmanager
    async def get_client():
        yield client

    monkeypatch.setattr(storage, "get_client", get_client)
    return storage, client


async def chunks():
    yield b"abcdefghij"


@pytest.mark.parametrize("response", [{}, {"UploadId": None}, {"UploadId": ""}, {"UploadId": 5}])
async def test_missing_upload_id_is_rejected_before_uploading_parts(monkeypatch, response):
    storage, client = storage_with_response(response, monkeypatch)
    with pytest.raises(ValueError, match="upload ID"):
        await storage.put_object_from_chunks("backup.zip", chunks(), part_size=4)
    client.upload_part.assert_not_awaited()
    client.complete_multipart_upload.assert_not_awaited()
    client.put_object.assert_not_awaited()


async def test_valid_multipart_upload_preserves_bytes_and_digest(monkeypatch):
    storage, client = storage_with_response({"UploadId": "upload-1"}, monkeypatch)
    digest, size = await storage.put_object_from_chunks("backup.zip", chunks(), part_size=4)
    assert (digest, size) == (hashlib.sha256(b"abcdefghij").hexdigest(), 10)
    uploaded_parts = client.upload_part.await_args_list
    assert [call.kwargs["Body"] for call in uploaded_parts] == [b"abcd", b"efgh", b"ij"]
    assert all(call.kwargs["UploadId"] == "upload-1" for call in uploaded_parts)
    client.complete_multipart_upload.assert_awaited_once()
    client.abort_multipart_upload.assert_not_awaited()


@pytest.mark.parametrize("failure", [asyncio.CancelledError(), ValueError("input failed")])
async def test_abort_failure_preserves_original_stream_error(monkeypatch, failure, caplog):
    storage, client = storage_with_response({"UploadId": "upload-1"}, monkeypatch)
    client.abort_multipart_upload.side_effect = ConnectionError("abort unavailable")

    async def interrupted():
        yield b"part"
        raise failure

    with pytest.raises(type(failure)) as raised:
        await storage.put_object_from_chunks("backup.zip", interrupted(), part_size=4)
    assert raised.value is failure
    client.abort_multipart_upload.assert_awaited_once_with(
        Bucket="test-bucket", Key="backup.zip", UploadId="upload-1"
    )
    client.complete_multipart_upload.assert_not_awaited()
    assert "Could not abort multipart upload" in caplog.text
