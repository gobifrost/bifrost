"""Solution source storage, scoped to _solutions/{install_id}/."""

from __future__ import annotations

import hashlib
from uuid import UUID

from src.config import Settings, get_settings
from src.core.storage import object_pages, object_store, optional_read
from src.core.storage.contracts import ObjectStore
from src.services.repo_storage import SourceFileMetadata

SOLUTIONS_ROOT = "_solutions"


class SolutionStorage:
    def __init__(self, solution_id: UUID | str, settings: Settings | None = None):
        self.solution_id = str(solution_id)
        self.prefix = f"{SOLUTIONS_ROOT}/{self.solution_id}/"
        self._settings = settings or get_settings()

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    def _key(self, path: str) -> str:
        return f"{self.prefix}{path.lstrip('/')}"

    async def write(self, path: str, content: bytes) -> str:
        async with self._get_store() as store:
            await store.write(self._key(path), content)
        return hashlib.sha256(content).hexdigest()

    async def read(self, path: str) -> bytes:
        async with self._get_store() as store:
            async with store.open_read(self._key(path)) as read:
                return await read.body.read()

    async def delete(self, path: str) -> None:
        async with self._get_store() as store:
            await store.delete(self._key(path))

    async def list(self, prefix: str = "") -> list[str]:
        paths = []
        async with self._get_store() as store:
            async for page in object_pages(store, self._key(prefix)):
                paths.extend(item.key[len(self.prefix):] for item in page.objects)
        return paths

    async def list_with_metadata(self, prefix: str = "") -> dict[str, SourceFileMetadata]:
        result = {}
        async with self._get_store() as store:
            async for page in object_pages(store, self._key(prefix)):
                for item in page.objects:
                    result[item.key[len(self.prefix):]] = SourceFileMetadata.from_object(item)
        return result

    async def content_hash(self, path: str) -> str | None:
        async with self._get_store() as store:
            async with optional_read(store, self._key(path)) as read:
                if read is None:
                    return None
                digest = hashlib.sha256()
                async for chunk in read.body.iter_chunks():
                    digest.update(chunk)
                return digest.hexdigest()

    async def prefix_exists(self, prefix: str) -> bool:
        async with self._get_store() as store:
            async for page in object_pages(store, self._key(prefix.rstrip("/") + "/"), limit=1):
                if page.objects:
                    return True
        return False
