"""Stream Solution-package previews through object storage without buffering them."""
from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from src.config import Settings, get_settings
from src.services.file_storage.s3_client import S3StorageClient

WORKSPACE_BUNDLE_IMPORTS_ROOT = "_workspace_bundle_imports"
CHUNK_SIZE = 8 * 1024 * 1024


class WorkspaceBundleIntegrityError(ValueError):
    """The staged object no longer has the digest approved in its preview."""


class WorkspaceBundleStorage:
    """Storage for one immutable workspace-import preview.

    The caller persists the preview metadata (requester, expiry and digest); this
    small adapter deliberately owns only byte staging and verification.
    """

    def __init__(self, preview_id: UUID | str, settings: Settings | None = None):
        self.preview_id = str(preview_id)
        self._settings = settings or get_settings()
        self._storage = S3StorageClient(self._settings)
        self.root = f"{WORKSPACE_BUNDLE_IMPORTS_ROOT}/{self.preview_id}"
        self.package_key = f"{self.root}/package.zip"
        self.metadata_key = f"{self.root}/preview.json"

    async def stage_package(self, source: Path) -> tuple[str, int]:
        async def chunks() -> AsyncIterator[bytes]:
            with source.open("rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    yield chunk

        return await self._storage.put_object_from_chunks(
            self.package_key, chunks(), content_type="application/zip"
        )

    async def copy_package_to(self, destination: Path, *, expected_sha256: str) -> int:
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as handle:
            async for chunk in self._storage.iter_object_chunks(
                self.package_key, chunk_size=CHUNK_SIZE
            ):
                digest.update(chunk)
                handle.write(chunk)
                size += len(chunk)
        if digest.hexdigest() != expected_sha256:
            destination.unlink(missing_ok=True)
            raise WorkspaceBundleIntegrityError("staged workspace bundle hash mismatch")
        return size

    async def stage_metadata(self, metadata: dict) -> None:
        """Store compact, requester-bound preview metadata beside the archive."""
        data = json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode("utf-8")

        async def chunks() -> AsyncIterator[bytes]:
            yield data

        await self._storage.put_object_from_chunks(
            self.metadata_key, chunks(), content_type="application/json"
        )

    async def load_metadata(self) -> dict:
        data = await self._storage.read_uploaded_file(self.metadata_key)
        return json.loads(data)

    async def delete(self) -> None:
        """Best-effort terminal cleanup of this preview's isolated staging prefix."""
        try:
            async with self._storage.get_client() as client:
                token = None
                while True:
                    response = await client.list_objects_v2(Bucket=self._settings.s3_bucket, Prefix=self.root + "/", **({"ContinuationToken": token} if token else {}))
                    keys = [{"Key": key} for obj in response.get("Contents", []) if (key := obj.get("Key")) is not None]
                    if keys:
                        await client.delete_objects(Bucket=self._settings.s3_bucket, Delete={"Objects": keys})
                    if not response.get("IsTruncated"):
                        break
                    token = response.get("NextContinuationToken")
        except Exception:
            # Retention cleanup can reap a failed best-effort deletion later.
            return


async def cleanup_expired_workspace_bundle_previews(*, now: datetime | None = None) -> int:
    """Reap expired staged previews, including never-enqueued and failed jobs."""
    settings = get_settings()
    storage = S3StorageClient(settings)
    now = now or datetime.now(timezone.utc)
    preview_ids: set[str] = set()
    async with storage.get_client() as client:
        token = None
        while True:
            response = await client.list_objects_v2(Bucket=settings.s3_bucket, Prefix=WORKSPACE_BUNDLE_IMPORTS_ROOT + "/", **({"ContinuationToken": token} if token else {}))
            for obj in response.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith("/preview.json"):
                    preview_ids.add(key.split("/")[1])
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
    removed = 0
    for preview_id in preview_ids:
        preview = WorkspaceBundleStorage(preview_id, settings)
        try:
            metadata = await preview.load_metadata()
            job_id = metadata.get("platform_job_id")
            if job_id:
                from uuid import UUID
                from src.core.database import get_db_context
                from src.models.orm.platform_jobs import PlatformJob

                async with get_db_context() as db:
                    job = await db.get(PlatformJob, UUID(job_id))
                if job is not None and job.status in {
                    "queued", "running", "waiting", "cancel_requested",
                }:
                    continue
            if datetime.fromisoformat(metadata["expires_at"]) <= now:
                await preview.delete()
                removed += 1
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            await preview.delete()
            removed += 1
    return removed
