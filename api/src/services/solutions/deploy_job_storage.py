"""Shared object-storage staging for scheduler-owned Solution deploy inputs."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.services.file_storage.s3_client import S3StorageClient

DEPLOY_JOB_ARTIFACTS_ROOT = "_solution_deploy_jobs"
CHUNK_SIZE = 8 * 1024 * 1024


class DeployJobInputIntegrityError(Exception):
    pass


class SolutionDeployJobStorage:
    def __init__(self, job_id: UUID | str, settings: Settings | None = None):
        self.job_id = str(job_id)
        self._settings = settings or get_settings()
        self._storage = S3StorageClient(self._settings)
        self._bucket = self._settings.s3_bucket or ""
        self.key = f"{DEPLOY_JOB_ARTIFACTS_ROOT}/{self.job_id}/input.zip"

    async def write_path(self, path: Path) -> tuple[str, int]:
        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    yield chunk

        return await self._storage.put_object_from_chunks(
            self.key, chunks(), content_type="application/zip"
        )

    async def write_bytes(self, data: bytes) -> tuple[str, int]:
        async def chunks() -> AsyncIterator[bytes]:
            yield data

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
            raise DeployJobInputIntegrityError(
                f"staged input for deploy job {self.job_id} failed integrity check"
            )
        return size

    async def delete(self) -> None:
        async with self._storage.get_client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=self.key)
