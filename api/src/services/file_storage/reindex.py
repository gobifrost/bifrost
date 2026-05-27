"""
Workspace Reindexing Service for File Storage.

Handles syncing workspace indexes from S3, reindexing from local filesystem,
and smart reindexing with reference validation.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.models.orm.file_index import FileIndex

logger = logging.getLogger(__name__)


class WorkspaceReindexService:
    """Service for reindexing workspace files from S3 or filesystem."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        s3_client,
        entity_resolution,
        file_hash_fn: Callable[[bytes], str],
        content_type_fn: Callable[[str], str],
        extract_metadata_fn,
        index_python_file_fn,
    ):
        """
        Initialize reindex service.

        Args:
            db: Database session
            settings: Application settings
            s3_client: S3 client context manager
            entity_resolution: EntityResolutionService instance
            file_hash_fn: Function to compute content hash
            content_type_fn: Function to guess content type
            extract_metadata_fn: Function to extract metadata from files
            index_python_file_fn: Function to index Python files
        """
        self.db = db
        self.settings = settings
        self._s3_client = s3_client
        self._entity_resolution = entity_resolution
        self._compute_hash = file_hash_fn
        self._guess_content_type = content_type_fn
        self._extract_metadata = extract_metadata_fn
        self._index_python_file = index_python_file_fn

    async def sync_index_from_s3(self) -> int:
        """
        Sync index from S3 bucket contents.

        Used for initial setup or recovery. Scans S3 bucket and
        creates file_index entries for all files.

        Returns:
            Number of files indexed
        """
        if self.settings.object_storage_provider == "azure_blob":
            if not self.settings.azure_blob_configured:
                raise RuntimeError("Azure Blob storage not configured")
        elif not self.settings.s3_configured:
            raise RuntimeError("S3 storage not configured")

        count = 0
        async with self._s3_client.get_client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.settings.s3_bucket):
                for obj in page.get("Contents", []):
                    key = obj.get("Key")
                    if not key:
                        continue

                    # Get content for hash
                    response = await s3.get_object(
                        Bucket=self.settings.s3_bucket,
                        Key=key,
                    )
                    content = await response["Body"].read()
                    content_hash = self._compute_hash(content)
                    content_str = content.decode("utf-8", errors="replace")

                    # Upsert file_index
                    now = datetime.now(timezone.utc)
                    stmt = (
                        insert(FileIndex)
                        .values(
                            path=key,
                            content=content_str,
                            content_hash=content_hash,
                            updated_at=now,
                        )
                        .on_conflict_do_update(
                            index_elements=[FileIndex.path],
                            set_={
                                "content": content_str,
                                "content_hash": content_hash,
                                "updated_at": now,
                            },
                        )
                    )
                    await self.db.execute(stmt)

                    # Extract metadata
                    await self._extract_metadata(key, content)
                    count += 1

        logger.info(f"Indexed {count} files from S3")
        return count

    async def reindex_workspace_files(
        self, local_path: Path
    ) -> dict[str, int | list[str]]:
        """
        Reindex file_index table from local filesystem.

        Ensures DB matches actual files on disk.
        Also reconciles orphaned workflows/data_providers.

        Args:
            local_path: Local workspace directory (e.g., /tmp/bifrost/workspace)

        Returns:
            Dict with counts: files_indexed, files_removed, workflows_deactivated,
            data_providers_deactivated
        """
        from src.models import Workflow
        from src.services.editor.file_filter import is_excluded_path

        counts: dict[str, int | list[str]] = {
            "files_indexed": 0,
            "files_removed": 0,
            "workflows_deactivated": 0,
            "data_providers_deactivated": 0,
        }

        # 1. Collect all file paths from local filesystem
        existing_paths: set[str] = set()
        for file_path in local_path.rglob("*"):
            if file_path.is_file():
                rel_path = str(file_path.relative_to(local_path))
                if not is_excluded_path(rel_path):
                    existing_paths.add(rel_path)

        # 2. Remove orphaned file_index entries (files no longer on disk)
        if existing_paths:
            del_stmt = delete(FileIndex).where(
                ~FileIndex.path.in_(existing_paths),
                ~FileIndex.path.endswith("/"),  # Skip folder markers
            )
        else:
            del_stmt = delete(FileIndex).where(
                ~FileIndex.path.endswith("/"),
            )
        result = await self.db.execute(del_stmt)
        counts["files_removed"] = result.rowcount if result.rowcount > 0 else 0

        # 3. For each existing file, upsert into file_index
        # Process Python files first to make decorator metadata available before
        # any text content that might reference them.
        py_files = sorted([p for p in existing_paths if p.endswith(".py")])
        other_files = sorted([p for p in existing_paths if not p.endswith(".py")])
        ordered_paths = py_files + other_files

        now = datetime.now(timezone.utc)

        for rel_path in ordered_paths:
            file_path = local_path / rel_path
            try:
                content = file_path.read_bytes()
            except OSError as e:
                logger.warning(f"Failed to read {rel_path}: {e}")
                continue

            content_hash = self._compute_hash(content)
            content_str = content.decode("utf-8", errors="replace")

            # Upsert file_index record
            stmt = (
                insert(FileIndex)
                .values(
                    path=rel_path,
                    content=content_str,
                    content_hash=content_hash,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[FileIndex.path],
                    set_={
                        "content": content_str,
                        "content_hash": content_hash,
                        "updated_at": now,
                    },
                )
            )
            await self.db.execute(stmt)

            # Extract metadata (workflows/data_providers)
            await self._extract_metadata(rel_path, content)

            counts["files_indexed"] += 1

        # 4. Clean up endpoints for orphaned endpoint-enabled workflows
        result = await self.db.execute(
            select(Workflow).where(
                Workflow.is_active == True,  # noqa: E712
                Workflow.endpoint_enabled == True,  # noqa: E712
                ~Workflow.path.in_(existing_paths) if existing_paths else True,
            )
        )
        orphaned_endpoint_workflows = result.scalars().all()

        for workflow in orphaned_endpoint_workflows:
            try:
                from src.services.openapi_endpoints import remove_workflow_endpoint
                from src.main import app

                remove_workflow_endpoint(app, workflow.name)
            except Exception as e:
                logger.warning(
                    f"Failed to remove endpoint for orphaned workflow {workflow.name}: {e}"
                )

        # 5. Mark orphaned workflows as inactive
        stmt = (
            update(Workflow)
            .where(
                Workflow.is_active == True,  # noqa: E712
                ~Workflow.path.in_(existing_paths) if existing_paths else True,
            )
            .values(is_active=False)
        )
        result = await self.db.execute(stmt)
        counts["workflows_deactivated"] = result.rowcount if result.rowcount > 0 else 0

        counts["data_providers_deactivated"] = 0

        if any(counts.values()):
            logger.info(f"Reindexed workspace: {counts}")

        return counts
