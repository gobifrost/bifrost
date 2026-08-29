"""Transient object-storage staging for independent App deploy source."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.services.file_storage.s3_client import S3StorageClient

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
        self._storage = S3StorageClient(self._settings)
        self._bucket = self._settings.s3_bucket or ""
        self.key = f"{APP_DEPLOY_INPUT_ROOT}/{self.job_id}/input.zip"

    async def write_path(self, path: Path) -> tuple[str, int]:
        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    yield chunk

        return await self._storage.put_object_from_chunks(
            self.key, chunks(), content_type="application/zip"
        )

    async def copy_to_path(self, path: Path, *, expected_sha256: str) -> int:
        digest = hashlib.sha256()
        size = 0
        with path.open("wb") as destination:
            async for chunk in self._storage.iter_object_chunks(
                self.key, chunk_size=CHUNK_SIZE
            ):
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
        async with self._storage.get_client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=self.key)
