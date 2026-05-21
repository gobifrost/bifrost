"""
File Backend Abstraction

Provides unified interface for file operations with two backends:
- LocalBackend: Local filesystem (for CLI mode)
- S3Backend: S3 storage (for cloud/platform mode)

S3 key resolution is delegated to `shared.file_paths.resolve_s3_key`.
"""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from shared.file_paths import resolve_s3_key
from src.core.paths import TEMP_PATH, UPLOADS_PATH
from src.services.file_storage import FileStorageService

# Location is now a free string validated by `shared.file_paths`. Keep the type
# alias for callers that want documentation, but allow any string at runtime.
Location = str
_REPO_PREFIX = "_repo/"


def _validate_workspace_path(path: str) -> str:
    """Validate a workspace-relative path and return its normalized form."""
    s3_key = resolve_s3_key("workspace", None, path)
    return s3_key.removeprefix(_REPO_PREFIX)


class FileBackend(ABC):
    """Abstract backend for file operations."""

    @abstractmethod
    async def read(self, path: str, location: Location, scope: str | None = None) -> bytes:
        """Read file content."""
        ...

    @abstractmethod
    async def write(self, path: str, content: bytes, location: Location, updated_by: str = "system", scope: str | None = None) -> None:
        """Write file content."""
        ...

    @abstractmethod
    async def delete(self, path: str, location: Location, scope: str | None = None) -> None:
        """Delete a file."""
        ...

    @abstractmethod
    async def list(self, directory: str, location: Location, scope: str | None = None) -> list[str]:
        """List files in a directory."""
        ...

    @abstractmethod
    async def exists(self, path: str, location: Location, scope: str | None = None) -> bool:
        """Check if a file exists."""
        ...


class LocalBackend(FileBackend):
    """Local filesystem backend for local CLI mode."""

    def __init__(self):
        # Use CWD for workspace - this is where the user's workflow files are
        self.workspace_root = Path.cwd()
        self.temp_root = TEMP_PATH
        self.uploads_root = UPLOADS_PATH

        # Ensure temp directories exist
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.uploads_root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str, location: Location) -> Path:
        """Resolve path to absolute filesystem path with sandboxing.

        Local mode is unscoped — used for CLI development without multi-tenancy.
        """
        if location == "temp":
            base_dir = self.temp_root
        elif location == "uploads":
            base_dir = self.uploads_root
        elif location == "workspace":
            base_dir = self.workspace_root
        else:
            # Freeform local locations are siblings of workspace_root.
            base_dir = self.workspace_root.parent / location

        # Resolve the path
        p = Path(path)
        if not p.is_absolute():
            p = base_dir / p

        try:
            p = p.resolve()
        except Exception as e:
            raise ValueError(f"Invalid path: {path}") from e

        # Sandbox check - ensure path is within the base directory.
        # Use relative_to() rather than str.startswith() to avoid sibling-prefix
        # confusion (e.g., base "/tmp/foo" would otherwise accept "/tmp/foo_evil/x").
        base_resolved = base_dir.resolve()
        try:
            p.relative_to(base_resolved)
        except ValueError as e:
            raise ValueError(f"Path must be within {location} directory: {path}") from e

        return p

    async def read(self, path: str, location: Location, scope: str | None = None) -> bytes:
        """Read file from local filesystem. Scope is ignored in local mode."""
        resolved = self._resolve_path(path, location)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return await asyncio.to_thread(resolved.read_bytes)

    async def write(self, path: str, content: bytes, location: Location, updated_by: str = "system", scope: str | None = None) -> None:
        """Write file to local filesystem. Scope is ignored in local mode."""
        resolved = self._resolve_path(path, location)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(resolved.write_bytes, content)

    async def delete(self, path: str, location: Location, scope: str | None = None) -> None:
        """Delete file from local filesystem. Scope is ignored in local mode."""
        resolved = self._resolve_path(path, location)
        if resolved.exists():
            await asyncio.to_thread(resolved.unlink)

    async def list(self, directory: str, location: Location, scope: str | None = None) -> list[str]:
        """List files in a local directory. Scope is ignored in local mode."""
        resolved = self._resolve_path(directory, location)
        if not resolved.exists():
            return []

        def _list_dir():
            items = []
            for item in resolved.iterdir():
                rel_path = str(item.relative_to(self._resolve_path("", location)))
                if item.is_dir():
                    rel_path += "/"
                items.append(rel_path)
            return sorted(items)

        return await asyncio.to_thread(_list_dir)

    async def exists(self, path: str, location: Location, scope: str | None = None) -> bool:
        """Check if file exists on local filesystem. Scope is ignored in local mode."""
        try:
            resolved = self._resolve_path(path, location)
            return await asyncio.to_thread(resolved.exists)
        except ValueError:
            return False


class S3Backend(FileBackend):
    """S3-based file storage for cloud/platform mode."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = FileStorageService(db)

    async def read(self, path: str, location: Location, scope: str | None = None) -> bytes:
        """Read file from S3."""
        try:
            if location == "workspace":
                # Workspace goes through the indexed read path so file_index stays in sync.
                content, _ = await self.storage.read_file(_validate_workspace_path(path))
                return content
            s3_path = resolve_s3_key(location, scope, path)
            return await self.storage.read_uploaded_file(s3_path)
        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "nosuchkey" in error_msg:
                raise FileNotFoundError(f"File not found: {path}")
            if "bad components" in error_msg or "invalid" in error_msg:
                raise ValueError(f"Invalid path: {path}")
            raise

    async def write(self, path: str, content: bytes, location: Location, updated_by: str = "system", scope: str | None = None) -> None:
        """Write file to S3."""
        if location == "workspace":
            await self.storage.write_file(_validate_workspace_path(path), content, updated_by)
            return
        s3_path = resolve_s3_key(location, scope, path)
        await self.storage.write_raw_to_s3(s3_path, content)

    async def delete(self, path: str, location: Location, scope: str | None = None) -> None:
        """Delete file from S3."""
        if location == "workspace":
            await self.storage.delete_file(_validate_workspace_path(path))
            return
        s3_path = resolve_s3_key(location, scope, path)
        await self.storage.delete_raw_from_s3(s3_path)

    async def list(self, directory: str, location: Location, scope: str | None = None) -> list[str]:
        """List files in S3 directory."""
        if location == "workspace":
            files = await self.storage.list_files(_validate_workspace_path(directory))
            return [f.path for f in files]
        s3_dir = resolve_s3_key(location, scope, directory)
        return await self.storage.list_raw_s3(s3_dir)

    async def exists(self, path: str, location: Location, scope: str | None = None) -> bool:
        """Check if file exists in S3."""
        if location == "workspace":
            s3_path = resolve_s3_key("workspace", None, path)
        else:
            s3_path = resolve_s3_key(location, scope, path)
        return await self.storage.file_exists(s3_path)


def get_backend(mode: str, db: AsyncSession | None = None) -> FileBackend:
    """Get the appropriate file backend based on mode."""
    if mode == "local":
        return LocalBackend()
    if db is None:
        raise ValueError("Database session required for cloud mode")
    return S3Backend(db)
