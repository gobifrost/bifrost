"""Workspace source storage, scoped to the provider-independent _repo/ prefix."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from src.config import Settings, get_settings
from src.core.storage import object_pages, object_store, optional_read
from src.core.storage.contracts import ObjectInfo, ObjectStore, ObjectSummary, ObjectVersion, StorageError

REPO_PREFIX = "_repo/"


@dataclass(frozen=True)
class SourceFileMetadata:
    """A source object's opaque version witness and listing metadata."""

    version: ObjectVersion
    last_modified: datetime
    size: int

    @classmethod
    def from_object(cls, item: ObjectInfo | ObjectSummary) -> SourceFileMetadata:
        if item.modified_at is None:
            raise StorageError("backend_error", provider_code="MissingSourceTimestamp")
        return cls(item.version, item.modified_at, item.size)


class RepoStorage:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    def _repo_key(self, path: str) -> str:
        return f"{REPO_PREFIX}{path.lstrip('/')}"

    async def read(self, path: str) -> bytes:
        async with self._get_store() as store:
            async with store.open_read(self._repo_key(path)) as read:
                return await read.body.read()

    async def write(self, path: str, content: bytes) -> str:
        async with self._get_store() as store:
            await store.write(self._repo_key(path), content)
        return self.compute_hash(content)

    async def delete(self, path: str) -> None:
        async with self._get_store() as store:
            await store.delete(self._repo_key(path))

    async def list(self, prefix: str = "") -> list[str]:
        paths = []
        async with self._get_store() as store:
            async for page in object_pages(store, self._repo_key(prefix)):
                paths.extend(item.key[len(REPO_PREFIX):] for item in page.objects)
        return paths

    async def list_with_metadata(self, prefix: str = "") -> dict[str, SourceFileMetadata]:
        result = {}
        async with self._get_store() as store:
            async for page in object_pages(store, self._repo_key(prefix)):
                for item in page.objects:
                    result[item.key[len(REPO_PREFIX):]] = SourceFileMetadata.from_object(item)
        return result

    async def list_directory(
        self, prefix: str = "", exclude_fn: Callable[[str], bool] | None = None,
    ) -> tuple[list[str], list[str]]:
        from src.services.editor.file_filter import is_excluded_path

        filter_fn = exclude_fn or is_excluded_path
        files, folders = [], []
        async with self._get_store() as store:
            async for page in object_pages(store, self._repo_key(prefix), delimiter="/"):
                files.extend(item.key[len(REPO_PREFIX):] for item in page.objects)
                folders.extend(path[len(REPO_PREFIX):] for path in page.prefixes)
        return (
            sorted(path for path in files if path and not filter_fn(path)),
            sorted(path for path in folders if path and not filter_fn(path.rstrip("/"))),
        )

    async def exists(self, path: str) -> bool:
        return await self.head(path) is not None

    async def head(self, path: str) -> SourceFileMetadata | None:
        try:
            async with self._get_store() as store:
                return SourceFileMetadata.from_object(await store.stat(self._repo_key(path)))
        except StorageError as error:
            if error.kind == "not_found":
                return None
            raise

    async def content_hash(self, path: str) -> str | None:
        async with self._get_store() as store:
            async with optional_read(store, self._repo_key(path)) as read:
                if read is None:
                    return None
                digest = hashlib.sha256()
                async for chunk in read.body.iter_chunks():
                    digest.update(chunk)
                return digest.hexdigest()

    async def prefix_exists(self, prefix: str) -> bool:
        async with self._get_store() as store:
            async for page in object_pages(store, self._repo_key(prefix.rstrip("/") + "/"), limit=1):
                if page.objects:
                    return True
        return False

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()
