"""Retained source artifacts for independent App deployments."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.core.s3_transport import S3Error
from src.services.application_deploy_storage import CHUNK_SIZE
from src.services.file_storage.s3_client import S3StorageClient

APPLICATION_ARTIFACT_ROOT = "_application_artifacts"


class ApplicationSourceArtifactStorage:
    """Store immutable sanitized source zips for active App deployments."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._storage = S3StorageClient(self._settings)
        self._bucket = self._settings.s3_bucket or ""

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

        return await self._storage.put_object_from_chunks(
            self.deployment_source_key(app_id, deployment_id),
            chunks(),
            content_type="application/zip",
        )

    async def copy_deployment_source_to_path(
        self, app_id: UUID | str, deployment_id: UUID | str, path: Path
    ) -> int:
        size = 0
        with path.open("wb") as destination:
            async for chunk in self._storage.iter_object_chunks(
                self.deployment_source_key(app_id, deployment_id),
                chunk_size=CHUNK_SIZE,
            ):
                destination.write(chunk)
                size += len(chunk)
        return size

    async def read_deployment_source(
        self, app_id: UUID | str, deployment_id: UUID | str
    ) -> bytes:
        async with self._storage.get_client() as s3:
            response = await s3.get_object(
                Bucket=self._bucket,
                Key=self.deployment_source_key(app_id, deployment_id),
            )
            body = response["Body"]
            async with body:
                return await body.read()

    def iter_deployment_source(
        self, app_id: UUID | str, deployment_id: UUID | str
    ) -> AsyncIterator[bytes]:
        """Stream a retained source archive without buffering it in API memory."""
        return self._storage.iter_object_chunks(
            self.deployment_source_key(app_id, deployment_id),
            chunk_size=CHUNK_SIZE,
        )

    async def deployment_source_exists(
        self, app_id: UUID | str, deployment_id: UUID | str
    ) -> bool:
        """Check that a retained source object exists before starting a response."""
        async with self._storage.get_client() as s3:
            try:
                await s3.head_object(
                    Bucket=self._bucket,
                    Key=self.deployment_source_key(app_id, deployment_id),
                )
            except S3Error as exc:
                error = exc.response.get("Error", {})
                if error.get("Code") in {"NoSuchKey", "404", "NotFound"}:
                    return False
                raise
        return True

    async def delete_deployment_source(
        self, app_id: UUID | str, deployment_id: UUID | str
    ) -> None:
        async with self._storage.get_client() as s3:
            await s3.delete_object(
                Bucket=self._bucket,
                Key=self.deployment_source_key(app_id, deployment_id),
            )

    async def delete_application_artifacts(self, app_id: UUID | str) -> None:
        prefix = self.application_prefix(app_id)
        keys: list[str] = []
        async with self._storage.get_client() as s3:
            token = None
            while True:
                kwargs: dict[str, str] = {"Bucket": self._bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                response = await s3.list_objects_v2(**kwargs)
                for obj in response.get("Contents", []):
                    key = obj.get("Key")
                    if key:
                        keys.append(key)
                if not response.get("IsTruncated"):
                    break
                token = response.get("NextContinuationToken")
            for key in keys:
                await s3.delete_object(Bucket=self._bucket, Key=key)
