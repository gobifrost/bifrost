"""Stream Solution-package previews through object storage without buffering them."""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
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
