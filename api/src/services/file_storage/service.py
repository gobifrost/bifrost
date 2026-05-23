"""
FileStorageService - Main facade for file storage operations.

This service composes all sub-services and provides the same public API
as the original monolithic FileStorageService.
"""

import ast
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from .models import (
    WriteResult,
    WorkflowIdConflictInfo,
    FileDiagnosticInfo,
    PendingDeactivationInfo,
    AvailableReplacementInfo,
)
from .azure_blob_client import AzureBlobStorageClient
from .s3_client import S3StorageClient
from .entity_detector import detect_platform_entity_type
from .ast_parser import ASTMetadataParser
from .diagnostics import DiagnosticsService
from .entity_resolution import EntityResolutionService
from .deactivation import DeactivationProtectionService
from .file_ops import FileOperationsService
from .folder_ops import FolderOperationsService
from .reindex import WorkspaceReindexService
from .indexers import WorkflowIndexer

logger = logging.getLogger(__name__)


class FileStorageService:
    """
    Main file storage service facade.

    Composes all sub-services and delegates method calls appropriately.
    Maintains the exact same public API as the original FileStorageService.
    """

    def __init__(self, db: AsyncSession, settings: Settings | None = None):
        """
        Initialize file storage service with all sub-services.

        Args:
            db: Database session
            settings: Application settings (defaults to global settings)
        """
        self.db = db
        self.settings = settings or get_settings()

        # Initialize object storage client wrapper
        if self.settings.object_storage_provider == "azure_blob":
            self._s3_storage = AzureBlobStorageClient(self.settings)
        else:
            self._s3_storage = S3StorageClient(self.settings)

        # Initialize helper services
        self._ast_parser = ASTMetadataParser()
        self._diagnostics = DiagnosticsService(db)
        self._entity_resolution = EntityResolutionService(db)
        self._deactivation = DeactivationProtectionService(db)

        # Initialize indexers
        self._workflow_indexer = WorkflowIndexer(db)

        # Initialize operation services with dependencies
        self._file_ops = FileOperationsService(
            db=db,
            settings=self.settings,
            s3_client=self._s3_storage,
            diagnostics=self._diagnostics,
            deactivation=self._deactivation,
            file_hash_fn=S3StorageClient.compute_hash,
            content_type_fn=S3StorageClient.guess_content_type,
            platform_entity_detector_fn=detect_platform_entity_type,
            extract_metadata_fn=self._extract_metadata_full,  # Full version for write ops
            remove_metadata_fn=self._remove_metadata,
        )

        self._folder_ops = FolderOperationsService(
            db=db,
            settings=self.settings,
            s3_client=self._s3_storage,
            remove_metadata_fn=self._remove_metadata,
            write_file_fn=self._file_ops.write_file,
        )

        self._reindex_service = WorkspaceReindexService(
            db=db,
            settings=self.settings,
            s3_client=self._s3_storage,
            entity_resolution=self._entity_resolution,
            file_hash_fn=S3StorageClient.compute_hash,
            content_type_fn=S3StorageClient.guess_content_type,
            extract_metadata_fn=self._extract_metadata,
            index_python_file_fn=self._workflow_indexer.index_python_file,
        )

    # ========================================================================
    # Core File Operations (delegate to FileOperationsService)
    # ========================================================================

    async def read_file(self, path: str) -> tuple[bytes, None]:
        """Read file content."""
        return await self._file_ops.read_file(path)

    async def write_file(
        self,
        path: str,
        content: bytes,
        updated_by: str = "system",
        force_deactivation: bool = False,
        replacements: dict[str, str] | None = None,
        workflows_to_deactivate: list[str] | None = None,
        skip_dirty_flag: bool = False,
    ) -> WriteResult:
        """Write file content to storage and update index."""
        return await self._file_ops.write_file(
            path=path,
            content=content,
            updated_by=updated_by,
            force_deactivation=force_deactivation,
            replacements=replacements,
            workflows_to_deactivate=workflows_to_deactivate,
            skip_dirty_flag=skip_dirty_flag,
        )

    async def delete_file(self, path: str) -> None:
        """Delete a file from storage."""
        await self._file_ops.delete_file(path)

    async def move_file(self, old_path: str, new_path: str) -> None:
        """Move/rename a file."""
        await self._file_ops.move_file(old_path, new_path)

    # ========================================================================
    # Folder Operations (delegate to FolderOperationsService)
    # ========================================================================

    async def create_folder(
        self,
        path: str,
        updated_by: str = "system",
    ) -> None:
        """Create a folder record."""
        await self._folder_ops.create_folder(path, updated_by)

    async def list_files(
        self,
        directory: str = "",
        include_deleted: bool = False,
        recursive: bool = False,
    ) -> list:
        """List files and folders in a directory."""
        return await self._folder_ops.list_files(
            directory=directory,
            include_deleted=include_deleted,
            recursive=recursive,
        )

    async def upload_from_directory(
        self,
        local_path: Path,
        updated_by: str = "system",
    ) -> int:
        """Upload files from local directory to workspace."""
        return await self._folder_ops.upload_from_directory(
            local_path=local_path,
            updated_by=updated_by,
        )

    # ========================================================================
    # Reindexing Operations (delegate to WorkspaceReindexService)
    # ========================================================================

    async def sync_index_from_s3(self) -> int:
        """Sync index from S3 bucket contents."""
        return await self._reindex_service.sync_index_from_s3()

    async def reindex_workspace_files(
        self,
        local_path: Path,
    ) -> dict[str, int | list[str]]:
        """Reindex workspace files from local filesystem."""
        return await self._reindex_service.reindex_workspace_files(
            local_path=local_path,
        )

    # ========================================================================
    # S3 Direct Operations (delegate to S3StorageClient)
    # ========================================================================

    async def generate_presigned_upload_url(
        self,
        path: str,
        content_type: str,
        expires_in: int = 600,
    ) -> str:
        """Generate a presigned PUT URL for direct S3 upload."""
        return await self._s3_storage.generate_presigned_upload_url(
            path=path,
            content_type=content_type,
            expires_in=expires_in,
        )

    def presigned_upload_headers(self, content_type: str) -> dict[str, str]:
        """Return headers clients must send with a presigned upload URL."""
        return self._s3_storage.presigned_upload_headers(content_type)

    async def generate_presigned_download_url(
        self,
        path: str,
        expires_in: int = 600,
    ) -> str:
        """Generate a presigned GET URL for direct S3 download."""
        return await self._s3_storage.generate_presigned_download_url(
            path=path,
            expires_in=expires_in,
        )

    async def read_uploaded_file(self, path: str) -> bytes:
        """Read a file from S3 (for uploaded files)."""
        return await self._s3_storage.read_uploaded_file(path)

    async def write_raw_to_s3(self, path: str, content: bytes) -> None:
        """Write content directly to S3 without workspace indexing."""
        async with self._s3_storage.get_client() as s3:
            await s3.put_object(
                Bucket=self.settings.s3_bucket,
                Key=path,
                Body=content,
                ContentType=S3StorageClient.guess_content_type(path),
            )

    async def delete_raw_from_s3(self, path: str) -> None:
        """Delete a file directly from S3 without workspace indexing."""
        async with self._s3_storage.get_client() as s3:
            try:
                await s3.delete_object(
                    Bucket=self.settings.s3_bucket,
                    Key=path,
                )
            except Exception:
                pass  # Ignore errors for idempotency

    async def list_raw_s3(self, prefix: str) -> list[str]:
        """List files in S3 with given prefix (raw, without indexing)."""
        keys = []
        async with self._s3_storage.get_client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self.settings.s3_bucket,
                Prefix=prefix,
            ):
                for obj in page.get("Contents", []):
                    key = obj.get("Key")
                    if key:
                        keys.append(key)
        return keys

    async def file_exists(self, path: str) -> bool:
        """Check if a file exists in S3."""
        async with self._s3_storage.get_client() as s3:
            try:
                await s3.head_object(
                    Bucket=self.settings.s3_bucket,
                    Key=path,
                )
                return True
            except s3.exceptions.NoSuchKey:
                return False
            except Exception:
                return False

    # ========================================================================
    # Internal Helper Methods (used by sub-services)
    # ========================================================================

    async def _extract_metadata_full(
        self,
        path: str,
        content: bytes,
        force_deactivation: bool = False,
        replacements: dict[str, str] | None = None,
        cached_ast: "ast.Module | None" = None,
        cached_content_str: str | None = None,
        workflows_to_deactivate: list[str] | None = None,
    ) -> tuple[
        bytes,
        bool,
        bool,
        list[WorkflowIdConflictInfo] | None,
        list[FileDiagnosticInfo],
        list[PendingDeactivationInfo] | None,
        list[AvailableReplacementInfo] | None,
    ]:
        """
        Extract metadata from file with full deactivation protection (for write ops).

        Args:
            path: File path
            content: Raw file content bytes
            force_deactivation: Skip deactivation protection
            replacements: Workflow ID replacement map
            cached_ast: Pre-parsed AST tree (avoids re-parsing large files)
            cached_content_str: Pre-decoded content string (avoids re-decoding)

        Returns:
            Tuple of (final_content, content_modified, needs_indexing, conflicts, diagnostics,
                      pending_deactivations, available_replacements)
        """
        try:
            if path.endswith(".py"):
                # For modules without decorators, skip indexing entirely
                # The has_decorators flag is set by detect_python_entity_type_with_ast
                # and indicates whether we need to process this file for workflows
                if cached_ast is None and cached_content_str is not None:
                    # No AST means no decorators were found - skip indexing
                    # Still need to run deactivation check in case file previously had workflows
                    if workflows_to_deactivate:
                        await self._deactivation.deactivate_workflows_by_id(
                            workflows_to_deactivate
                        )
                    if force_deactivation:
                        await self._deactivation.deactivate_removed_workflows(
                            path, set()
                        )
                    else:
                        (
                            pending,
                            available,
                        ) = await self._deactivation.detect_pending_deactivations(
                            path=path,
                            new_function_names=set(),
                            new_decorator_info={},
                        )
                        if pending:
                            return content, False, False, None, [], pending, available
                    logger.info(
                        f"Skipping indexing for module without decorators: {path}"
                    )
                    return content, False, False, None, [], None, None

                # Python files with decorators: do deactivation check then index
                return await self._index_python_file_full(
                    path,
                    content,
                    force_deactivation,
                    replacements,
                    cached_ast=cached_ast,
                    cached_content_str=cached_content_str,
                    workflows_to_deactivate=workflows_to_deactivate,
                )
        except Exception as e:
            logger.warning(f"Failed to extract metadata from {path}: {e}")

        return content, False, False, None, [], None, None

    async def _index_python_file_full(
        self,
        path: str,
        content: bytes,
        force_deactivation: bool = False,
        replacements: dict[str, str] | None = None,
        cached_ast: ast.Module | None = None,
        cached_content_str: str | None = None,
        workflows_to_deactivate: list[str] | None = None,
    ) -> tuple[
        bytes,
        bool,
        bool,
        list[WorkflowIdConflictInfo] | None,
        list[FileDiagnosticInfo],
        list[PendingDeactivationInfo] | None,
        list[AvailableReplacementInfo] | None,
    ]:
        """
        Index Python file with deactivation protection.

        For write operations, checks if workflows would be deactivated
        before actually indexing.

        Args:
            path: File path
            content: Raw file content bytes
            force_deactivation: Skip deactivation protection
            replacements: Workflow ID replacement map
            cached_ast: Pre-parsed AST tree (avoids re-parsing large files)
            cached_content_str: Pre-decoded content string (avoids re-decoding)
        """
        diagnostics: list[FileDiagnosticInfo] = []

        # Use cached content string if available (avoids re-decoding 4MB files)
        content_str = cached_content_str or content.decode("utf-8", errors="replace")

        # Use cached AST if available (avoids re-parsing - saves ~100MB for 4MB files)
        tree = cached_ast
        if tree is None:
            try:
                tree = ast.parse(content_str, filename=path)
            except SyntaxError as e:
                logger.warning(f"Syntax error parsing {path}: {e}")
                diagnostics.append(
                    FileDiagnosticInfo(
                        severity="error",
                        message=f"Syntax error: {e.msg}" if e.msg else str(e),
                        line=e.lineno,
                        column=e.offset,
                        source="syntax",
                    )
                )
                return content, False, False, None, diagnostics, None, None

        # Pre-scan: collect all decorated function names and their info
        new_function_names: set[str] = set()
        new_decorator_info: dict[str, tuple[str, str]] = {}

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                decorator_info = self._ast_parser.parse_decorator(decorator)
                if not decorator_info:
                    continue
                decorator_name, kwargs = decorator_info
                if decorator_name in ("workflow", "tool", "data_provider"):
                    func_name = node.name
                    new_function_names.add(func_name)
                    display_name = kwargs.get("name") or func_name
                    # Map decorator type
                    if decorator_name == "tool":
                        dtype = "tool"
                    elif decorator_name == "data_provider":
                        dtype = "data_provider"
                    else:
                        dtype = "workflow"
                    new_decorator_info[func_name] = (dtype, display_name)

        # Apply replacements first if provided
        if replacements:
            await self._deactivation.apply_workflow_replacements(replacements)

        # Selectively deactivate specific workflows if requested
        if workflows_to_deactivate:
            await self._deactivation.deactivate_workflows_by_id(workflows_to_deactivate)

        # Check for pending deactivations (always check, even if no new functions)
        pending_deactivations: list[PendingDeactivationInfo] | None = None
        available_replacements: list[AvailableReplacementInfo] | None = None

        if not force_deactivation:
            pending, available = await self._deactivation.detect_pending_deactivations(
                path=path,
                new_function_names=new_function_names,
                new_decorator_info=new_decorator_info,
            )
            if pending:
                pending_deactivations = pending
                available_replacements = available
                # Return early without indexing - caller will raise 409
                return (
                    content,
                    False,
                    False,
                    None,
                    [],
                    pending_deactivations,
                    available_replacements,
                )
        else:
            # Force deactivation: deactivate workflows that are no longer in the file
            await self._deactivation.deactivate_removed_workflows(
                path, new_function_names
            )

        # No deactivation issues - proceed with indexing
        # Pass cached AST and content string to avoid re-parsing
        await self._workflow_indexer.index_python_file(
            path, content, cached_ast=tree, cached_content_str=content_str
        )

        return content, False, False, None, [], None, None

    async def _extract_metadata(
        self,
        path: str,
        content: bytes,
        entity_type: str | None = None,
    ):
        """
        Extract and index metadata from a file (simplified for reindex).

        Routes to the workflow indexer for Python files. Form and agent
        content is no longer driven by per-file YAML — it is imported from
        the manifest by ``manifest_import``.
        """
        if entity_type is None and path.endswith(".py"):
            entity_type = "workflow"

        if entity_type == "workflow":
            await self._workflow_indexer.index_python_file(path, content)

    async def _remove_metadata(self, path: str):
        """
        Remove metadata for a file.

        Clears diagnostic notifications and deletes workflow records associated
        with deleted Python files. Form and agent records are managed via the
        manifest, not per-file YAML.
        """
        # Clear diagnostic notifications
        await self._diagnostics.clear_diagnostic_notification(path)

        # Delete workflows/data_providers/tools when their source file goes away
        if path.endswith(".py"):
            await self._workflow_indexer.delete_workflows_for_file(path)

    # ========================================================================
    # Deactivation Protection (delegate to DeactivationProtectionService)
    # These are exposed for backward compatibility with tests
    # ========================================================================

    def _compute_similarity(self, old_name: str, new_name: str) -> float:
        """Compute similarity score between two function names."""
        return self._deactivation.compute_similarity(old_name, new_name)

    async def _find_affected_entities(
        self,
        workflow_id: str,
    ) -> list[dict[str, str]]:
        """Find entities that reference a workflow."""
        return await self._deactivation.find_affected_entities(workflow_id)

    async def _detect_pending_deactivations(
        self,
        path: str,
        new_content: bytes,
        existing_workflows: list,
        existing_function_names: set[str],
    ):
        """Detect workflows that would be deactivated by a file save."""
        return await self._deactivation.detect_pending_deactivations(
            path=path,
            new_content=new_content,
            existing_workflows=existing_workflows,
            existing_function_names=existing_function_names,
        )

    async def _apply_workflow_replacements(
        self,
        replacements: dict[str, str],
    ) -> None:
        """Apply workflow identity replacements."""
        await self._deactivation.apply_workflow_replacements(replacements)


def get_file_storage_service(db: AsyncSession) -> FileStorageService:
    """
    Factory function to get a FileStorageService instance.

    Args:
        db: Database session

    Returns:
        Configured FileStorageService instance
    """
    return FileStorageService(db)
