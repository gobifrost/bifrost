"""Multipart streams require a valid provider upload ID before sending parts."""

import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.file_storage.object_storage import FileObjectStorage
from src.core.storage.s3 import S3ObjectStore
from src.core.storage import s3_transfers
from src.core.storage.contracts import StorageError


def storage_with_response(response, monkeypatch):
    client = SimpleNamespace(
        create_multipart_upload=AsyncMock(return_value=response),
        upload_part=AsyncMock(return_value={"ETag": "part-etag"}),
        complete_multipart_upload=AsyncMock(return_value={"ETag": '"committed"'}),
        abort_multipart_upload=AsyncMock(),
        put_object=AsyncMock(),
    )
    storage = FileObjectStorage(SimpleNamespace(s3_bucket="test-bucket"))

    @asynccontextmanager
    async def get_store():
        yield S3ObjectStore("test-bucket", client)

    monkeypatch.setattr(storage, "get_store", get_store)
    monkeypatch.setattr(s3_transfers, "STREAM_PART_SIZE", 4)
    return storage, client


async def chunks():
    yield b"abcdefghij"


@pytest.mark.parametrize("response", [{}, {"UploadId": None}, {"UploadId": ""}, {"UploadId": 5}])
async def test_missing_upload_id_is_rejected_before_uploading_parts(monkeypatch, response):
    storage, client = storage_with_response(response, monkeypatch)
    with pytest.raises(StorageError) as caught:
        await storage.put_object_from_chunks("backup.zip", chunks())
    assert caught.value.provider_code == "MissingMultipartIdentity"
    client.upload_part.assert_not_awaited()
    client.complete_multipart_upload.assert_not_awaited()
    client.put_object.assert_not_awaited()


async def test_valid_multipart_upload_preserves_bytes_and_digest(monkeypatch):
    storage, client = storage_with_response({"UploadId": "upload-1"}, monkeypatch)
    digest, size = await storage.put_object_from_chunks("backup.zip", chunks())
    assert (digest, size) == (hashlib.sha256(b"abcdefghij").hexdigest(), 10)
    uploaded_parts = client.upload_part.await_args_list
    assert [call.kwargs["Body"] for call in uploaded_parts] == [b"abcd", b"efgh", b"ij"]
    assert all(call.kwargs["UploadId"] == "upload-1" for call in uploaded_parts)
    client.complete_multipart_upload.assert_awaited_once()
    client.abort_multipart_upload.assert_not_awaited()
