"""S3 primitives used by files.move: head, conditional copy, strict delete, listing."""

from contextlib import asynccontextmanager
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx

from src.core.s3_transport import S3Error

from src.services.file_storage.s3_client import (
    COPY_OBJECT_LIMIT,
    ObjectHead,
    ObjectMissing,
    ObjectPreconditionFailed,
    S3StorageClient,
    is_storage_unavailable,
)


def client_error(code: str, status: int, operation: str = "Op") -> S3Error:
    del operation
    return S3Error(code, status, code)


def storage_with(monkeypatch, **methods):
    client = SimpleNamespace(**methods)
    storage = S3StorageClient(SimpleNamespace(s3_bucket="bucket", s3_multipart_copy_mode="server"))

    @asynccontextmanager
    async def get_client():
        yield client

    monkeypatch.setattr(storage, "get_client", get_client)
    return storage, client


SOURCE = ObjectHead(
    size=10,
    etag='"abc"',
    metadata={"owner": "x"},
    headers={"ContentType": "application/pdf", "ContentDisposition": "inline"},
)


def streaming_client(monkeypatch, *, change_after_first=False, bad_etag=False,
                     bad_range=False, truncated=False, complete_error=None,
                     read_error=None, close_error=None):
    bodies = []

    async def get_object(**kwargs):
        start, end = map(int, kwargs["Range"].removeprefix("bytes=").split("-"))
        if change_after_first and bodies:
            raise client_error("PreconditionFailed", 412)
        data = b"abcdefghij"[start:end + 1]
        body = SimpleNamespace(
            read=AsyncMock(return_value=data[:-1] if truncated else data, side_effect=read_error),
            close=AsyncMock(side_effect=close_error),
        )
        bodies.append(body)
        assert kwargs["IfMatch"] == SOURCE.etag
        return {"Body": body, "ETag": '"changed"' if bad_etag else SOURCE.etag,
                "ContentLength": len(data), "ContentRange": f"bytes {start}-{end}/10",
                "ResponseMetadata": {"HTTPStatusCode": 200 if bad_range else 206}}

    storage, client = storage_with(
        monkeypatch, get_object=AsyncMock(side_effect=get_object),
        create_multipart_upload=AsyncMock(return_value={"UploadId": "u1"}),
        upload_part=AsyncMock(return_value={"ETag": '"part"'}),
        upload_part_copy=AsyncMock(),
        complete_multipart_upload=AsyncMock(side_effect=complete_error),
        abort_multipart_upload=AsyncMock(),
    )
    storage.settings.s3_multipart_copy_mode = "stream"
    return storage, client, bodies


async def test_streaming_copy_pins_ranges_and_keeps_atomic_completion(monkeypatch):
    storage, client, bodies = streaming_client(monkeypatch)
    await storage.copy_object("src", "dst", source=SOURCE, metadata={"owner": "new"},
                              multipart_threshold=1, part_size=4)
    assert b"".join(c.kwargs["Body"] for c in client.upload_part.await_args_list) == b"abcdefghij"
    assert [c.kwargs["Range"] for c in client.get_object.await_args_list] == [
        "bytes=0-3", "bytes=4-7", "bytes=8-9",
    ]
    assert client.complete_multipart_upload.await_args.kwargs["IfNoneMatch"] == "*"
    assert client.create_multipart_upload.await_args.kwargs["Metadata"] == {"owner": "new"}
    assert client.create_multipart_upload.await_args.kwargs["ContentType"] == "application/pdf"
    assert all(body.close.await_count == 1 for body in bodies)
    client.upload_part_copy.assert_not_awaited()
    client.abort_multipart_upload.assert_not_awaited()


@pytest.mark.parametrize("fault", ["change_after_first", "bad_etag", "bad_range", "truncated"])
async def test_streaming_copy_aborts_instead_of_publishing_mixed_or_partial_bytes(monkeypatch, fault):
    storage, client, bodies = streaming_client(monkeypatch, **{fault: True})
    expected = ObjectPreconditionFailed if fault in {"change_after_first", "bad_etag"} else ValueError
    with pytest.raises(expected):
        await storage.copy_object("src", "dst", source=SOURCE, metadata={},
                                  multipart_threshold=1, part_size=4)
    client.complete_multipart_upload.assert_not_awaited()
    client.abort_multipart_upload.assert_awaited_once()
    assert all(body.close.await_count == 1 for body in bodies)


async def test_streaming_copy_preserves_destination_conflict(monkeypatch):
    storage, client, _ = streaming_client(
        monkeypatch, complete_error=client_error("PreconditionFailed", 412)
    )
    with pytest.raises(ObjectPreconditionFailed) as caught:
        await storage.copy_object("src", "dst", source=SOURCE, metadata={},
                                  multipart_threshold=1, part_size=4)
    assert caught.value.side == "destination"
    client.abort_multipart_upload.assert_awaited_once()


@pytest.mark.parametrize("close_error", [None, OSError("synthetic close failure")])
async def test_cancelled_stream_copy_aborts_and_preserves_cancellation(monkeypatch, close_error):
    storage, client, bodies = streaming_client(
        monkeypatch, read_error=asyncio.CancelledError(), close_error=close_error,
    )
    with pytest.raises(asyncio.CancelledError):
        await storage.copy_object("src", "dst", source=SOURCE, metadata={},
                                  multipart_threshold=1, part_size=4)
    client.complete_multipart_upload.assert_not_awaited()
    client.abort_multipart_upload.assert_awaited_once()
    assert all(body.close.await_count == 1 for body in bodies)


async def test_head_object_returns_none_only_for_not_found(monkeypatch) -> None:
    for code in ("404", "NoSuchKey", "NotFound"):
        storage, _ = storage_with(
            monkeypatch, head_object=AsyncMock(side_effect=client_error(code, 404, "HeadObject"))
        )
        assert await storage.head_object("k") is None
    storage, _ = storage_with(
        monkeypatch, head_object=AsyncMock(side_effect=client_error("403", 403, "HeadObject"))
    )
    with pytest.raises(S3Error):
        await storage.head_object("k")


async def test_head_object_maps_response(monkeypatch) -> None:
    storage, _ = storage_with(
        monkeypatch,
        head_object=AsyncMock(
            return_value={
                "ContentLength": 10,
                "ETag": '"abc"',
                "Metadata": {"owner": "x"},
                "ContentType": "application/pdf",
                "CacheControl": None,
            }
        ),
    )
    head = await storage.head_object("k")
    assert head == ObjectHead(
        size=10, etag='"abc"', metadata={"owner": "x"}, headers={"ContentType": "application/pdf"}
    )


@pytest.mark.parametrize("copy_mode", ["server", "stream"])
async def test_single_part_copy_replaces_metadata_and_guards_source(monkeypatch, copy_mode) -> None:
    storage, client = storage_with(monkeypatch, copy_object=AsyncMock())
    storage.settings.s3_multipart_copy_mode = copy_mode
    await storage.copy_object("src", "dst", source=SOURCE, metadata={"m": "1"})
    client.copy_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="dst",
        CopySource={"Bucket": "bucket", "Key": "src"},
        CopySourceIfMatch='"abc"',
        MetadataDirective="REPLACE",
        Metadata={"m": "1"},
        ContentType="application/pdf",
        ContentDisposition="inline",
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (client_error("PreconditionFailed", 412), ObjectPreconditionFailed),
        (client_error("NoSuchKey", 404), ObjectMissing),
    ],
)
async def test_single_part_copy_maps_source_errors(monkeypatch, error, expected) -> None:
    storage, _ = storage_with(monkeypatch, copy_object=AsyncMock(side_effect=error))
    with pytest.raises(expected) as caught:
        await storage.copy_object("src", "dst", source=SOURCE, metadata={})
    if expected is ObjectPreconditionFailed:
        assert caught.value.side == "source"


def multipart_client(monkeypatch, *, complete_error=None, part_error=None):
    return storage_with(
        monkeypatch,
        create_multipart_upload=AsyncMock(return_value={"UploadId": "u1"}),
        upload_part_copy=AsyncMock(
            side_effect=part_error,
            return_value={"CopyPartResult": {"ETag": '"p"'}},
        ),
        complete_multipart_upload=AsyncMock(side_effect=complete_error),
        abort_multipart_upload=AsyncMock(),
    )


async def test_multipart_copy_ranges_parts_and_refuses_existing_destination(monkeypatch) -> None:
    storage, client = multipart_client(monkeypatch)
    await storage.copy_object(
        "src", "dst", source=SOURCE, metadata={"m": "1"}, multipart_threshold=4, part_size=4
    )
    client.create_multipart_upload.assert_awaited_once_with(
        Bucket="bucket", Key="dst", Metadata={"m": "1"},
        ContentType="application/pdf", ContentDisposition="inline",
    )
    ranges = [c.kwargs["CopySourceRange"] for c in client.upload_part_copy.await_args_list]
    assert ranges == ["bytes=0-3", "bytes=4-7", "bytes=8-9"]
    assert all(c.kwargs["CopySourceIfMatch"] == '"abc"' for c in client.upload_part_copy.await_args_list)
    complete = client.complete_multipart_upload.await_args.kwargs
    assert complete["IfNoneMatch"] == "*"
    assert [p["PartNumber"] for p in complete["MultipartUpload"]["Parts"]] == [1, 2, 3]
    client.abort_multipart_upload.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [client_error("PreconditionFailed", 412), client_error("ConditionalRequestConflict", 409)],
)
async def test_multipart_complete_conflict_is_destination_precondition(monkeypatch, error) -> None:
    storage, client = multipart_client(monkeypatch, complete_error=error)
    with pytest.raises(ObjectPreconditionFailed) as caught:
        await storage.copy_object("src", "dst", source=SOURCE, metadata={}, multipart_threshold=4, part_size=4)
    assert caught.value.side == "destination"
    client.abort_multipart_upload.assert_awaited_once()


@pytest.mark.parametrize(("size", "multipart"), [(COPY_OBJECT_LIMIT, False), (COPY_OBJECT_LIMIT + 1, True)])
async def test_default_threshold_is_the_5_gib_copy_limit(monkeypatch, size, multipart) -> None:
    storage, client = multipart_client(monkeypatch)
    client.copy_object = AsyncMock()
    head = ObjectHead(size=size, etag='"abc"', metadata={}, headers={})
    await storage.copy_object("src", "dst", source=head, metadata={})
    assert client.create_multipart_upload.await_count == int(multipart)
    assert client.copy_object.await_count == int(not multipart)


async def test_multipart_part_failure_aborts(monkeypatch) -> None:
    storage, client = multipart_client(monkeypatch, part_error=client_error("NoSuchKey", 404))
    with pytest.raises(ObjectMissing):
        await storage.copy_object("src", "dst", source=SOURCE, metadata={}, multipart_threshold=4, part_size=4)
    client.abort_multipart_upload.assert_awaited_once()
    client.complete_multipart_upload.assert_not_awaited()


async def test_default_part_size_respects_part_limits(monkeypatch) -> None:
    storage, client = multipart_client(monkeypatch)
    huge = ObjectHead(size=6 * 1024**4, etag='"e"', metadata={}, headers={})  # 6 TiB
    await storage.copy_object("src", "dst", source=huge, metadata={})
    assert client.upload_part_copy.await_count <= 10_000
    first = client.upload_part_copy.await_args_list[0].kwargs["CopySourceRange"]
    start, end = (int(x) for x in first.removeprefix("bytes=").split("-"))
    assert end - start + 1 >= 256 * 1024**2


async def test_delete_object_strict_raises(monkeypatch) -> None:
    storage, _ = storage_with(monkeypatch, delete_object=AsyncMock(side_effect=client_error("InternalError", 500)))
    with pytest.raises(S3Error):
        await storage.delete_object_strict("k")


async def test_list_keys_follows_continuation(monkeypatch) -> None:
    pages = [
        {"Contents": [{"Key": "a"}], "IsTruncated": True, "NextContinuationToken": "t"},
        {"Contents": [{"Key": "b"}], "IsTruncated": False},
    ]
    storage, client = storage_with(monkeypatch, list_objects_v2=AsyncMock(side_effect=pages))
    assert await storage.list_keys("p/") == ["a", "b"]
    second = client.list_objects_v2.await_args_list[1].kwargs
    assert second["ContinuationToken"] == "t" and second["Delimiter"] == "/"


def test_storage_unavailable_classification() -> None:
    assert is_storage_unavailable(httpx.ConnectError("connection failed"))
    assert is_storage_unavailable(client_error("AccessDenied", 403))
    assert is_storage_unavailable(client_error("403", 403))
    assert is_storage_unavailable(client_error("Forbidden", 403))
    assert is_storage_unavailable(client_error("InternalError", 503))
    assert not is_storage_unavailable(client_error("NoSuchKey", 404))
    assert not is_storage_unavailable(ValueError("x"))
