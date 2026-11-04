"""
Repo Sync Writer — regenerate the manifest in S3 _repo/.

When platform entities are created/updated/deleted, this writer
regenerates the split-file manifest under ``.bifrost/`` so the S3
working tree stays in sync with the DB.

Skips silently when S3-compatible storage is not configured.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.services.file_index_service import FileIndexService
from bifrost.manifest import MANIFEST_FILES, MANIFEST_LEGACY_FILE, serialize_manifest_dir
from src.services.legacy_entity_files import (
    LEGACY_ENTITY_SUFFIXES,
    is_legacy_entity_file,
)
from src.services.manifest_generator import generate_manifest
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)


class RepoSyncWriter:
    """Writes the regenerated manifest to S3 _repo/.bifrost/.

    Skips writes silently when S3-compatible storage is not configured.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self._settings = get_settings()
        self._file_index = FileIndexService(db, RepoStorage())

    @property
    def _s3_available(self) -> bool:
        return self._settings.s3_configured

    async def delete_legacy_entity_file(
        self, entity_type: str, entity_id: UUID | str
    ) -> None:
        """Delete the retired companion file for one form or agent."""
        if not self._s3_available:
            return
        directory = f"{entity_type}s"
        suffix = LEGACY_ENTITY_SUFFIXES.get(directory)
        if suffix is None:
            raise ValueError(f"unsupported legacy entity type: {entity_type}")
        normalized_id = str(UUID(str(entity_id)))
        await self._file_index.delete(f"{directory}/{normalized_id}{suffix}")

    async def regenerate_manifest(self) -> None:
        """Generate manifest from DB and write split files to _repo/.bifrost/."""
        if not self._s3_available:
            return
        manifest = await generate_manifest(self.db)
        files = serialize_manifest_dir(manifest)
        for filename, content in files.items():
            await self._file_index.write(
                f".bifrost/{filename}",
                content.encode("utf-8"),
            )
        # Remove split files for now-empty entity types
        for filename in MANIFEST_FILES.values():
            if filename not in files:
                try:
                    await self._file_index.delete(f".bifrost/{filename}")
                except Exception:
                    pass  # File didn't exist
        # Clean up legacy single-file manifest
        try:
            await self._file_index.delete(f".bifrost/{MANIFEST_LEGACY_FILE}")
        except Exception:
            pass  # Already gone
        # Inline manifest content superseded UUID-named form/agent companions.
        # Remove only that historical shape so ordinary user YAML is preserved.
        for directory in LEGACY_ENTITY_SUFFIXES:
            paths = await self._file_index.repo_storage.list(f"{directory}/")
            for path in paths:
                if is_legacy_entity_file(path):
                    await self._file_index.delete(path)
        logger.debug("Regenerated split manifest files in _repo/.bifrost/")
