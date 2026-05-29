"""
File Operations Service for File Storage.

Handles read, write, delete, and move operations for individual files.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.models import Workflow
from src.models.orm.file_index import FileIndex
from src.core.module_cache import set_module, invalidate_module
from src.services.repo_storage import REPO_PREFIX
from .models import WriteResult
from .entity_detector import detect_platform_entity_type

if TYPE_CHECKING:
    from src.models.orm.applications import Application
    from src.services.app_bundler import BundleResult
    from .diagnostics import DiagnosticsService
    from .deactivation import DeactivationProtectionService

logger = logging.getLogger(__name__)


def compute_git_blob_sha(content: bytes) -> str:
    """
    Compute Git blob SHA (how Git identifies file content).

    Git blob SHA = SHA1("blob <size>\\0<content>")

    This is stored in github_sha to enable fast sync comparison
    without reading file content from S3.
    """
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


class FileOperationsService:
    """Service for individual file operations (read, write, delete, move)."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        s3_client,
        diagnostics: "DiagnosticsService",
        deactivation: "DeactivationProtectionService",
        file_hash_fn: Callable[[bytes], str],
        content_type_fn: Callable[[str], str],
        platform_entity_detector_fn: Callable[[str, bytes], str | None],
        extract_metadata_fn,
        remove_metadata_fn,
    ):
        """
        Initialize file operations service.

        Args:
            db: Database session
            settings: Application settings
            s3_client: S3 client context manager
            diagnostics: DiagnosticsService instance
            deactivation: DeactivationProtectionService instance
            file_hash_fn: Function to compute content hash
            content_type_fn: Function to guess content type
            platform_entity_detector_fn: Function to detect platform entity type
            extract_metadata_fn: Function to extract metadata
            remove_metadata_fn: Function to remove metadata
        """
        self.db = db
        self.settings = settings
        self._s3_client = s3_client
        self._diagnostics = diagnostics
        self._deactivation = deactivation
        self._compute_hash = file_hash_fn
        self._guess_content_type = content_type_fn
        self._detect_platform_entity_type = platform_entity_detector_fn
        self._extract_metadata = extract_metadata_fn
        self._remove_metadata = remove_metadata_fn

    async def read_file(self, path: str) -> tuple[bytes, None]:
        """
        Read file content.

        Python modules go through the Redis cache first (workers import via the
        cache), then fall back to S3 ``_repo/``. Everything else reads directly
        from S3.

        Args:
            path: Relative path within workspace

        Returns:
            Tuple of (content bytes, None)

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        # Python modules: Redis cache → S3 fallback (for fast worker imports)
        if path.endswith(".py"):
            from src.core.module_cache import get_module
            cached = await get_module(path)
            if cached:
                return cached["content"].encode("utf-8"), None

        # Fallback to S3 _repo/ prefix
        s3_key = f"{REPO_PREFIX}{path}"
        async with self._s3_client.get_client() as s3:
            try:
                response = await s3.get_object(
                    Bucket=self.settings.s3_bucket,
                    Key=s3_key,
                )
                content = await response["Body"].read()
                return content, None
            except s3.exceptions.NoSuchKey:
                raise FileNotFoundError(f"File not found: {path}")

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
        """
        Write file content to storage and update index.

        Also extracts workflow/form metadata at write time.
        For platform entities (workflows, forms, apps, agents), content is stored
        in the database. For regular files, content is stored in S3.

        Args:
            path: Relative path within workspace
            content: File content as bytes
            updated_by: User who made the change
            force_deactivation: Skip deactivation protection for Python files
            replacements: Map of workflow_id -> new_function_name for identity transfer

        Returns:
            WriteResult containing file record, final content, modification flag,
            diagnostics, and pending deactivations if any.

        Raises:
            ValueError: If path is excluded (system files, caches, etc.)
        """
        # Check if path is excluded (system files, caches, metadata, etc.)
        from src.services.editor.file_filter import is_excluded_path
        if is_excluded_path(path):
            raise ValueError(f"Path is excluded from workspace: {path}")

        content_hash = self._compute_hash(content)
        content_type = self._guess_content_type(path)
        size_bytes = len(content)

        # For Python files, use AST detection to cache the tree and decoded string.
        # The cached AST avoids re-parsing in _extract_metadata.
        cached_ast = None
        cached_content_str = None
        if path.endswith(".py"):
            from src.services.file_storage.entity_detector import (
                detect_python_entity_type_with_ast,
            )
            detection_result = detect_python_entity_type_with_ast(content)
            cached_ast = detection_result.ast_tree
            cached_content_str = detection_result.content_str

        # Write ALL files to S3 under _repo/ prefix — this is the durable store.
        # Platform entities also go to S3 so the Redis→S3 fallback works for workers.
        s3_key = f"{REPO_PREFIX}{path}"
        async with self._s3_client.get_client() as s3:
            await s3.put_object(
                Bucket=self.settings.s3_bucket,
                Key=s3_key,
                Body=content,
                ContentType=content_type,
            )

        now = datetime.now(timezone.utc)

        # Write to file_index (the sole search index)
        # Binary files (containing null bytes) can't be stored in PostgreSQL text columns,
        # so we index them with path only (no content) for listing/existence checks.
        content_str = cached_content_str or content.decode("utf-8", errors="replace")
        is_binary = b"\x00" in content
        fi_stmt = insert(FileIndex).values(
            path=path,
            content="" if is_binary else content_str,
            content_hash=content_hash,
            updated_at=now,
            updated_by=updated_by,
        ).on_conflict_do_update(
            index_elements=[FileIndex.path],
            set_={
                "content": "" if is_binary else content_str,
                "content_hash": content_hash,
                "updated_at": now,
                "updated_by": updated_by,
            },
        )
        await self.db.execute(fi_stmt)
        await self.db.flush()

        # Update module cache in Redis for immediate availability in virtual imports.
        # Both workflows and modules need caching — workers load code via Redis→S3.
        if path.endswith(".py"):
            await set_module(path, content_str, content_hash)

        # Extract metadata for workflows/forms/agents
        # Pass cached AST and content_str to avoid re-parsing large Python files
        (
            final_content,
            content_modified,
            needs_indexing,
            workflow_id_conflicts,
            diagnostics,
            pending_deactivations,
            available_replacements,
        ) = await self._extract_metadata(
            path, content, force_deactivation, replacements,
            cached_ast=cached_ast, cached_content_str=cached_content_str,
            workflows_to_deactivate=workflows_to_deactivate,
        )

        # Release cached AST and content string to free memory
        del cached_ast
        del cached_content_str

        # If there are pending deactivations, return early (caller should raise 409)
        if pending_deactivations:
            return WriteResult(
                file_record=None,
                final_content=final_content,
                content_modified=content_modified,
                needs_indexing=needs_indexing,
                workflow_id_conflicts=workflow_id_conflicts,
                diagnostics=diagnostics if diagnostics else None,
                pending_deactivations=pending_deactivations,
                available_replacements=available_replacements,
            )

        # Scan Python files for missing SDK references
        if path.endswith(".py"):
            try:
                await self._diagnostics.scan_for_sdk_issues(path, final_content)
            except Exception as e:
                logger.warning(f"Failed to scan for SDK issues in {path}: {e}")

        # Create or clear system notification based on diagnostic errors
        has_errors = diagnostics and any(d.severity == "error" for d in diagnostics)
        if has_errors:
            try:
                await self._diagnostics.create_diagnostic_notification(path, diagnostics)
            except Exception as e:
                logger.warning(f"Failed to create diagnostic notification for {path}: {e}")
        else:
            try:
                await self._diagnostics.clear_diagnostic_notification(path)
            except Exception as e:
                logger.warning(f"Failed to clear diagnostic notification for {path}: {e}")

        # App files: rebuild bundle + fire pubsub for real-time preview
        app = await self._find_app_by_path(path)
        if not app and path.startswith("apps/"):
            logger.info(
                f"No Application matched path {path!r} — preview refresh skipped. "
                f"Check Application.repo_path."
            )
        if app:
            await self._rebuild_app_bundle(app, path, content_str, updated_by)

        # Mark repo as having uncommitted changes (skip for CLI pushes)
        if not skip_dirty_flag:
            from src.core.repo_dirty import mark_repo_dirty
            try:
                await mark_repo_dirty()
            except Exception as e:
                logger.warning(f"Failed to mark repo dirty: {e}")

        # Broadcast file_push event for watch mode sync
        try:
            from src.core.pubsub import publish_file_activity
            from src.core.request_context import get_request_user, get_request_session_id
            req_user = get_request_user()
            await publish_file_activity(
                user_id=req_user.user_id if req_user else updated_by,
                user_name=req_user.user_name if req_user else updated_by,
                activity_type="file_push",
                paths=[path],
                session_id=get_request_session_id(),
            )
        except Exception as e:
            logger.warning(f"Failed to publish file_push for {path}: {e}")

        logger.info(f"File written: {path} ({size_bytes} bytes) by {updated_by}")
        return WriteResult(
            file_record=None,
            final_content=final_content,
            content_modified=content_modified,
            needs_indexing=needs_indexing,
            workflow_id_conflicts=workflow_id_conflicts,
            diagnostics=diagnostics if diagnostics else None,
        )

    async def delete_file(self, path: str) -> None:
        """
        Delete a file from storage.

        Pattern: S3 first (source of truth), then conditional side effects.
        """
        # === S3: Source of truth (must succeed) ===
        await self._delete_from_s3(path)

        # === Side effects (best-effort, independent) ===
        for op in (
            self._remove_from_search_index,
            self._handle_app_file_cleanup,
            self._remove_metadata,
            self._invalidate_module_cache_if_python,
        ):
            try:
                await op(path)
            except Exception as e:
                logger.warning(f"Delete side effect failed for {path}: {e}")

        # Broadcast file_delete event for watch mode sync
        try:
            from src.core.pubsub import publish_file_activity
            from src.core.request_context import get_request_user, get_request_session_id
            req_user = get_request_user()
            await publish_file_activity(
                user_id=req_user.user_id if req_user else "system",
                user_name=req_user.user_name if req_user else "system",
                activity_type="file_delete",
                paths=[path],
                session_id=get_request_session_id(),
            )
        except Exception as e:
            logger.warning(f"Failed to publish file_delete for {path}: {e}")

        logger.info(f"File deleted: {path}")

    async def _delete_from_s3(self, path: str) -> None:
        """Delete from S3 _repo/ — source-of-truth operation."""
        s3_key = f"{REPO_PREFIX}{path}"
        async with self._s3_client.get_client() as s3:
            await s3.delete_object(Bucket=self.settings.s3_bucket, Key=s3_key)

    async def _remove_from_search_index(self, path: str) -> None:
        """Remove from file_index search table if present."""
        from sqlalchemy import delete
        del_stmt = delete(FileIndex).where(FileIndex.path == path)
        await self.db.execute(del_stmt)

    async def _find_app_by_path(self, path: str) -> "Application | None":
        """Find the Application that owns a file path via repo_path prefix match.

        Uses a DB query to find apps whose repo_path is a prefix of the given
        path, selecting the longest (most specific) match. This supports apps
        at any repo_path, not just apps/{slug}.
        """
        from sqlalchemy import func, text

        from src.models.orm.applications import Application

        # Find the app whose repo_path is the longest prefix of this path.
        # e.g. path="custom/deep/app/pages/index.tsx" matches repo_path="custom/deep/app"
        # We append "/" to repo_path so "apps/my" doesn't match "apps/myapp/file.tsx"
        # Uses starts_with() instead of LIKE to avoid wildcard chars (_, %) in repo_path.
        stmt = (
            select(Application)
            .where(text("starts_with(:path, repo_path || '/')").bindparams(path=path))
            .order_by(func.length(Application.repo_path).desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _handle_app_file_cleanup(self, path: str) -> None:
        """If this file belongs to an app, clean up preview and notify clients."""
        app = await self._find_app_by_path(path)
        if not app:
            return

        app_prefix = app.repo_prefix
        relative_path = path[len(app_prefix):] if path.startswith(app_prefix) else path

        from src.core.pubsub import publish_app_code_file_update
        await publish_app_code_file_update(
            app_id=str(app.id),
            user_id="system",
            user_name="system",
            path=relative_path,
            source=None,
            compiled=None,
            action="delete",
        )

        from src.services.app_storage import AppStorageService
        app_storage = AppStorageService()
        await app_storage.delete_preview_file(str(app.id), relative_path)

    async def _invalidate_module_cache_if_python(self, path: str) -> None:
        """Invalidate Redis module cache for Python files."""
        platform_entity_type = detect_platform_entity_type(path, b"")
        if platform_entity_type == "module" or path.endswith(".py"):
            await invalidate_module(path)

    async def _rebuild_app_bundle(
        self,
        app: "Application",
        path: str,
        content_str: str,
        updated_by: str,
    ) -> None:
        """Rebuild the whole app bundle after a file write and broadcast the result.

        On success: manifest.json + hashed chunks are written to S3; clients
        receive a `bundle` signal with the new entry name and reload.

        On failure: S3 is unchanged — last good bundle stays live. Clients
        receive an `error` signal with structured esbuild messages (file,
        line, column, text) so the UI can render a banner over the last-good
        render. A system diagnostic notification is also created.
        """
        from src.services.app_bundler import BundleMessage, BundleResult, build_with_migrate

        app_prefix = app.repo_prefix
        relative_path = path[len(app_prefix):] if path.startswith(app_prefix) else path
        app_id = str(app.id)

        try:
            result, _migrated = await build_with_migrate(
                app_id=app_id,
                repo_prefix=app_prefix,
                mode="preview",
                dependencies=app.dependencies or {},
            )
        except Exception as e:
            # Bundler should surface esbuild failures via BundleResult.errors
            # rather than raising. If it raises anyway (subprocess blew up,
            # S3 upload mid-build, etc.) synthesize a failure result and
            # route through the same reporting path so the file write
            # itself does not fail.
            logger.exception(f"Bundler crashed for app={app_id}: {e}")
            result = BundleResult(
                success=False,
                errors=[BundleMessage(text=f"Bundler crashed: {e}")],
            )

        await self._report_bundle_result(
            result,
            app_id=app_id,
            app_prefix=app_prefix,
            path=path,
            relative_path=relative_path,
            content_str=content_str,
            updated_by=updated_by,
        )

    async def _report_bundle_result(
        self,
        result: "BundleResult",
        *,
        app_id: str,
        app_prefix: str,
        path: str,
        relative_path: str,
        content_str: str,
        updated_by: str,
    ) -> None:
        """Surface a bundle outcome uniformly: pubsub + diagnostics + logging.

        Called for every build attempt — successful or not — so there is one
        code path that decides what gets published, what diagnostic state is
        updated, and what gets logged. Failures here are logged and swallowed
        so the enclosing file write still succeeds.
        """
        from src.core.pubsub import publish_app_code_file_update
        from .diagnostics import FileDiagnosticInfo

        bundle_payload: dict | None = None
        error_payload: dict | None = None

        if result.success and result.manifest is not None:
            m = result.manifest
            bundle_payload = {
                "entry": m.entry,
                "css": m.css,
                "duration_ms": m.duration_ms,
            }
            try:
                await self._diagnostics.clear_diagnostic_notification(path)
            except Exception as e:
                logger.warning(f"Failed to clear bundler diagnostic for {path}: {e}")
            logger.info(
                f"App bundle rebuilt: app={app_id} path={relative_path} "
                f"entry={m.entry} duration_ms={m.duration_ms}"
            )
        else:
            errors = result.errors or []
            error_payload = {
                "messages": [
                    {
                        "text": e.text,
                        "file": e.file,
                        "line": e.line,
                        "column": e.column,
                        "line_text": e.line_text,
                    }
                    for e in errors
                ],
            }
            try:
                diagnostics = [
                    FileDiagnosticInfo(
                        severity="error",
                        message=e.text,
                        line=e.line,
                        column=e.column,
                        source="bundler",
                    )
                    for e in errors
                ]
                # Prefer the first error's file path for the notification
                # target so "view file" jumps to the right place. esbuild
                # file paths are already app-relative.
                target_path = path
                if errors and errors[0].file:
                    target_path = app_prefix + errors[0].file
                await self._diagnostics.create_diagnostic_notification(
                    target_path, diagnostics
                )
            except Exception as e:
                logger.warning(f"Failed to create bundler diagnostic for {path}: {e}")
            first = errors[0] if errors else None
            first_file = first.file if first else None
            first_line = first.line if first else None
            first_col = first.column if first else None
            first_msg = first.text if first else ""
            logger.warning(
                f"App bundle BUILD FAILED: app={app_id} path={relative_path} "
                f"errors={len(errors)} "
                f"first_file={first_file!r} "
                f"first_line={first_line} "
                f"first_col={first_col} "
                f"first_msg={first_msg!r}"
            )

        try:
            await publish_app_code_file_update(
                app_id=app_id,
                user_id=updated_by,
                user_name=updated_by,
                path=relative_path,
                source=content_str,
                action="update",
                bundle=bundle_payload,
                error=error_payload,
            )
        except Exception as pub_err:
            logger.warning(f"Failed to publish app file update: {pub_err}")

    async def move_file(self, old_path: str, new_path: str) -> None:
        """
        Move/rename a file, preserving platform entity associations.

        Args:
            old_path: Current relative path within workspace
            new_path: New relative path within workspace

        Raises:
            FileNotFoundError: If old_path doesn't exist
            FileExistsError: If new_path already exists
        """
        now = datetime.now(timezone.utc)

        # Check old path exists in file_index
        fi_stmt = select(FileIndex).where(FileIndex.path == old_path)
        fi_result = await self.db.execute(fi_stmt)
        old_record = fi_result.scalar_one_or_none()
        if not old_record:
            raise FileNotFoundError(f"File not found: {old_path}")

        # Check new path doesn't exist
        fi_stmt2 = select(FileIndex).where(FileIndex.path == new_path)
        fi_result2 = await self.db.execute(fi_stmt2)
        if fi_result2.scalar_one_or_none():
            raise FileExistsError(f"File already exists: {new_path}")

        # Update entity table paths for Python files
        if old_path.endswith(".py"):
            # Update any workflows that reference this path
            stmt = update(Workflow).where(
                Workflow.path == old_path
            ).values(
                path=new_path,
                updated_at=now,
            )
            await self.db.execute(stmt)

            # Update module cache
            await invalidate_module(old_path)
            if old_record.content:
                await set_module(new_path, old_record.content, old_record.content_hash or "")

        # Move file in S3 _repo/ prefix for all file types
        old_s3_key = f"{REPO_PREFIX}{old_path}"
        new_s3_key = f"{REPO_PREFIX}{new_path}"
        async with self._s3_client.get_client() as s3:
            try:
                await s3.copy_object(
                    Bucket=self.settings.s3_bucket,
                    CopySource={"Bucket": self.settings.s3_bucket, "Key": old_s3_key},
                    Key=new_s3_key,
                )
                await s3.delete_object(
                    Bucket=self.settings.s3_bucket,
                    Key=old_s3_key,
                )
            except Exception as e:
                logger.warning(f"S3 move failed for {old_path} -> {new_path}: {e}")

        # Update file_index: insert new path, delete old
        new_stmt = insert(FileIndex).values(
            path=new_path,
            content=old_record.content,
            content_hash=old_record.content_hash,
            updated_at=now,
            updated_by=old_record.updated_by,
        ).on_conflict_do_update(
            index_elements=[FileIndex.path],
            set_={
                "content": old_record.content,
                "content_hash": old_record.content_hash,
                "updated_at": now,
                "updated_by": old_record.updated_by,
            },
        )
        await self.db.execute(new_stmt)

        from sqlalchemy import delete
        del_stmt = delete(FileIndex).where(FileIndex.path == old_path)
        await self.db.execute(del_stmt)

        logger.info(f"File moved: {old_path} -> {new_path}")
