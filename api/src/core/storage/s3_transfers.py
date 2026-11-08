"""S3 multipart mechanics, private to the S3 provider."""

import asyncio
import hashlib
import logging
import math
from collections.abc import AsyncIterator
from typing import Any, Literal

from src.core.s3_transport import S3Error, S3Transport
from src.core.storage.contracts import (
    CopyConditionFailed, CopyOptions, ObjectInfo, StorageError, StreamWriteResult,
    WriteOptions, WriteResult,
)
from src.core.storage.s3 import _condition, _properties, _version

logger = logging.getLogger(__name__)
STREAM_PART_SIZE = 8 * 1024**2
COPY_PART_SIZE = 256 * 1024**2
COPY_OBJECT_LIMIT = 5 * 1024**3
MAX_PARTS = 10_000


def _result(response: dict[str, Any]) -> WriteResult:
    etag = response.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise StorageError("backend_error", provider_code="MissingCommittedVersion")
    return WriteResult(_version(etag))


def _conditions(options: WriteOptions | CopyOptions) -> dict[str, str | None]:
    return {"IfNoneMatch": "*" if options.create_only else None, "IfMatch": _condition(options.expected_version)}


async def _abort(s3: S3Transport, bucket: str, key: str, upload_id: str) -> None:
    # The caller is already preserving a producer/transport failure. A second
    # cancellation must not release its owner before the one abort is drained.
    cleanup = asyncio.gather(s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id), return_exceptions=True)
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # Drain this owned abort; the caller re-raises its original failure.
            pass
    result = cleanup.result()[0]
    if isinstance(result, BaseException):
        logger.warning("Could not abort failed storage multipart upload (%s)", type(result).__name__)


async def write_stream(
    s3: S3Transport, bucket: str, key: str, chunks: AsyncIterator[bytes], options: WriteOptions,
) -> StreamWriteResult:
    digest, total = hashlib.sha256(), 0
    buffer = bytearray()
    upload_id: str | None = None
    parts: list[dict[str, Any]] = []
    headers = {**_properties(options.properties), "Metadata": options.metadata}

    async def part() -> None:
        nonlocal upload_id
        if len(parts) >= MAX_PARTS:
            raise StorageError("unsupported", provider_code="MultipartPartLimit")
        if upload_id is None:
            created = await s3.create_multipart_upload(Bucket=bucket, Key=key, **headers)
            candidate = created.get("UploadId")
            if not isinstance(candidate, str) or not candidate:
                raise StorageError("backend_error", provider_code="MissingMultipartIdentity")
            upload_id = candidate
        response = await s3.upload_part(
            Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=len(parts) + 1, Body=bytes(buffer),
        )
        etag = response.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise StorageError("backend_error", provider_code="MissingPartVersion")
        parts.append({"PartNumber": len(parts) + 1, "ETag": etag})
        buffer.clear()

    try:
        async for chunk in chunks:
            digest.update(chunk)
            total += len(chunk)
            view = memoryview(chunk)
            offset = 0
            while offset < len(view):
                take = min(STREAM_PART_SIZE - len(buffer), len(view) - offset)
                buffer.extend(view[offset:offset + take])
                offset += take
                if len(buffer) == STREAM_PART_SIZE:
                    await part()
        if upload_id is None:
            response = await s3.put_object(Bucket=bucket, Key=key, Body=bytes(buffer), **headers, **_conditions(options))
        else:
            if buffer:
                await part()
            response = await s3.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id,
                MultipartUpload={"Parts": parts}, **_conditions(options),
            )
        result = _result(response)
    except BaseException:
        if upload_id is not None:
            await _abort(s3, bucket, key, upload_id)
        raise
    return StreamWriteResult(result.version, total, digest.hexdigest())


async def _read_part(s3: S3Transport, bucket: str, source: ObjectInfo, start: int, end: int) -> bytes:
    length = end - start + 1
    response = await s3.get_object(
        Bucket=bucket, Key=source.key, IfMatch=_condition(source.version),
        Range=f"bytes={start}-{end}" if source.size else None,
    )
    body = response["Body"]
    try:
        if _version(response.get("ETag", "")) != source.version:
            raise CopyConditionFailed("source")
        if response.get("ContentLength") != length:
            raise StorageError("backend_error", provider_code="InvalidCopyRangeLength")
        if source.size and (
            response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 206
            or response.get("ContentRange") != f"bytes {start}-{end}/{source.size}"
        ):
            raise StorageError("backend_error", provider_code="InvalidCopyRange")
        data = await body.read(length + 1)
        if len(data) != length:
            raise StorageError("backend_error", provider_code="IncompleteCopyRange")
    except BaseException:
        try:
            await body.close()
        except Exception:
            logger.warning("Could not close failed storage copy read", exc_info=True)
        raise
    else:
        await body.close()
        return data


async def copy(
    s3: S3Transport, bucket: str, source: ObjectInfo, destination: str, options: CopyOptions,
    *, copy_mode: Literal["server", "stream"],
) -> WriteResult:
    if not source.version or source.size < 0:
        raise ValueError("Copy requires a valid source snapshot")
    properties = options.properties if options.properties is not None else source.properties
    metadata = options.metadata if options.metadata is not None else source.metadata
    headers = {**_properties(properties), "Metadata": metadata}
    copy_source = {"Bucket": bucket, "Key": source.key}
    conditional = options.create_only or options.expected_version is not None
    if source.size <= COPY_OBJECT_LIMIT and not conditional:
        try:
            response = await s3.copy_object(
                Bucket=bucket, Key=destination, CopySource=copy_source,
                CopySourceIfMatch=_condition(source.version), MetadataDirective="REPLACE", **headers,
            )
        except S3Error as error:
            if error.status_code == 412:
                raise CopyConditionFailed("source") from error
            raise
        return _result(response.get("CopyObjectResult", {}))
    if source.size == 0:
        try:
            data = await _read_part(s3, bucket, source, 0, -1)
        except S3Error as error:
            if error.status_code == 412:
                raise CopyConditionFailed("source") from error
            raise
        try:
            return _result(await s3.put_object(
                Bucket=bucket, Key=destination, Body=data, **headers, **_conditions(options),
            ))
        except S3Error as error:
            if error.status_code == 412:
                raise CopyConditionFailed("destination") from error
            raise
    created = await s3.create_multipart_upload(Bucket=bucket, Key=destination, **headers)
    upload_id = created.get("UploadId")
    if not isinstance(upload_id, str) or not upload_id:
        raise StorageError("backend_error", provider_code="MissingMultipartIdentity")
    minimum = STREAM_PART_SIZE if copy_mode == "stream" else COPY_PART_SIZE
    part_size = max(minimum, math.ceil(source.size / MAX_PARTS))
    try:
        parts = []
        for number, start in enumerate(range(0, source.size, part_size), start=1):
            end = min(start + part_size, source.size) - 1
            try:
                if copy_mode == "stream":
                    data = await _read_part(s3, bucket, source, start, end)
                    response = await s3.upload_part(
                        Bucket=bucket, Key=destination, UploadId=upload_id, PartNumber=number, Body=data,
                    )
                    etag = response["ETag"]
                else:
                    response = await s3.upload_part_copy(
                        Bucket=bucket, Key=destination, UploadId=upload_id, PartNumber=number,
                        CopySource=copy_source, CopySourceIfMatch=_condition(source.version),
                        CopySourceRange=f"bytes={start}-{end}",
                    )
                    etag = response["CopyPartResult"]["ETag"]
            except S3Error as error:
                if error.status_code == 412:
                    raise CopyConditionFailed("source") from error
                raise
            if not isinstance(etag, str) or not etag:
                raise StorageError("backend_error", provider_code="MissingPartVersion")
            parts.append({"PartNumber": number, "ETag": etag})
        try:
            response = await s3.complete_multipart_upload(
                Bucket=bucket, Key=destination, UploadId=upload_id,
                MultipartUpload={"Parts": parts}, **_conditions(options),
            )
        except S3Error as error:
            if error.status_code == 412 or error.code == "ConditionalRequestConflict":
                raise CopyConditionFailed("destination") from error
            raise
        return _result(response)
    except BaseException:
        await _abort(s3, bucket, destination, upload_id)
        raise
