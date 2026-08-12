"""
Maintenance Router

API endpoints for workspace maintenance operations.
Platform admin resource - no org scoping.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context, CurrentSuperuser
from src.core.database import get_db
from src.models import (
    CleanupOrphanedResponse,
    DocsIndexResponse,
    MaintenanceStatus,
    OrphanedEntity,
    PreflightIssueResponse,
    PreflightResponse,
    ReimportJobResponse,
)
from src.models.contracts.notifications import (
    NotificationCategory,
    NotificationCreate,
    NotificationStatus,
)
from src.models.orm import (
    Application,
    Workflow,
)
from src.models.orm.file_index import FileIndex
from src.services.app_dependencies import parse_dependencies
from src.services.notification_service import get_notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/maintenance", tags=["Maintenance"])


@router.get(
    "/status",
    response_model=MaintenanceStatus,
    summary="Get maintenance status",
    description="Get current workspace maintenance status (Platform admin only)",
)
async def get_maintenance_status(
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> MaintenanceStatus:
    """
    Get the current maintenance status of the workspace.

    Returns:
        - total_files: Total number of Python files in file_index
        - last_reindex: Timestamp of last reindex (not tracked currently)
    """
    try:
        result = await db.execute(
            select(func.count()).select_from(FileIndex).where(
                FileIndex.path.endswith(".py"),
                ~FileIndex.path.endswith("/"),
            )
        )
        total_files = result.scalar() or 0

        return MaintenanceStatus(
            total_files=total_files,
            last_reindex=None,
        )

    except Exception as e:
        logger.error(f"Error getting maintenance status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get maintenance status",
        )


@router.post(
    "/index-docs",
    response_model=DocsIndexResponse,
    summary="Index platform documentation",
    description="Manually trigger indexing of Bifrost platform documentation into the knowledge store (Platform admin only)",
)
async def index_documentation(
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> DocsIndexResponse:
    """
    Manually index platform documentation into the knowledge store.

    This indexes all .txt files from the bundled documentation into embeddings
    for use by the Coding Assistant. Uses content hashing to avoid re-indexing
    unchanged files.

    Requires:
        - Platform admin access
        - Embeddings to be configured (OpenAI API key)

    Returns:
        DocsIndexResponse with indexing statistics
    """
    import time

    from src.services.docs_indexer import index_platform_docs

    start_time = time.time()

    try:
        result = await index_platform_docs()
        duration_ms = int((time.time() - start_time) * 1000)

        if result["status"] == "skipped":
            return DocsIndexResponse(
                status="skipped",
                files_indexed=0,
                files_unchanged=0,
                files_deleted=0,
                duration_ms=duration_ms,
                message=result.get("reason", "Indexing skipped"),
            )

        if result["status"] == "complete":
            indexed = result.get("indexed", 0)
            skipped = result.get("skipped", 0)
            deleted = result.get("deleted", 0)

            parts = [f"Indexed {indexed} files"]
            if skipped > 0:
                parts.append(f"{skipped} unchanged")
            if deleted > 0:
                parts.append(f"{deleted} orphaned removed")
            message = ", ".join(parts)

            return DocsIndexResponse(
                status="complete",
                files_indexed=indexed,
                files_unchanged=skipped,
                files_deleted=deleted,
                duration_ms=duration_ms,
                message=message,
            )

        return DocsIndexResponse(
            status="failed",
            duration_ms=duration_ms,
            message=f"Unexpected result: {result}",
        )

    except Exception as e:
        logger.error(f"Error indexing documentation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to index documentation: {str(e)}",
        )


@router.post(
    "/reimport",
    response_model=ReimportJobResponse,
    summary="Reimport from repository",
    description="Queue a reimport of all entities from S3. Poll GET /api/jobs/{job_id} for result.",
)
async def reimport_from_repo(
    ctx: Context,
    user: CurrentSuperuser,
) -> ReimportJobResponse:
    """
    Queue a reimport of all entities from the S3 repo.

    Returns a job_id immediately. Poll GET /api/jobs/{job_id} for completion.
    Platform admin only.
    """
    from src.jobs.platform.reimport import (
        WORKSPACE_REIMPORT_DEFINITION,
        WorkspaceReimportPayload,
    )
    from src.services.platform_jobs import enqueue_platform_job, publish_platform_job_update

    job, _ = await enqueue_platform_job(
        ctx.db,
        WORKSPACE_REIMPORT_DEFINITION,
        WorkspaceReimportPayload(),
        dedupe_key="workspace",
        resource_lock_key="workspace",
        priority=500,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="workspace",
        resource_id="_repo",
        title="Reimport workspace",
        action_url="/diagnostics",
    )
    await ctx.db.commit()
    await publish_platform_job_update(job)
    return ReimportJobResponse(status=job.status, job_id=str(job.id))


@router.post(
    "/cleanup-orphaned",
    response_model=CleanupOrphanedResponse,
    summary="Clean up orphaned entity references",
    description="Deactivate workflows, forms, and agents whose files no longer exist in the workspace",
)
async def cleanup_orphaned(
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> CleanupOrphanedResponse:
    """
    Find and deactivate workflows that reference files no longer in FileIndex.

    Forms and agents are managed via the manifest (``.bifrost/forms.yaml`` /
    ``.bifrost/agents.yaml``); stale entries are cleaned up by the manifest
    import pipeline, not here.

    This is a synchronous DB-only operation (no S3/checkout needed).
    """
    try:
        # Get all existing file paths from FileIndex
        fi_result = await db.execute(select(FileIndex.path))
        existing_paths: set[str] = {row[0] for row in fi_result.all()}

        cleaned: list[OrphanedEntity] = []

        # Workflows — have a direct `path` column
        wf_result = await db.execute(
            select(Workflow).where(Workflow.is_active.is_(True))
        )
        for wf in wf_result.scalars().all():
            if wf.path and wf.path not in existing_paths:
                wf.is_active = False
                wf.is_orphaned = True
                cleaned.append(OrphanedEntity(
                    entity_type="workflow",
                    entity_id=str(wf.id),
                    entity_name=wf.display_name or wf.name,
                    path=wf.path,
                ))

        await db.commit()

        if cleaned:
            logger.info(
                f"Cleaned up {len(cleaned)} orphaned entities: "
                f"{', '.join(f'{e.entity_type}:{e.entity_name}' for e in cleaned)}"
            )

        return CleanupOrphanedResponse(
            success=True,
            cleaned=cleaned,
            count=len(cleaned),
        )

    except Exception as e:
        logger.error(f"Error cleaning up orphaned entities: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clean up orphaned entities: {str(e)}",
        )


class AppDependencyIssue(BaseModel):
    """A missing dependency issue in an app."""

    app_id: str
    app_name: str
    app_slug: str
    file_path: str
    dependency_type: str
    dependency_id: str


class AppDependencyScanResponse(BaseModel):
    """Response from app dependency scan."""

    apps_scanned: int
    files_scanned: int
    dependencies_rebuilt: int
    issues_found: int
    issues: list[AppDependencyIssue]
    notification_created: bool


@router.post(
    "/scan-app-dependencies",
    response_model=AppDependencyScanResponse,
    summary="Scan app dependencies",
    description="Scan all app source files for workflow references and report issues (Platform admin only)",
)
async def scan_app_dependencies(
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> AppDependencyScanResponse:
    """
    Scan app file dependencies from source code in file_index.

    This endpoint:
    1. Reads all app source files from file_index (apps/{slug}/* paths)
    2. Parses source code for useWorkflow(), useWorkflowQuery(), useWorkflowMutation() calls
    3. Resolves references against active workflows
    4. Reports any dependencies that reference non-existent workflows

    Creates a platform admin notification if issues are found.
    """
    try:
        # Step 1: Get all apps
        apps_result = await db.execute(select(Application))
        apps = apps_result.scalars().all()

        # Build workflow lookup (all active workflows, all ref formats)
        wf_result = await db.execute(
            select(Workflow.id, Workflow.name, Workflow.path, Workflow.function_name)
            .where(Workflow.is_active.is_(True))
        )
        wf_lookup: set[str] = set()  # all valid ref formats
        for wf_id, wf_name, wf_path, wf_fn_name in wf_result.all():
            wf_lookup.add(str(wf_id))
            wf_lookup.add(wf_name)
            if wf_path and wf_fn_name:
                wf_lookup.add(f"{wf_path}::{wf_fn_name}")

        all_issues: list[AppDependencyIssue] = []
        total_deps_found = 0
        files_scanned = 0
        apps_seen: set[str] = set()

        for app in apps:
            prefix = app.repo_prefix
            fi_result = await db.execute(
                select(FileIndex.path, FileIndex.content).where(
                    FileIndex.path.startswith(prefix),
                )
            )

            app_files = fi_result.all()
            if not app_files:
                continue

            apps_seen.add(str(app.id))

            for fi_path, content in app_files:
                files_scanned += 1
                if not content:
                    continue

                refs = parse_dependencies(content)
                if not refs:
                    continue

                relative_path = fi_path[len(prefix):]

                for ref in refs:
                    total_deps_found += 1
                    # Check if ref resolves to an active workflow
                    resolved = ref in wf_lookup
                    if not resolved:
                        all_issues.append(
                            AppDependencyIssue(
                                app_id=str(app.id),
                                app_name=app.name,
                                app_slug=app.slug,
                                file_path=relative_path,
                                dependency_type="workflow",
                                dependency_id=ref,
                            )
                        )

        # Create notification if issues found
        notification_created = False
        if all_issues:
            notification_service = get_notification_service()

            apps_with_issues = len({i.app_slug for i in all_issues})
            title = f"Missing App Dependencies: {apps_with_issues} app(s)"

            # Check for existing notification to avoid duplicates
            existing = await notification_service.find_admin_notification_by_title(
                title=title,
                category=NotificationCategory.SYSTEM,
            )

            if not existing:
                # Build description with first few issues
                app_names = list({i.app_name for i in all_issues})[:3]
                description = f"{len(all_issues)} missing: {', '.join(app_names)}"
                if len(all_issues) > 3:
                    description += "..."

                await notification_service.create_notification(
                    user_id="system",
                    request=NotificationCreate(
                        category=NotificationCategory.SYSTEM,
                        title=title,
                        description=description,
                        metadata={
                            "action": "view_apps",
                            "issues": [
                                {
                                    "app_id": i.app_id,
                                    "app_name": i.app_name,
                                    "app_slug": i.app_slug,
                                    "file_path": i.file_path,
                                    "dependency_type": i.dependency_type,
                                    "dependency_id": i.dependency_id,
                                }
                                for i in all_issues
                            ],
                        },
                    ),
                    for_admins=True,
                    initial_status=NotificationStatus.AWAITING_ACTION,
                )
                notification_created = True

        logger.info(
            f"App dependency rebuild completed: scanned {len(apps_seen)} apps, "
            f"{files_scanned} files, rebuilt {total_deps_found} dependencies, "
            f"found {len(all_issues)} issues"
        )

        return AppDependencyScanResponse(
            apps_scanned=len(apps_seen),
            files_scanned=files_scanned,
            dependencies_rebuilt=total_deps_found,
            issues_found=len(all_issues),
            issues=all_issues,
            notification_created=notification_created,
        )

    except Exception as e:
        logger.error(f"Error rebuilding app dependencies: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rebuild app dependencies: {str(e)}",
        )


@router.post(
    "/preflight",
    response_model=PreflightResponse,
    summary="Run preflight validation",
    description="Run validation checks including unregistered function detection (Platform admin only)",
)
async def run_preflight(
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> PreflightResponse:
    """Run preflight validation on the current workspace state."""
    import ast

    from src.services.file_storage import FileStorageService

    issues: list[PreflightIssueResponse] = []
    warnings: list[PreflightIssueResponse] = []

    service = FileStorageService(db)

    # List all files
    try:
        all_files = await service.list_files("", recursive=True)
    except Exception as e:
        logger.error(f"Preflight: failed to list files: {e}")
        return PreflightResponse(
            valid=False,
            issues=[
                PreflightIssueResponse(
                    level="error",
                    category="system",
                    detail=f"Failed to list files: {e}",
                )
            ],
            warnings=[],
        )

    # Detect unregistered decorated functions.
    #
    # Memory note: on a workspace with hundreds of .py files the naive shape
    # (read all files, ast.parse each, hold every tree until the walk
    # finishes) retained 300+ MB per call in an e2e memory trace. AST objects
    # run ~10-50x the source byte size and they're cyclic, so Python's
    # generational GC doesn't reclaim them promptly. Fix: process one file
    # at a time and `del` the tree + strings before the next iteration so
    # peak RSS is bounded to a single file's AST, not the whole workspace.
    py_files = [f for f in all_files if f.path.endswith(".py")]
    decorator_names = {"workflow", "tool", "data_provider"}
    for py_file in py_files:
        # Parse → collect candidate function names → release AST. All of
        # this is synchronous so when the block exits the AST and content
        # are released before we hit any `await`. Previous shape held the
        # tree across the inner `await db.execute(...)` which kept it alive
        # for the full DB round trip per decorator.
        try:
            content_result = await service.read_file(py_file.path)
            content = content_result[0] if isinstance(content_result, tuple) else content_result
            content_str = content.decode("utf-8", errors="replace")
            tree = ast.parse(content_str, filename=py_file.path)
        except Exception:
            continue

        candidates: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                dec_name = None
                if isinstance(dec, ast.Name):
                    dec_name = dec.id
                elif isinstance(dec, ast.Call) and isinstance(
                    dec.func, ast.Name
                ):
                    dec_name = dec.func.id
                if dec_name in decorator_names:
                    candidates.append(node.name)
                    break

        # Explicitly drop the big objects before the DB awaits. Without
        # this, async suspension during db.execute would keep them alive.
        del tree, content_str, content

        for fn_name in candidates:
            result = await db.execute(
                select(Workflow).where(
                    Workflow.path == py_file.path,
                    Workflow.function_name == fn_name,
                    Workflow.is_active.is_(True),
                )
            )
            if not result.scalar_one_or_none():
                warnings.append(
                    PreflightIssueResponse(
                        level="warning",
                        category="unregistered_function",
                        detail=(
                            f"Decorated function '{fn_name}' in"
                            f" {py_file.path} is not registered."
                            " Use POST /api/workflows/register to"
                            " register it."
                        ),
                        path=py_file.path,
                    )
                )

    valid = len(issues) == 0
    return PreflightResponse(valid=valid, issues=issues, warnings=warnings)
