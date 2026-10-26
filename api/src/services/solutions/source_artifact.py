"""Stored source artifact for a deployed Solution install.

The artifact is the portable workspace zip that produced the install. Runtime
outputs still live elsewhere: Python execution files under ``_solutions/`` and
app dist under ``_apps/``. Keeping the zip in a separate prefix prevents the
Python full-replace writer from deleting it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.core.s3_transport import S3NoSuchKey, S3Transport
from src.services.application_deploy_storage import CHUNK_SIZE
from src.services.file_storage.s3_client import S3StorageClient

SOURCE_ARTIFACTS_ROOT = "_solution_artifacts"
SOURCE_ARTIFACT_NAME = "source.zip"


class SolutionSourceArtifactStorage:
    """S3 storage for one install's source workspace zip."""

    def __init__(self, solution_id: UUID | str, settings: Settings | None = None):
        self.solution_id = str(solution_id)
        self.prefix = f"{SOURCE_ARTIFACTS_ROOT}/{self.solution_id}/"
        self._settings = settings or get_settings()
        self._bucket: str = self._settings.s3_bucket or ""
        self._storage = S3StorageClient(self._settings)

    @asynccontextmanager
    async def _get_client(self):
        async with S3Transport(
            endpoint_url=self._settings.s3_endpoint_url,
            access_key_id=self._settings.s3_access_key,
            secret_access_key=self._settings.s3_secret_key,
            region=self._settings.s3_region,
        ) as client:
            yield client

    def _key(self) -> str:
        return f"{self.prefix}{SOURCE_ARTIFACT_NAME}"

    async def write(self, data: bytes) -> None:
        async with self._get_client() as client:
            await client.put_object(Bucket=self._bucket, Key=self._key(), Body=data)

    async def write_from_path(self, path: Path) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    yield chunk

        await self._storage.put_object_from_chunks(
            self._key(), chunks(), content_type="application/zip"
        )

    async def read(self) -> bytes | None:
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=self._key())
                return await response["Body"].read()
            except S3NoSuchKey:
                return None

    async def copy_to_path(self, path: Path) -> bool:
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=self._key())
            except S3NoSuchKey:
                return False

            body = response["Body"]
            async with body:
                with path.open("wb") as f:
                    while chunk := await body.read(8 * 1024 * 1024):
                        f.write(chunk)
            return True

    async def delete(self) -> None:
        async with self._get_client() as client:
            await client.delete_object(Bucket=self._bucket, Key=self._key())
