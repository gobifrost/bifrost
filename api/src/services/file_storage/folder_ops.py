"""
Folder Operations Service for File Storage.

Handles folder creation, listing, and bulk operations.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.models.orm.file_index import FileIndex

logger = logging.getLogger(__name__)


@dataclass
class FileEntry:
    """Lightweight file/folder entry for listings (replaces WorkspaceFile)."""

    path: str
    content_hash: str = ""
    size_bytes: int = 0
    content_type: str = "text/plain"
    is_deleted: bool = False
    entity_type: str | None = None
    entity_id: str | None = None
    updated_at: datetime | None = None


class FolderOperationsService:
    """Service for folder and bulk file operations."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        s3_client,
        remove_metadata_fn,
        write_file_fn,
    ):
        """
        Initialize folder operations service.

        Args:
            db: Database session
            settings: Application settings
            s3_client: S3 client context manager
            remove_metadata_fn: Function to remove file metadata
            write_file_fn: Function to write individual files
        """
        self.db = db
        self.settings = settings
        self._s3_client = s3_client
        self._remove_metadata = remove_metadata_fn
        self._write_file = write_file_fn

    async def create_folder(
        self,
        path: str,
        updated_by: str = "system",
    ) -> None:
        """Create a folder by writing a .gitkeep placeholder in S3."""
        from src.services.file_index_service import FileIndexService

        clean_path = path.strip("/")
        gitkeep_path = f"{clean_path}/.gitkeep"
        await FileIndexService(self.db).write(gitkeep_path, b"", updated_by=updated_by)

    async def list_files(
        self,
        directory: str = "",
        include_deleted: bool = False,
        recursive: bool = False,
    ) -> list[FileEntry]:
        """
        List files and folders in a directory.

        Queries file_index for code files and synthesizes folder entries.

        Args:
            directory: Directory path (empty for root)
            include_deleted: Ignored (file_index has no soft delete)
            recursive: If True, return all files under directory

        Returns:
            List of FileEntry records (files and folders)
        """
        from src.services.editor.file_filter import is_excluded_path

        prefix = directory.rstrip("/") + "/" if directory else ""

        # Query file_index for all files under this prefix
        stmt = select(FileIndex).order_by(FileIndex.path)
        if prefix:
            stmt = stmt.where(FileIndex.path.startswith(prefix))

        result = await self.db.execute(stmt)
        all_entries = list(result.scalars().all())

        # Convert to FileEntry
        all_files = [
            FileEntry(
                path=fi.path,
                content_hash=fi.content_hash or "",
                size_bytes=len(fi.content.encode("utf-8")) if fi.content else 0,
                content_type="inode/directory" if fi.path.endswith("/") else "text/plain",
                updated_at=fi.updated_at,
            )
            for fi in all_entries
        ]

        if recursive:
            return [
                f for f in all_files
                if not is_excluded_path(f.path) and not f.path.endswith("/")
            ]

        # Synthesize direct children
        direct_children: dict[str, FileEntry] = {}
        seen_folders: set[str] = set()

        for file in all_files:
            if is_excluded_path(file.path):
                continue

            relative_path = file.path[len(prefix):] if prefix else file.path
            if not relative_path:
                continue

            slash_idx = relative_path.find("/")

            if slash_idx == -1:
                direct_children[file.path] = file
            elif slash_idx == len(relative_path) - 1:
                folder_name = relative_path.rstrip("/")
                direct_children[file.path] = file
                seen_folders.add(folder_name)
            else:
                folder_name = relative_path[:slash_idx]
                folder_path = f"{prefix}{folder_name}/"

                if folder_name not in seen_folders:
                    seen_folders.add(folder_name)
                    if folder_path not in direct_children:
                        direct_children[folder_path] = FileEntry(
                            path=folder_path,
                            content_type="inode/directory",
                        )

        return sorted(direct_children.values(), key=lambda f: f.path)

    async def upload_from_directory(
        self,
        local_path: Path,
        updated_by: str = "system",
    ) -> int:
        """
        Upload all files from local directory to workspace.

        Used for git sync operations.

        Args:
            local_path: Local directory to upload from
            updated_by: User who made the change

        Returns:
            Number of files uploaded
        """
        count = 0

        for file_path in local_path.rglob("*"):
            if file_path.is_file():
                # Skip git metadata
                if ".git" in file_path.parts:
                    continue

                rel_path = str(file_path.relative_to(local_path))
                content = file_path.read_bytes()

                await self._write_file(rel_path, content, updated_by)
                count += 1

        logger.info(f"Uploaded {count} files from {local_path}")
        return count
