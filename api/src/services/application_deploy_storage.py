"""Transient object-storage staging for independent App deploy source."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.core.storage import object_store, optional_read
from src.core.storage.contracts import ContentProperties, ObjectStore, WriteOptions

APP_DEPLOY_INPUT_ROOT = "_application_deploy_jobs"
CHUNK_SIZE = 8 * 1024 * 1024


class ApplicationDeployInputIntegrityError(Exception):
    pass


class ApplicationDeployStorage:
    """Own the source zip for exactly one platform job.

    The job deletes this object in a ``finally`` block. It is build input, not
    an application source store.
    """

    def __init__(self, job_id: UUID | str, settings: Settings | None = None):
        self.job_id = str(job_id)
        self._settings = settings or get_settings()
        self.key = f"{APP_DEPLOY_INPUT_ROOT}/{self.job_id}/input.zip"

    def _get_store(self) -> ObjectStore:
        return object_store(self._settings)

    async def write_path(self, path: Path) -> tuple[str, int]:
        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    yield chunk

        async with self._get_store() as store:
            result = await store.write_stream(self.key, chunks(), options=WriteOptions(
                properties=ContentProperties(content_type="application/zip"),
            ))
            return result.sha256, result.size

    async def copy_to_path(self, path: Path, *, expected_sha256: str) -> int:
        digest = hashlib.sha256()
        size = 0
        async with self._get_store() as store:
            async with optional_read(store, self.key) as read:
                if read is None:
                    raise FileNotFoundError("Staged App input archive not found")
                with path.open("wb") as destination:
                    async for chunk in read.body.iter_chunks(CHUNK_SIZE):
                        destination.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
        if digest.hexdigest() != expected_sha256:
            path.unlink(missing_ok=True)
            raise ApplicationDeployInputIntegrityError(
                f"staged App input for job {self.job_id} failed integrity check"
            )
        return size

    async def delete(self) -> None:
        async with self._get_store() as store:
            await store.delete(self.key)
