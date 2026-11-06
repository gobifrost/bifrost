"""Portable Solution source archives, separate from replaceable runtime files."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.core.storage import object_store, optional_read
from src.core.storage.contracts import ContentProperties, ObjectStore, WriteOptions
from src.services.application_deploy_storage import CHUNK_SIZE

SOURCE_ARTIFACTS_ROOT = "_solution_artifacts"
SOURCE_ARTIFACT_NAME = "source.zip"


class SolutionSourceArtifactStorage:
    def __init__(self, solution_id: UUID | str, settings: Settings | None = None):
        self.solution_id = str(solution_id)
        self.prefix = f"{SOURCE_ARTIFACTS_ROOT}/{self.solution_id}/"
        self._settings = settings or get_settings()

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    def _key(self) -> str:
        return f"{self.prefix}{SOURCE_ARTIFACT_NAME}"

    async def write(self, data: bytes) -> None:
        async with self._get_store() as store:
            await store.write(self._key(), data)

    async def write_from_path(self, path: Path) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    yield chunk

        async with self._get_store() as store:
            await store.write_stream(self._key(), chunks(), options=WriteOptions(
                properties=ContentProperties(content_type="application/zip"),
            ))

    async def read(self) -> bytes | None:
        async with self._get_store() as store:
            async with optional_read(store, self._key()) as read:
                return None if read is None else await read.body.read()

    async def copy_to_path(self, path: Path) -> bool:
        async with self._get_store() as store:
            async with optional_read(store, self._key()) as read:
                if read is None:
                    return False
                with path.open("wb") as destination:
                    async for chunk in read.body.iter_chunks(CHUNK_SIZE):
                        destination.write(chunk)
        return True

    async def delete(self) -> None:
        async with self._get_store() as store:
            await store.delete(self._key())
