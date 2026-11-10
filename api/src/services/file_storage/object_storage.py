"""Workspace/managed-file operations over the shared storage provider."""
import hashlib
import mimetypes
from collections.abc import AsyncIterator
from typing import Literal

import httpx

from src.config import Settings
from src.core.storage import object_pages, object_store, optional_read, storage_configured
from src.core.storage.contracts import (
    ContentProperties, CopyConditionFailed, CopyOptions, ObjectInfo, ObjectStore,
    ObjectVersion, SignedRequest, StorageConfigurationError, StorageError, WriteOptions,
)


class ObjectMissing(Exception):
    """The source disappeared while a managed-file move was copying it."""


class ObjectPreconditionFailed(Exception):
    def __init__(self, side: Literal["source", "destination"]):
        super().__init__(f"{side} precondition failed")
        self.side: Literal["source", "destination"] = side


def is_storage_unavailable(error: BaseException) -> bool:
    if isinstance(error, StorageError):
        return error.kind in {"permission_denied", "unavailable"}
    return isinstance(error, (httpx.TransportError, ConnectionError, TimeoutError))


class FileObjectStorage:
    def __init__(self, settings: Settings):
        self.settings = settings

    def get_store(self) -> ObjectStore:
        if not storage_configured(self.settings):
            raise StorageConfigurationError("Object storage not configured")
        return object_store(self.settings)

    def serialize_version(self, version: ObjectVersion) -> str:
        return self.get_store().serialize_version(version)

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def guess_content_type(path: str) -> str:
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    async def generate_signed_upload_request(self, path: str, content_type: str, expires_in: int = 600, *, content_length: int | None = None) -> SignedRequest:
        async with self.get_store() as store:
            return await store.signed_request(path, "PUT", content_type=content_type, expires_in=expires_in, content_length=content_length)

    async def generate_signed_download_request(
        self, path: str, expires_in: int = 600, *, response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> SignedRequest:
        async with self.get_store() as store:
            return await store.signed_request(path, "GET", expires_in=expires_in,
                                              response_content_type=response_content_type,
                                              response_content_disposition=response_content_disposition)

    async def generate_presigned_download_url(
        self, path: str, expires_in: int = 600, *, response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        request = await self.generate_signed_download_request(
            path, expires_in, response_content_type=response_content_type,
            response_content_disposition=response_content_disposition,
        )
        if request.headers:
            raise StorageError("unsupported", provider_code="SignedRequestHeadersRequired")
        return request.url

    async def read_uploaded_file(self, path: str) -> bytes:
        async with self.get_store() as store:
            async with optional_read(store, path) as read:
                if read is None:
                    raise FileNotFoundError(f"File not found: {path}")
                return await read.body.read()

    async def iter_object_chunks(self, path: str, *, chunk_size: int = 8 * 1024**2) -> AsyncIterator[bytes]:
        async with self.get_store() as store:
            async with optional_read(store, path) as read:
                if read is None:
                    raise FileNotFoundError(f"File not found: {path}")
                async for chunk in read.body.iter_chunks(chunk_size):
                    yield chunk

    async def put_object_from_chunks(
        self, path: str, chunks: AsyncIterator[bytes], *, content_type: str | None = None,
    ) -> tuple[str, int]:
        async with self.get_store() as store:
            result = await store.write_stream(path, chunks, options=WriteOptions(
                properties=ContentProperties(content_type=content_type),
            ))
            return result.sha256, result.size

    async def head_object(self, key: str) -> ObjectInfo | None:
        async with self.get_store() as store:
            try:
                return await store.stat(key)
            except StorageError as error:
                if error.kind == "not_found":
                    return None
                raise

    async def copy_object(self, source_key: str, destination_key: str, *, source: ObjectInfo, metadata: dict[str, str]) -> None:
        if source.key != source_key:
            raise ValueError("Copy source snapshot does not match its key")
        async with self.get_store() as store:
            try:
                await store.copy(source, destination_key, options=CopyOptions(metadata=metadata, create_only=True))
            except CopyConditionFailed as error:
                raise ObjectPreconditionFailed(error.side) from error
            except StorageError as error:
                if error.kind == "not_found":
                    raise ObjectMissing(source_key) from error
                raise

    async def delete_object_strict(self, key: str) -> None:
        async with self.get_store() as store:
            await store.delete(key)

    async def list_keys(self, prefix: str, *, delimiter: str = "/") -> list[str]:
        async with self.get_store() as store:
            return [item.key async for page in object_pages(store, prefix, delimiter=delimiter) for item in page.objects]
