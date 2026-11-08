"""Retained source artifacts for independent App deployments."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.core.storage import object_pages, object_store, optional_read
from src.core.storage.contracts import ContentProperties, ObjectStore, StorageError, WriteOptions
from src.services.application_deploy_storage import CHUNK_SIZE

APPLICATION_ARTIFACT_ROOT = "_application_artifacts"


class ApplicationSourceArtifactStorage:
    """Store immutable sanitized source zips for active App deployments."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    @staticmethod
    def deployment_source_key(app_id: UUID | str, deployment_id: UUID | str) -> str:
        return (
            f"{APPLICATION_ARTIFACT_ROOT}/{app_id}/deployments/"
            f"{deployment_id}/source.zip"
        )

    @staticmethod
    def application_prefix(app_id: UUID | str) -> str:
        return f"{APPLICATION_ARTIFACT_ROOT}/{app_id}/"

    async def write_deployment_source(
        self, app_id: UUID | str, deployment_id: UUID | str, path: Path
    ) -> tuple[str, int]:
        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    yield chunk

        async with self._get_store() as store:
            result = await store.write_stream(self.deployment_source_key(app_id, deployment_id), chunks(), options=WriteOptions(
                properties=ContentProperties(content_type="application/zip"),
            ))
            return result.sha256, result.size

    async def copy_deployment_source_to_path(
        self, app_id: UUID | str, deployment_id: UUID | str, path: Path
    ) -> int:
        size = 0
        with path.open("wb") as destination:
            async for chunk in self.iter_deployment_source(app_id, deployment_id):
                destination.write(chunk)
                size += len(chunk)
        return size

    async def read_deployment_source(
        self, app_id: UUID | str, deployment_id: UUID | str
    ) -> bytes:
        async with self._get_store() as store:
            async with store.open_read(self.deployment_source_key(app_id, deployment_id)) as read:
                return await read.body.read()

    async def iter_deployment_source(
        self, app_id: UUID | str, deployment_id: UUID | str
    ) -> AsyncIterator[bytes]:
        """Stream retained source without buffering the archive in API memory."""
        async with self._get_store() as store:
            async with optional_read(store, self.deployment_source_key(app_id, deployment_id)) as read:
                if read is None:
                    raise FileNotFoundError("Retained App source archive not found")
                async for chunk in read.body.iter_chunks(CHUNK_SIZE):
                    yield chunk

    async def deployment_source_exists(self, app_id: UUID | str, deployment_id: UUID | str) -> bool:
        async with self._get_store() as store:
            try:
                await store.stat(self.deployment_source_key(app_id, deployment_id))
            except StorageError as error:
                if error.kind == "not_found":
                    return False
                raise
        return True

    async def delete_deployment_source(self, app_id: UUID | str, deployment_id: UUID | str) -> None:
        async with self._get_store() as store:
            await store.delete(self.deployment_source_key(app_id, deployment_id))

    async def delete_application_artifacts(self, app_id: UUID | str) -> None:
        async with self._get_store() as store:
            keys = [item.key async for page in object_pages(store, self.application_prefix(app_id))
                    for item in page.objects]
            await store.delete_many(keys)
