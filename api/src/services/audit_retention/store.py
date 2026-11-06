"""Object storage for archived audit segments.

A scoped consumer of the shared storage provider. Errors are never swallowed: the
archiver deletes Postgres rows and catalog rows only after storage calls
succeed, so a hidden failure here would lose audit events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from src.config import Settings
from src.core.storage import object_pages, object_store, optional_read, storage_configured
from src.core.storage.contracts import ContentProperties, ObjectStore, WriteOptions

CONTENT_TYPE = "application/gzip"


class ArchiveStorageUnavailable(Exception):
    """Object storage is not configured, so nothing can be archived."""


class AuditArchiveStore:
    def __init__(self, settings: Settings) -> None:
        if not storage_configured(settings):
            raise ArchiveStorageUnavailable("object storage is not configured")
        self._settings = settings

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    async def put(self, key: str, body: bytes) -> None:
        async with self._get_store() as store:
            await store.write(key, body, options=WriteOptions(properties=ContentProperties(content_type=CONTENT_TYPE)))

    async def get(self, key: str) -> bytes:
        async with self._get_store() as store:
            async with optional_read(store, key) as read:
                if read is None:
                    raise FileNotFoundError(f"Audit archive not found: {key}")
                return await read.body.read()

    async def iter_chunks(self, key: str) -> AsyncIterator[bytes]:
        async with self._get_store() as store:
            async with optional_read(store, key) as read:
                if read is None:
                    raise FileNotFoundError(f"Audit archive not found: {key}")
                async for chunk in read.body.iter_chunks():
                    yield chunk

    async def delete(self, key: str) -> None:
        async with self._get_store() as store:
            await store.delete(key)

    async def put_chunks(self, key: str, chunks: AsyncIterator[bytes]) -> tuple[str, int]:
        async with self._get_store() as store:
            result = await store.write_stream(key, chunks, options=WriteOptions(
                properties=ContentProperties(content_type=CONTENT_TYPE),
            ))
            return result.sha256, result.size

    async def iter_keys(self, prefix: str) -> AsyncIterator[str]:
        async with self._get_store() as store:
            async for page in object_pages(store, prefix):
                for item in page.objects:
                    yield item.key
