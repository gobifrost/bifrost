"""S3 storage for immutable builder source revisions.

Each revision is one zip at
``_solution_builder/{solution_id}/revisions/{revision_id}/source.zip``. This
prefix is separate from ``_solution_artifacts/`` (the last deployed install's
source) so builder history is never touched by install writers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.services.repo_storage import _get_shared_session

BUILDER_ROOT = "_solution_builder"
REVISION_ARTIFACT_NAME = "source.zip"
_CHUNK_SIZE = 8 * 1024 * 1024


class SolutionRevisionStorage:
    """S3 storage for one Solution's builder source revisions."""

    def __init__(self, solution_id: UUID | str, settings: Settings | None = None):
        self.solution_id = str(solution_id)
        self.prefix = f"{BUILDER_ROOT}/{self.solution_id}/"
        self._settings = settings or get_settings()
        self._bucket: str = self._settings.s3_bucket or ""

    @asynccontextmanager
    async def _get_client(self):
        session = _get_shared_session()
        async with session.create_client(
            "s3",
            endpoint_url=self._settings.s3_endpoint_url,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            region_name=self._settings.s3_region,
        ) as client:
            yield client

    def _key(self, revision_id: UUID | str) -> str:
        return f"{self.prefix}revisions/{revision_id}/{REVISION_ARTIFACT_NAME}"

    async def write(self, revision_id: UUID | str, data: bytes) -> None:
        async with self._get_client() as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=self._key(revision_id),
                Body=data,
            )

    async def write_from_path(self, revision_id: UUID | str, path: Path) -> None:
        async with self._get_client() as client:
            with path.open("rb") as f:
                await client.put_object(
                    Bucket=self._bucket,
                    Key=self._key(revision_id),
                    Body=f,
                )

    async def read(self, revision_id: UUID | str) -> bytes | None:
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=self._key(revision_id))
                return await response["Body"].read()
            except client.exceptions.NoSuchKey:
                return None
            except Exception as exc:  # noqa: BLE001 - aiobotocore backends vary
                if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc):
                    return None
                raise

    async def copy_to_path(self, revision_id: UUID | str, dest: Path) -> bool:
        """Stream a revision to ``dest``. Returns False when the revision is absent."""
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=self._key(revision_id))
            except client.exceptions.NoSuchKey:
                return False
            except Exception as exc:  # noqa: BLE001 - aiobotocore backends vary
                if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc):
                    return False
                raise

            body = response["Body"]
            async with body:
                with dest.open("wb") as f:
                    while chunk := await body.read(_CHUNK_SIZE):
                        f.write(chunk)
            return True

    async def delete(self, revision_id: UUID | str) -> None:
        async with self._get_client() as client:
            await client.delete_object(Bucket=self._bucket, Key=self._key(revision_id))

    async def delete_all_for_solution(self) -> int:
        """Delete every object under this Solution's builder prefix. Returns the count."""
        deleted = 0
        async with self._get_client() as client:
            continuation_token = None
            while True:
                kwargs = {"Bucket": self._bucket, "Prefix": self.prefix}
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token

                response = await client.list_objects_v2(**kwargs)
                keys = [
                    {"Key": key}
                    for obj in response.get("Contents", [])
                    if (key := obj.get("Key")) is not None
                ]
                if keys:
                    await client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
                    deleted += len(keys)

                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")

        return deleted
