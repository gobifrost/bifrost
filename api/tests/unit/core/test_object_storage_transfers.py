"""Multipart ownership and concurrency guarantees at the provider boundary."""
import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.storage import optional_read
from src.core.storage import s3_transfers as transfers
from src.core.storage.contracts import (
    ContentProperties, CopyConditionFailed, CopyOptions, ObjectInfo, ObjectVersion,
    StorageError, WriteOptions,
)
from src.core.s3_transport import S3Error


def transport():
    return SimpleNamespace(
        put_object=AsyncMock(return_value={"ETag": '"committed"'}),
        create_multipart_upload=AsyncMock(return_value={"UploadId": "owned"}),
        upload_part=AsyncMock(return_value={"ETag": '"part"'}),
        upload_part_copy=AsyncMock(return_value={"CopyPartResult": {"ETag": '"part"'}}),
        complete_multipart_upload=AsyncMock(return_value={"ETag": '"committed"'}),
        abort_multipart_upload=AsyncMock(),
    )


async def chunks(*values):
    for value in values:
        yield value


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"small"])
async def test_small_stream_is_one_conditional_write_with_digest(content):
    s3 = transport()
    result = await transfers.write_stream(s3, "bucket", "key", chunks(content), WriteOptions(create_only=True))
    assert result.version == "committed"
    assert result.size == len(content) and result.sha256 == hashlib.sha256(content).hexdigest()
    assert s3.put_object.call_args.kwargs["Body"] == content
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    s3.create_multipart_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_bounds_parts_and_checks_destination_only_at_commit():
    s3 = transport()
    content = b"a" * transfers.STREAM_PART_SIZE + b"tail"
    result = await transfers.write_stream(s3, "bucket", "key", chunks(content), WriteOptions(expected_version=ObjectVersion("prior")))
    bodies = [call.kwargs["Body"] for call in s3.upload_part.call_args_list]
    assert bodies == [content[:-4], b"tail"]
    assert all("IfMatch" not in call.kwargs for call in s3.upload_part.call_args_list)
    assert s3.complete_multipart_upload.call_args.kwargs["IfMatch"] == '"prior"'
    assert result.sha256 == hashlib.sha256(content).hexdigest() and result.size == len(content)
    s3.abort_multipart_upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("producer failed"), asyncio.CancelledError()])
async def test_producer_failure_or_cancellation_aborts_owned_upload_preserving_cause(failure):
    s3 = transport()
    s3.abort_multipart_upload.side_effect = RuntimeError("cleanup failed")
    async def producer():
        yield b"a" * transfers.STREAM_PART_SIZE
        raise failure
    with pytest.raises(type(failure)) as caught:
        await transfers.write_stream(s3, "bucket", "key", producer(), WriteOptions())
    assert caught.value is failure
    s3.abort_multipart_upload.assert_awaited_once_with(Bucket="bucket", Key="key", UploadId="owned")
    s3.complete_multipart_upload.assert_not_awaited()


def source(size=3):
    return ObjectInfo("source", size, ObjectVersion("snapshot"), None,
                      ContentProperties(content_type="text/plain", cache_control="private"), {"label": "value"}, '"snapshot"')


@pytest.mark.asyncio
async def test_conditional_copy_preserves_properties_pins_source_and_checks_destination_at_commit():
    s3 = transport()
    result = await transfers.copy(s3, "bucket", source(), "destination", CopyOptions(create_only=True), copy_mode="server")
    assert result.version == "committed"
    assert s3.create_multipart_upload.call_args.kwargs["CacheControl"] == "private"
    assert s3.create_multipart_upload.call_args.kwargs["Metadata"] == {"label": "value"}
    assert s3.upload_part_copy.call_args.kwargs["CopySourceIfMatch"] == '"snapshot"'
    assert s3.complete_multipart_upload.call_args.kwargs["IfNoneMatch"] == "*"


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["source", "destination"])
async def test_copy_reports_failed_condition_side_and_aborts(side):
    s3 = transport()
    operation = s3.upload_part_copy if side == "source" else s3.complete_multipart_upload
    operation.side_effect = S3Error("PreconditionFailed", 412, "fixture")
    with pytest.raises(CopyConditionFailed) as caught:
        await transfers.copy(s3, "bucket", source(), "destination", CopyOptions(create_only=True), copy_mode="server")
    assert caught.value.side == side
    s3.abort_multipart_upload.assert_awaited_once()
    s3.upload_part.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_committed_version_refuses_without_racy_head():
    s3 = transport()
    s3.put_object.return_value = {}
    with pytest.raises(StorageError) as caught:
        await transfers.write_stream(s3, "bucket", "key", chunks(b"x"), WriteOptions())
    assert caught.value.provider_code == "MissingCommittedVersion"


@pytest.mark.parametrize("committed", ['"opaque-winning-version"', '"d41d8cd98f00b204e9800998ecf8427e-2"', None])
async def test_copy_result_belongs_to_commit_response_never_a_competing_head(committed):
    s3 = transport()
    s3.complete_multipart_upload.return_value = {"ETag": committed} if committed is not None else {}
    s3.head_object = AsyncMock(return_value={"ETag": '"competing-writer"'})
    if committed is None:
        with pytest.raises(StorageError) as caught:
            await transfers.copy(s3, "bucket", source(), "destination", CopyOptions(create_only=True), copy_mode="server")
        assert caught.value.provider_code == "MissingCommittedVersion"
    else:
        result = await transfers.copy(s3, "bucket", source(), "destination", CopyOptions(create_only=True), copy_mode="server")
        assert result.version == committed[1:-1]
    s3.head_object.assert_not_awaited()


async def test_repeated_cancellation_drains_one_owned_abort_and_preserves_primary_stop():
    s3 = transport()
    producing, aborting, finish, completed = (asyncio.Event() for _ in range(4))
    async def producer():
        yield b"a" * transfers.STREAM_PART_SIZE
        producing.set()
        await asyncio.Event().wait()
    async def abort(**_):
        aborting.set()
        await finish.wait()
        completed.set()
    s3.abort_multipart_upload.side_effect = abort
    owner = asyncio.create_task(transfers.write_stream(s3, "bucket", "key", producer(), WriteOptions()))
    try:
        await producing.wait()
        owner.cancel("primary stop")
        await aborting.wait()
        owner.cancel("second stop")
        await asyncio.sleep(0)
        assert not owner.done() and not completed.is_set()
        finish.set()
        await asyncio.wait({owner})
        with pytest.raises(asyncio.CancelledError, match="primary stop"):
            owner.result()
        assert completed.is_set()
        s3.abort_multipart_upload.assert_awaited_once()
    finally:
        finish.set()
        if not owner.done():
            owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)


@pytest.mark.asyncio
async def test_optional_read_does_not_hide_body_absence_or_consumer_failure():
    @asynccontextmanager
    async def open_read(key):
        yield "body"
    store = SimpleNamespace(open_read=open_read)
    error = StorageError("not_found", provider_code="LateBodyFailure")
    caught_error = None
    try:
        async with optional_read(store, "key") as read:
            assert read == "body"
            raise error
    except StorageError as caught:
        caught_error = caught
    assert caught_error is error


@pytest.mark.asyncio
async def test_optional_read_maps_only_initial_confirmed_absence():
    @asynccontextmanager
    async def open_read(key):
        raise StorageError("not_found", provider_code="MissingKey")
        yield
    async with optional_read(SimpleNamespace(open_read=open_read), "key") as read:
        assert read is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,content_range,etag,expected", [
    (200, "bytes 0-2/3", '"snapshot"', "InvalidCopyRange"),
    (206, "bytes 1-3/3", '"snapshot"', "InvalidCopyRange"),
    (206, "bytes 0-2/3", '"changed"', "CopyConditionFailed"),
])
async def test_stream_copy_refuses_ignored_range_or_source_condition_and_closes_body(status, content_range, etag, expected):
    s3 = transport()
    body = SimpleNamespace(read=AsyncMock(return_value=b"abc"), close=AsyncMock())
    s3.get_object = AsyncMock(return_value={
        "Body": body, "ETag": etag, "ContentLength": 3,
        "ContentRange": content_range, "ResponseMetadata": {"HTTPStatusCode": status},
    })
    with pytest.raises(StorageError) as caught:
        await transfers.copy(s3, "bucket", source(), "destination", CopyOptions(create_only=True), copy_mode="stream")
    assert caught.value.provider_code == expected
    body.close.assert_awaited_once()
    s3.upload_part.assert_not_awaited()
    s3.abort_multipart_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_conditional_copy_checks_source_and_commits_empty_destination():
    s3 = transport()
    body = SimpleNamespace(read=AsyncMock(return_value=b""), close=AsyncMock())
    s3.get_object = AsyncMock(return_value={"Body": body, "ETag": '"snapshot"', "ContentLength": 0})
    result = await transfers.copy(s3, "bucket", source(0), "destination", CopyOptions(create_only=True), copy_mode="server")
    assert result.version == "committed"
    assert s3.get_object.call_args.kwargs["IfMatch"] == '"snapshot"'
    assert s3.put_object.call_args.kwargs["Body"] == b""
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    body.close.assert_awaited_once()
    s3.create_multipart_upload.assert_not_awaited()
