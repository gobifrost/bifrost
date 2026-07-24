"""S3 storage client for workspace files.

Handles S3 operations including:
- Presigned upload URLs
- File reading from S3
- Content hashing
- MIME type detection
"""

import hashlib
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from src.config import Settings


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
            Async S3 client from aiobotocore

        Raises:
            RuntimeError: If S3 storage is not configured
        """
        if not self.settings.s3_configured:
            raise RuntimeError("S3 storage not configured")

        from aiobotocore.session import get_session

        session = get_session()
        async with session.create_client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name=self.settings.s3_region,
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

    def presigned_upload_headers(self, content_type: str) -> dict[str, str]:
        return {"Content-Type": content_type}

    async def generate_presigned_download_url(
        self,
        path: str,
        expires_in: int = 600,
    ) -> str:
        """
        Generate a presigned GET URL for direct S3 download.

        Args:
            path: Target path in S3
            expires_in: URL expiration time in seconds (default 10 minutes)

        Returns:
            Presigned GET URL for direct download
        """
        async with self.get_client() as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.settings.s3_bucket,
                    "Key": path,
                },
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
                if upload_id is None:
                    created = await s3.create_multipart_upload(
                        Bucket=self.settings.s3_bucket,
                        Key=path,
                        ContentType=content_type or self.guess_content_type(path),
                    )
                    upload_id = created["UploadId"]
                return upload_id

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
