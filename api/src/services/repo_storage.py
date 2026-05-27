"""
Repo Storage Service — S3 operations scoped to _repo/ prefix.

All paths are relative to _repo/. Callers pass "workflows/test.py"
and this service reads/writes "_repo/workflows/test.py" in S3.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

REPO_PREFIX = "_repo/"


@dataclass
class S3FileMetadata:
    """Metadata for a file stored in S3."""

    etag: str
    last_modified: datetime


class RepoStorage:
    """Object storage scoped to _repo/ prefix."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        if self._settings.object_storage_provider == "azure_blob":
            from src.services.file_storage.azure_blob_client import (
                AzureBlobStorageClient,
            )

            self._storage = AzureBlobStorageClient(self._settings)
            self._bucket = self._settings.azure_blob_container or ""
        else:
            from src.services.file_storage.s3_client import S3StorageClient

            self._storage = S3StorageClient(self._settings)
            self._bucket = self._settings.s3_bucket or ""

    def _get_client(self):
        return self._storage.get_client()

    def _repo_key(self, path: str) -> str:
        """Convert relative path to S3 key with _repo/ prefix."""
        return f"{REPO_PREFIX}{path.lstrip('/')}"

    async def read(self, path: str) -> bytes:
        """Read a file from _repo/."""
        async with self._get_client() as client:
            return await self._read_from_s3(client, path)

    async def _read_from_s3(self, client, path: str) -> bytes:
        key = self._repo_key(path)
        response = await client.get_object(Bucket=self._bucket, Key=key)
        return await response["Body"].read()

    async def write(self, path: str, content: bytes) -> str:
        """Write a file to _repo/. Returns content hash."""
        async with self._get_client() as client:
            return await self._write_to_s3(client, path, content)

    async def _write_to_s3(self, client, path: str, content: bytes) -> str:
        key = self._repo_key(path)
        content_hash = hashlib.sha256(content).hexdigest()
        await client.put_object(Bucket=self._bucket, Key=key, Body=content)
        return content_hash

    async def delete(self, path: str) -> None:
        """Delete a file from _repo/."""
        async with self._get_client() as client:
            key = self._repo_key(path)
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def list(self, prefix: str = "") -> list[str]:
        """List files in _repo/ with optional sub-prefix. Returns relative paths."""
        async with self._get_client() as client:
            return await self._list_from_s3(client, prefix)

    async def list_with_metadata(self, prefix: str = "") -> dict[str, S3FileMetadata]:
        """List files in _repo/ with metadata. Returns {relative_path: S3FileMetadata}."""
        async with self._get_client() as client:
            return await self._list_with_metadata_from_s3(client, prefix)

    async def _list_with_metadata_from_s3(
        self, client, prefix: str = ""
    ) -> dict[str, S3FileMetadata]:
        full_prefix = self._repo_key(prefix)
        result: dict[str, S3FileMetadata] = {}
        continuation_token = None

        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": full_prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            response = await client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                rel_path = obj["Key"][len(REPO_PREFIX) :]
                # S3 ETags are quoted — strip quotes for clean comparison
                etag = obj["ETag"].strip('"')
                last_modified = obj["LastModified"]
                result[rel_path] = S3FileMetadata(
                    etag=etag, last_modified=last_modified
                )

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return result

    async def _list_from_s3(self, client, prefix: str = "") -> list[str]:
        full_prefix = self._repo_key(prefix)
        paths: list[str] = []
        continuation_token = None

        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": full_prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            response = await client.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                # Strip _repo/ prefix from key
                rel_path = obj["Key"][len(REPO_PREFIX) :]
                paths.append(rel_path)

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return paths

    async def list_directory(
        self,
        prefix: str = "",
        exclude_fn: Callable[[str], bool] | None = None,
    ) -> tuple[list[str], list[str]]:
        """List direct children of a directory in _repo/.

        Returns (files, folders) where:
        - files: full relative paths of direct child files
        - folders: full relative paths of direct child directories (with trailing /)

        Uses S3 Delimiter='/' for efficient non-recursive listing that scales
        with the number of direct children, not total descendants.

        Args:
            prefix: Directory prefix (e.g. "apps/myapp/"). Empty string for root.
            exclude_fn: Optional filter function(path) -> bool. If True, path is excluded.
        """
        from src.services.editor.file_filter import is_excluded_path

        filter_fn = exclude_fn or is_excluded_path

        async with self._get_client() as client:
            raw_files, raw_folders = await self._list_directory_from_s3(client, prefix)

        files = [f for f in raw_files if not filter_fn(f)]
        folders = [d for d in raw_folders if not filter_fn(d.rstrip("/"))]

        return sorted(files), sorted(folders)

    async def _list_directory_from_s3(
        self, client, prefix: str = ""
    ) -> tuple[list[str], list[str]]:
        """Non-recursive S3 listing using Delimiter='/'.

        Returns (files, folders) as relative paths under _repo/.
        """
        full_prefix = self._repo_key(prefix)
        files: list[str] = []
        folders: list[str] = []
        continuation_token = None

        while True:
            kwargs: dict = {
                "Bucket": self._bucket,
                "Prefix": full_prefix,
                "Delimiter": "/",
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            response = await client.list_objects_v2(**kwargs)

            # Direct child files
            for obj in response.get("Contents", []):
                rel_path = obj["Key"][len(REPO_PREFIX) :]
                if rel_path:
                    files.append(rel_path)

            # Direct child folders (CommonPrefixes)
            for prefix_obj in response.get("CommonPrefixes", []):
                folder_path = prefix_obj["Prefix"][len(REPO_PREFIX) :]
                if folder_path:
                    folders.append(folder_path)

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return files, folders

    async def exists(self, path: str) -> bool:
        """Check if a file exists in _repo/."""
        try:
            async with self._get_client() as client:
                key = self._repo_key(path)
                await client.head_object(Bucket=self._bucket, Key=key)
                return True
        except Exception:
            return False

    @staticmethod
    def compute_hash(content: bytes) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content).hexdigest()
