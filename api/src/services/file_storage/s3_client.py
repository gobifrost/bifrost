"""S3 storage client for workspace files.

Handles S3 operations including:
- Presigned upload URLs
- File reading from S3
- Content hashing
- MIME type detection
"""

import hashlib
import logging
import math
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from src.config import Settings
from src.core.s3_transport import S3Error, S3Transport

logger = logging.getLogger(__name__)


COPY_OBJECT_LIMIT = 5 * 1024**3
MIN_COPY_PART = 256 * 1024**2
_MAX_PARTS = 10_000
# System metadata a copy must carry over (MetadataDirective=REPLACE drops it).
_COPIED_HEADERS = (
    "ContentType",
    "ContentDisposition",
    "ContentEncoding",
    "ContentLanguage",
    "CacheControl",
    "Expires",
)
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
_UNAVAILABLE_CODES = frozenset({"AccessDenied", "403", "Forbidden", "InvalidAccessKeyId", "SignatureDoesNotMatch"})


@dataclass(frozen=True)
class ObjectHead:
    """The parts of a HeadObject response a server-side move relies on."""

    size: int
    etag: str
    metadata: dict[str, str]
    headers: dict[str, Any]


class ObjectMissing(Exception):
    """The copy source no longer exists."""


class ObjectPreconditionFailed(Exception):
    """A conditional copy was refused: the source changed or the destination exists."""

    def __init__(self, side: Literal["source", "destination"]) -> None:
        super().__init__(f"{side} precondition failed")
        self.side = side


def _error_code(exc: S3Error) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _http_status(exc: S3Error) -> int | None:
    return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def is_storage_unavailable(exc: BaseException) -> bool:
    """True when ``exc`` means object storage is unreachable or refusing the bucket."""
    if isinstance(exc, S3Error):
        status = _http_status(exc)
        return _error_code(exc) in _UNAVAILABLE_CODES or (status is not None and status >= 500)
    return isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError))


def _copy_error(exc: S3Error) -> Exception:
    if _error_code(exc) == "PreconditionFailed" or _http_status(exc) == 412:
        return ObjectPreconditionFailed("source")
    if _error_code(exc) in _NOT_FOUND_CODES:
        return ObjectMissing(str(exc))
    return exc


class S3StorageClient:
    """Client for S3 storage operations."""

    def __init__(self, settings: Settings):
        """
        Initialize S3 storage client.

        Args:
            settings: Application settings with S3 configuration
        """
        self.settings = settings

    @asynccontextmanager
    async def get_client(self):
        """
        Get S3 client context manager.

        Yields:
            Async CRT-signed S3 client

        Raises:
            RuntimeError: If S3 storage is not configured
        """
        if not self.settings.s3_configured:
            raise RuntimeError("S3 storage not configured")

        async with S3Transport(
            endpoint_url=self.settings.s3_endpoint_url,
            access_key_id=self.settings.s3_access_key,
            secret_access_key=self.settings.s3_secret_key,
            region=self.settings.s3_region,
        ) as client:
            yield client

    def _rewrite_presigned_url(self, url: str) -> str:
        """Rewrite a presigned URL to use the public endpoint if configured.

        When s3_public_endpoint_url is set (e.g. "/s3"), replaces the
        scheme+host+port of the presigned URL so the browser routes through
        the Vite proxy instead of hitting the internal Docker endpoint.
        """
        public = self.settings.s3_public_endpoint_url
        if not public:
            return url

        parsed = urlparse(url)
        # public is a path prefix like "/s3" — make the URL origin-relative
        return f"{public}{parsed.path}?{parsed.query}"

    @staticmethod
    def compute_hash(content: bytes) -> str:
        """
        Compute SHA-256 hash of content.

        Args:
            content: File content bytes

        Returns:
            Hex-encoded SHA-256 hash
        """
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def guess_content_type(path: str) -> str:
        """
        Guess content type from file path.

        Args:
            path: File path

        Returns:
            MIME type string (defaults to 'application/octet-stream' if unknown)
        """
        content_type, _ = mimetypes.guess_type(path)
        return content_type or "application/octet-stream"

    async def generate_presigned_upload_url(
        self,
        path: str,
        content_type: str,
        expires_in: int = 600,
    ) -> str:
        """
        Generate a presigned PUT URL for direct S3 upload.

        Uses the files bucket (not workspace bucket) for form uploads.
        The files bucket is for runtime uploads that are not git-tracked.

        Args:
            path: Target path in S3 (e.g., "uploads/{form_id}/{uuid}/{filename}")
            content_type: MIME type of the file being uploaded
            expires_in: URL expiration time in seconds (default 10 minutes)

        Returns:
            Presigned PUT URL for direct browser upload
        """
        async with self.get_client() as s3:
            url = await s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.settings.s3_bucket,
                    "Key": path,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in,
            )
        return self._rewrite_presigned_url(url)

    async def generate_presigned_download_url(
        self,
        path: str,
        expires_in: int = 600,
        *,
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        """
        Generate a presigned GET URL for direct S3 download.

        Args:
            path: Target path in S3
            expires_in: URL expiration time in seconds (default 10 minutes)

        Returns:
            Presigned GET URL for direct download
        """
        params = {
            "Bucket": self.settings.s3_bucket,
            "Key": path,
        }
        if response_content_type is not None:
            params["ResponseContentType"] = response_content_type
        if response_content_disposition is not None:
            params["ResponseContentDisposition"] = response_content_disposition

        async with self.get_client() as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires_in,
            )
        return self._rewrite_presigned_url(url)

    async def read_uploaded_file(self, path: str) -> bytes:
        """
        Read a file from the bucket (for uploaded files).

        Args:
            path: File path in the bucket (e.g., uploads/{form_id}/{uuid}/filename)

        Returns:
            File content as bytes

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        async with self.get_client() as s3:
            try:
                response = await s3.get_object(
                    Bucket=self.settings.s3_bucket,
                    Key=path,
                )
                return await response["Body"].read()
            except s3.exceptions.NoSuchKey:
                raise FileNotFoundError(f"File not found: {path}")

    async def iter_object_chunks(
        self,
        path: str,
        *,
        chunk_size: int = 8 * 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        """Yield an S3 object in bounded chunks."""
        async with self.get_client() as s3:
            try:
                response = await s3.get_object(
                    Bucket=self.settings.s3_bucket,
                    Key=path,
                )
            except s3.exceptions.NoSuchKey:
                raise FileNotFoundError(f"File not found: {path}")

            body = response["Body"]
            async with body:
                while True:
                    chunk = await body.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

    async def put_object_from_chunks(
        self,
        path: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str | None = None,
        part_size: int = 8 * 1024 * 1024,
    ) -> tuple[str, int]:
        """Write chunks to S3, returning ``(sha256, size)``.

        Small objects are uploaded with a single PUT. Larger streams switch to
        multipart upload without retaining the whole object in memory.
        """
        digest = hashlib.sha256()
        total = 0
        buffer = bytearray()
        upload_id: str | None = None
        parts: list[dict[str, int | str]] = []
        part_number = 1

        async with self.get_client() as s3:

            async def ensure_upload() -> str:
                nonlocal upload_id
                if upload_id is not None:
                    return upload_id
                created = await s3.create_multipart_upload(
                    Bucket=self.settings.s3_bucket,
                    Key=path,
                    ContentType=content_type or self.guess_content_type(path),
                )
                created_upload_id = created.get("UploadId")
                if not isinstance(created_upload_id, str) or not created_upload_id:
                    raise ValueError("Object storage did not return a valid multipart upload ID")
                upload_id = created_upload_id
                return created_upload_id

            async def upload_part(data: bytes) -> None:
                nonlocal part_number
                current_upload_id = await ensure_upload()
                response = await s3.upload_part(
                    Bucket=self.settings.s3_bucket,
                    Key=path,
                    UploadId=current_upload_id,
                    PartNumber=part_number,
                    Body=data,
                )
                parts.append({"PartNumber": part_number, "ETag": response["ETag"]})
                part_number += 1

            try:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    digest.update(chunk)
                    total += len(chunk)
                    buffer.extend(chunk)
                    while len(buffer) >= part_size:
                        part = bytes(buffer[:part_size])
                        del buffer[:part_size]
                        await upload_part(part)

                if upload_id is None:
                    await s3.put_object(
                        Bucket=self.settings.s3_bucket,
                        Key=path,
                        Body=bytes(buffer),
                        ContentType=content_type or self.guess_content_type(path),
                    )
                else:
                    if buffer:
                        await upload_part(bytes(buffer))
                    await s3.complete_multipart_upload(
                        Bucket=self.settings.s3_bucket,
                        Key=path,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                    )
            except Exception:
                if upload_id is not None:
                    await s3.abort_multipart_upload(
                        Bucket=self.settings.s3_bucket,
                        Key=path,
                        UploadId=upload_id,
                    )
                raise

        return digest.hexdigest(), total

    async def head_object(self, key: str) -> ObjectHead | None:
        """HeadObject; ``None`` only when the key does not exist, anything else raises."""
        async with self.get_client() as s3:
            try:
                response = await s3.head_object(Bucket=self.settings.s3_bucket, Key=key)
            except S3Error as exc:
                if _error_code(exc) in _NOT_FOUND_CODES:
                    return None
                raise
        return ObjectHead(
            size=int(response["ContentLength"]),
            etag=response["ETag"],
            metadata=dict(response.get("Metadata") or {}),
            headers={
                name: response[name]
                for name in _COPIED_HEADERS
                if response.get(name) is not None
            },
        )

    async def copy_object(
        self,
        source_key: str,
        destination_key: str,
        *,
        source: ObjectHead,
        metadata: dict[str, str],
        multipart_threshold: int = COPY_OBJECT_LIMIT,
        part_size: int | None = None,
    ) -> None:
        """Server-side copy of exactly the bytes ``source`` describes.

        Every copy request carries ``CopySourceIfMatch=source.etag``. Objects
        up to ``multipart_threshold`` use one ``CopyObject``; larger ones use
        a multipart part copy whose completion refuses to overwrite an object
        that appeared at the destination (``IfNoneMatch="*"``) and which is
        aborted on any failure.
        """
        bucket = self.settings.s3_bucket
        copy_source = {"Bucket": bucket, "Key": source_key}
        async with self.get_client() as s3:
            if source.size <= multipart_threshold:
                try:
                    await s3.copy_object(
                        Bucket=bucket,
                        Key=destination_key,
                        CopySource=copy_source,
                        CopySourceIfMatch=source.etag,
                        MetadataDirective="REPLACE",
                        Metadata=metadata,
                        **source.headers,
                    )
                except S3Error as exc:
                    mapped = _copy_error(exc)
                    if mapped is exc:
                        raise
                    raise mapped from exc
                return

            size_per_part = part_size or max(MIN_COPY_PART, math.ceil(source.size / _MAX_PARTS))
            created = await s3.create_multipart_upload(
                Bucket=bucket, Key=destination_key, Metadata=metadata, **source.headers
            )
            upload_id = created["UploadId"]
            try:
                parts: list[dict[str, Any]] = []
                for number, start in enumerate(range(0, source.size, size_per_part), start=1):
                    end = min(start + size_per_part, source.size) - 1
                    try:
                        response = await s3.upload_part_copy(
                            Bucket=bucket,
                            Key=destination_key,
                            UploadId=upload_id,
                            PartNumber=number,
                            CopySource=copy_source,
                            CopySourceRange=f"bytes={start}-{end}",
                            CopySourceIfMatch=source.etag,
                        )
                    except S3Error as exc:
                        mapped = _copy_error(exc)
                        if mapped is exc:
                            raise
                        raise mapped from exc
                    parts.append({"PartNumber": number, "ETag": response["CopyPartResult"]["ETag"]})
                try:
                    await s3.complete_multipart_upload(
                        Bucket=bucket,
                        Key=destination_key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                        IfNoneMatch="*",
                    )
                except S3Error as exc:
                    if _error_code(exc) in {"PreconditionFailed", "ConditionalRequestConflict"} or _http_status(exc) == 412:
                        raise ObjectPreconditionFailed("destination") from exc
                    raise
            except BaseException:
                try:
                    await s3.abort_multipart_upload(
                        Bucket=bucket, Key=destination_key, UploadId=upload_id
                    )
                except Exception:
                    logger.warning("Could not abort multipart copy", exc_info=True)
                raise

    async def delete_object_strict(self, key: str) -> None:
        """DeleteObject that raises on any failure (unlike ``delete_raw_from_s3``)."""
        async with self.get_client() as s3:
            await s3.delete_object(Bucket=self.settings.s3_bucket, Key=key)

    async def list_keys(self, prefix: str, *, delimiter: str = "/") -> list[str]:
        """Keys directly under ``prefix`` (no deeper ``delimiter``), all pages."""
        keys: list[str] = []
        kwargs: dict[str, Any] = {
            "Bucket": self.settings.s3_bucket,
            "Prefix": prefix,
            "Delimiter": delimiter,
        }
        async with self.get_client() as s3:
            while True:
                response = await s3.list_objects_v2(**kwargs)
                keys.extend(item["Key"] for item in response.get("Contents", []))
                if not response.get("IsTruncated"):
                    return keys
                kwargs["ContinuationToken"] = response["NextContinuationToken"]
