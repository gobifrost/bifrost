"""
Solution Storage Service — S3 operations scoped to ``_solutions/{solution_id}/``.

Mirrors :class:`src.services.repo_storage.RepoStorage` but every key is prefixed
with the install's ``solution_id``. Callers pass relative paths
("workflows/triage.py") and this service reads/writes
"_solutions/{solution_id}/workflows/triage.py" in S3.

Two installs of the same Solution definition (and the ad-hoc ``_repo/``
workspace) therefore never collide — this is the storage half of the
"self-contained worlds" guarantee (success-criteria §3.5/§3.6). Python source
installs here and is *executed as source* by the virtual importer; React app
``src/`` is NOT installed here (only built ``dist/`` goes to ``_apps/``).
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from src.config import Settings, get_settings
from src.services.repo_storage import _get_shared_session

logger = logging.getLogger(__name__)

SOLUTIONS_ROOT = "_solutions"


class SolutionStorage:
    """S3 storage scoped to ``_solutions/{solution_id}/`` prefix."""

    def __init__(self, solution_id: UUID | str, settings: Settings | None = None):
        self.solution_id = str(solution_id)
        self.prefix = f"{SOLUTIONS_ROOT}/{self.solution_id}/"
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

    def _key(self, path: str) -> str:
        """Convert a relative path to an S3 key under this install's prefix."""
        return f"{self.prefix}{path.lstrip('/')}"

    async def write(self, path: str, content: bytes) -> str:
        """Write a file to this install's prefix. Returns SHA-256 content hash."""
        async with self._get_client() as client:
            key = self._key(path)
            content_hash = hashlib.sha256(content).hexdigest()
            await client.put_object(Bucket=self._bucket, Key=key, Body=content)
            return content_hash

    async def delete(self, path: str) -> None:
        """Delete a file from this install's prefix."""
        async with self._get_client() as client:
            key = self._key(path)
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def list(self, prefix: str = "") -> list[str]:
        """List files under this install (optional sub-prefix). Returns relative paths."""
        async with self._get_client() as client:
            return await self._list_from_s3(client, prefix)

    async def _list_from_s3(self, client, prefix: str = "") -> list[str]:
        full_prefix = self._key(prefix)
        strip = len(self.prefix)
        paths: list[str] = []
        continuation_token = None

        while True:
            kwargs: dict = {"Bucket": self._bucket, "Prefix": full_prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            response = await client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                paths.append(obj["Key"][strip:])

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return paths
