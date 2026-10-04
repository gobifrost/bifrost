"""Object storage for archived audit segments.

A thin adapter over ``S3StorageClient``. Errors are never swallowed: the
archiver deletes Postgres rows and catalog rows only after storage calls
succeed, so a hidden failure here would lose audit events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from src.config import Settings
from src.services.file_storage.s3_client import S3StorageClient

CONTENT_TYPE = "application/gzip"


class ArchiveStorageUnavailable(Exception):
    """Object storage is not configured, so nothing can be archived."""


class AuditArchiveStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.s3_configured:
            raise ArchiveStorageUnavailable("object storage is not configured")
        self._bucket = settings.s3_bucket
        self._client = S3StorageClient(settings)

    async def put(self, key: str, body: bytes) -> None:
        async with self._client.get_client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=CONTENT_TYPE)

    async def get(self, key: str) -> bytes:
        return await self._client.read_uploaded_file(key)

    def iter_chunks(self, key: str) -> AsyncIterator[bytes]:
        return self._client.iter_object_chunks(key)

    async def delete(self, key: str) -> None:
        # DeleteObject succeeds for a missing key; every other error propagates.
        async with self._client.get_client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def put_chunks(self, key: str, chunks: AsyncIterator[bytes]) -> tuple[str, int]:
        return await self._client.put_object_from_chunks(key, chunks, content_type=CONTENT_TYPE)
