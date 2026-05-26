"""
Applications Router

Manage applications for the App Builder with draft/live versioning.
Uses OrgScopedRepository for standardized org scoping.

Applications follow the same scoping pattern as configs:
- organization_id = NULL: Global application (platform-wide)
- organization_id = UUID: Organization-scoped application

Applications use code-based files (TSX/TypeScript) stored in app_files table.
File operations are handled through the app_files router.
"""

import base64
import logging
import re
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.core.auth import Context, CurrentUser
from src.core.log_safety import log_safe
from src.core.org_filter import resolve_org_filter
from src.core.pubsub import publish_app_draft_update, publish_app_published
from src.models.contracts.applications import (
    ApplicationCreate,
    ApplicationDefinition,
    ApplicationDraftSave,
    ApplicationListResponse,
    ApplicationPublic,
    ApplicationPublishRequest,
    ApplicationReplaceRequest,
    ApplicationRollbackRequest,
    ApplicationUpdate,
)
from src.models.orm.applications import Application
from src.core.exceptions import AccessDeniedError
from shared.svg_sanitizer import SvgSanitizationError, sanitize_svg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/applications", tags=["Applications"])


class AppValidationIssue(BaseModel):
    severity: str  # "error" or "warning"
    file: str
    message: str
    line: int | None = None


class AppValidationResponse(BaseModel):
    valid: bool
    errors: list[AppValidationIssue] = []
    warnings: list[AppValidationIssue] = []


_IMPORT_RE = re.compile(
    r'^\s*import\s+.*?\s+from\s+["\']([^"\']+)["\']\s*;?\s*$',
    re.MULTILINE,
)


def extract_external_deps(content: str) -> set[str]:
    """Extract bare-specifier import targets from a TS/TSX source.

    Excludes:
    - the bifrost runtime (resolved by the bundler)
    - relative imports (./, ../) and absolute paths (/) — these resolve
      within the app and are not external dependencies

    Used by the validator to flag undeclared external deps.
    """
    deps: set[str] = set()
    for match in _IMPORT_RE.finditer(content):
        pkg = match.group(1)
        if pkg == "bifrost" or pkg.startswith((".", "/")):
            continue
        deps.add(pkg)
    return deps


from src.repositories.applications import ApplicationRepository  # noqa: E402


# =============================================================================
# Helper functions
# =============================================================================


def _logo_data_url(data: bytes | None, content_type: str | None) -> str | None:
    """Encode a binary logo as a data URL, or None if no logo is set."""
    if not data:
        return None
    mime = content_type or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def application_to_public(
    application: Application,
    repo: "ApplicationRepository",
) -> ApplicationPublic:
    """Convert Application ORM to ApplicationPublic with role_ids."""
    role_ids = await repo.get_role_ids(application.id)
    return ApplicationPublic(
        id=application.id,
        name=application.name,
        slug=application.slug,
        description=application.description,
        icon=application.icon,
        organization_id=application.organization_id,
        published_at=application.published_at,
        created_at=application.created_at,
        updated_at=application.updated_at,
        created_by=application.created_by,
        is_published=application.is_published,
        has_unpublished_changes=application.has_unpublished_changes,
        access_level=application.access_level,
        role_ids=role_ids,
        repo_path=application.repo_path,
        logo=_logo_data_url(application.logo_data, application.logo_content_type),
    )


async def get_application_or_404(
    ctx: Context,
    slug: str,
) -> Application:
    """Get application by slug with access control.

    Uses ApplicationRepository for cascade scoping and role-based access.
    Returns 404 for both not found and access denied to avoid leaking
    existence information.

    Returns:
        Application if found and accessible

    Raises:
        HTTPException 404 if not found or access denied
    """
    repo = ApplicationRepository(
        session=ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_platform_admin,
    )
    try:
        if ctx.user.is_platform_admin:
            # Slugs are globally unique — super admins can resolve across orgs
            app = await repo.get_by_slug_global(slug)
            if not app:
                raise AccessDeniedError(f"Application '{slug}' not found")
            return app
        return await repo.can_access(slug=slug)
    except AccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{slug}' not found",
        )


async def get_application_by_id_or_404(
    ctx: Context,
    app_id: UUID,
) -> Application:
    """Get application by UUID with access control.

    Uses ApplicationRepository for cascade scoping and role-based access.
    Returns 404 for both not found and access denied to avoid leaking
    existence information.

    Returns:
        Application if found and accessible

    Raises:
        HTTPException 404 if not found or access denied
    """
    repo = ApplicationRepository(
        session=ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_platform_admin,
    )
    try:
        return await repo.can_access(id=app_id)
    except AccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_id}' not found",
        )


LOGO_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"}
LOGO_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


# =============================================================================
# CRUD Endpoints
# =============================================================================


@router.post(
    "",
    response_model=ApplicationPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create an application",
)
async def create_application(
    data: ApplicationCreate,
    ctx: Context,
    user: CurrentUser,
) -> ApplicationPublic:
    """Create a new application."""
    # Use organization_id from request body if explicitly provided, else default to current org
    if "organization_id" in (data.model_fields_set or set()):
        target_org_id = data.organization_id
    else:
        target_org_id = ctx.org_id
    repo = ApplicationRepository(
        ctx.db,
        target_org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )

    try:
        application = await repo.create_application(data, created_by=user.email)
        return await application_to_public(application, repo)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Application with slug '{data.slug}' already exists",
        )


@router.get(
    "",
    response_model=ApplicationListResponse,
    summary="List applications",
)
async def list_applications(
    ctx: Context,
    user: CurrentUser,
    scope: str | None = Query(
        default=None,
        description="Filter scope: 'global' for global only, org UUID for specific org.",
    ),
) -> ApplicationListResponse:
    """List all applications in the current scope."""
    try:
        filter_type, filter_org = resolve_org_filter(ctx.user, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    repo = ApplicationRepository(
        ctx.db,
        filter_org,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )

    # Superusers use list_all_in_scope (respects filter_type, no role checks)
    # Regular users use list_applications (cascade scope + role checks)
    if user.is_platform_admin:
        applications = await repo.list_all_in_scope(filter_type)
    else:
        applications = await repo.list_applications()

    # Convert each application with role_ids
    public_apps = [await application_to_public(app, repo) for app in applications]

    return ApplicationListResponse(
        applications=public_apps,
        total=len(applications),
    )


@router.get(
    "/{slug}",
    response_model=ApplicationPublic,
    summary="Get application metadata",
)
async def get_application(
    slug: str,
    ctx: Context,
    user: CurrentUser,
) -> ApplicationPublic:
    """Get application metadata by slug (globally unique)."""
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )
    application = await get_application_or_404(ctx, slug)
    return await application_to_public(application, repo)


@router.patch(
    "/{app_id}",
    response_model=ApplicationPublic,
    summary="Update application metadata",
)
async def update_application(
    app_id: UUID,
    data: ApplicationUpdate,
    ctx: Context,
    user: CurrentUser,
) -> ApplicationPublic:
    """Update application metadata and access control by ID."""
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )

    try:
        application = await repo.update_application(
            app_id,
            data,
            updated_by=ctx.user.email,
            is_platform_admin=user.is_platform_admin,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Application with slug '{data.slug}' already exists",
        )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_id}' not found",
        )

    # Emit event for real-time updates
    await publish_app_draft_update(
        app_id=str(application.id),
        user_id=str(user.user_id),
        user_name=user.name or user.email or "Unknown",
        entity_type="app",
        entity_id=str(application.id),
    )

    return await application_to_public(application, repo)


@router.delete(
    "/{app_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete application",
)
async def delete_application(
    app_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> None:
    """Delete an application by ID."""
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )
    success = await repo.delete_application(app_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_id}' not found",
        )


# =============================================================================
# Draft Endpoints
# =============================================================================


@router.get(
    "/{app_id}/draft",
    response_model=ApplicationDefinition,
    summary="Get draft definition",
)
async def get_draft(
    app_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> ApplicationDefinition:
    """
    Get the current draft definition.

    Returns the draft files serialized as JSON.
    """
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )
    app = await get_application_by_id_or_404(ctx, app_id)
    export_data = await repo.export_application(app)
    return ApplicationDefinition(
        definition=export_data,
        version=0,  # Legacy field - deprecated
        is_live=False,
    )


@router.put(
    "/{app_id}/draft",
    response_model=ApplicationDefinition,
    summary="Save draft definition",
)
async def save_draft(
    app_id: UUID,
    data: ApplicationDraftSave,
    ctx: Context,
    user: CurrentUser,
) -> ApplicationDefinition:
    """
    Save a new draft definition.

    Replaces all existing draft files with the provided definition.
    """
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )
    app = await get_application_by_id_or_404(ctx, app_id)

    # Extract files from definition and update
    files_data = data.definition.get("files", [])
    await repo.update_draft_files(app, files_data)
    await ctx.db.flush()
    await ctx.db.refresh(app)
    return ApplicationDefinition(
        definition=data.definition,
        version=0,  # Legacy field - deprecated
        is_live=False,
    )


# =============================================================================
# Publish Endpoint
# =============================================================================


@router.post(
    "/{app_id}/publish",
    response_model=ApplicationPublic,
    summary="Publish draft to live",
)
async def publish_application(
    app_id: UUID,
    ctx: Context,
    user: CurrentUser,
    data: ApplicationPublishRequest | None = None,
) -> ApplicationPublic:
    """
    Publish the draft to live.

    Copies all draft files to a new live version.
    """
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )

    try:
        message = data.message if data else None
        application = await repo.publish(app_id, user.email, message)
        if not application:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application '{app_id}' not found",
            )

        # Emit event for real-time updates
        await publish_app_published(
            app_id=str(app_id),
            user_id=str(user.user_id),
            user_name=user.name or user.email or "Unknown",
            new_version_id=application.published_at.isoformat() if application.published_at else "",
        )

        return await application_to_public(application, repo)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# =============================================================================
# Replace Endpoint
# =============================================================================


@router.post(
    "/{app_id}/replace",
    response_model=ApplicationPublic,
    summary="Repoint application source directory",
)
async def replace_application_endpoint(
    app_id: UUID,
    data: ApplicationReplaceRequest,
    ctx: Context,
    user: CurrentUser,
) -> ApplicationPublic:
    """Update ``repo_path`` after source files have been moved/renamed.

    Validates that the new path is unique, non-nested with other apps, and has
    source files under it. ``force: true`` bypasses all three checks.
    """
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )

    try:
        application = await repo.replace_application(
            app_id, data.repo_path, force=data.force
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_id}' not found",
        )

    return await application_to_public(application, repo)


# =============================================================================
# Validate Endpoint
# =============================================================================


@router.post(
    "/{app_id}/validate",
    response_model=AppValidationResponse,
    summary="Validate application files",
)
async def validate_application(
    app_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> AppValidationResponse:
    """
    Run static analysis on application files.

    Checks for: unknown components, workflow ID format/existence,
    bad imports, forbidden patterns, required file structure.
    """
    from src.models.orm.file_index import FileIndex
    from src.models.orm.workflows import Workflow

    app = await get_application_by_id_or_404(ctx, app_id)
    prefix = app.repo_prefix

    # Get all app files
    result = await ctx.db.execute(
        select(FileIndex.path, FileIndex.content).where(
            FileIndex.path.startswith(prefix)
        )
    )
    files = {row.path: row.content or "" for row in result.all()}

    errors: list[AppValidationIssue] = []
    warnings: list[AppValidationIssue] = []

    # Check required file structure
    layout_path = f"{prefix}_layout.tsx"
    index_path = f"{prefix}pages/index.tsx"

    if layout_path not in files:
        errors.append(AppValidationIssue(
            severity="error",
            file="_layout.tsx",
            message="Missing required _layout.tsx file",
        ))

    if index_path not in files:
        warnings.append(AppValidationIssue(
            severity="warning",
            file="pages/index.tsx",
            message="Missing pages/index.tsx (home page)",
        ))

    # Get declared dependencies and track referenced ones
    declared_deps = app.dependencies or {}
    referenced_deps: set[str] = set()

    # Collect all compilable TSX/TS files
    compilable_files = []
    for full_path, content in files.items():
        rel_path = full_path[len(prefix):]
        if rel_path.endswith(".tsx") or rel_path.endswith(".ts"):
            compilable_files.append({"path": rel_path, "source": content, "full_path": full_path})

    # Compile all files via the server-side compiler
    if compilable_files:
        from src.services.app_compiler import AppCompilerService

        compiler = AppCompilerService()
        compile_inputs = [{"path": f["path"], "source": f["source"]} for f in compilable_files]
        compile_results = await compiler.compile_batch(compile_inputs)

        for comp_file, comp_result in zip(compilable_files, compile_results):
            rel_path = comp_file["path"]
            content = comp_file["source"]

            # Report compilation errors
            if not comp_result.success:
                errors.append(AppValidationIssue(
                    severity="error",
                    file=rel_path,
                    message=f"Compilation failed: {comp_result.error}",
                ))

            # Check for missing default export in pages and components
            if comp_result.success and comp_result.default_export is None:
                if rel_path.startswith("pages/") or rel_path.startswith("components/"):
                    errors.append(AppValidationIssue(
                        severity="error",
                        file=rel_path,
                        message="Missing default export. Pages and components must have a default export (e.g., export default function MyComponent() { ... })",
                    ))

            # Check _layout.tsx uses <Outlet /> not {children}
            if rel_path == "_layout.tsx":
                if "{children}" in content and "Outlet" not in content:
                    errors.append(AppValidationIssue(
                        severity="error",
                        file=rel_path,
                        message="Layout uses {children} but should use <Outlet /> for page routing. Replace {children} with <Outlet />.",
                    ))

            # Check for forbidden patterns
            forbidden = [
                (r'\brequire\s*\(', "require() is not allowed"),
                (r'\bmodule\.exports\b', "module.exports is not allowed"),
            ]
            for pattern, msg in forbidden:
                for i, line in enumerate(content.split("\n"), 1):
                    if re.search(pattern, line) and not line.strip().startswith("//"):
                        errors.append(AppValidationIssue(
                            severity="error",
                            file=rel_path,
                            message=msg,
                            line=i,
                        ))

            referenced_deps |= extract_external_deps(content)

            # Check workflow IDs
            # Match useWorkflowQuery("...") and useWorkflowMutation("...")
            workflow_refs = re.findall(
                r'(?:useWorkflowQuery|useWorkflowMutation)\s*\(\s*["\']([^"\']+)["\']',
                content,
            )
            for wf_ref in workflow_refs:
                # Check UUID format
                try:
                    wf_uuid = UUID(wf_ref)
                except ValueError:
                    errors.append(AppValidationIssue(
                        severity="error",
                        file=rel_path,
                        message=f"Workflow reference '{wf_ref}' is not a valid UUID. Use workflow IDs, not names.",
                    ))
                    continue

                # Check workflow exists
                wf_result = await ctx.db.execute(
                    select(Workflow.id).where(
                        Workflow.id == wf_uuid,
                        Workflow.is_active == True,  # noqa: E712
                    )
                )
                if not wf_result.scalar_one_or_none():
                    errors.append(AppValidationIssue(
                        severity="error",
                        file=rel_path,
                        message=f"Workflow '{wf_ref}' not found or inactive",
                    ))

    # Check for missing/unused dependencies. Host-provided modules
    # (DEFAULT_EXTERNALS — react, lucide-react, sonner, etc.) are
    # resolved by the host import map and never need to appear in
    # `app.dependencies`, so subtract them before the missing check.
    from src.services.app_bundler import DEFAULT_EXTERNALS

    host_provided = set(DEFAULT_EXTERNALS)
    user_referenced = referenced_deps - host_provided
    for dep in user_referenced:
        if dep not in declared_deps:
            errors.append(AppValidationIssue(
                severity="error",
                file="dependencies",
                message=f"Missing dependency: '{dep}' is imported but not declared in app dependencies",
            ))
    for dep in declared_deps:
        if dep not in user_referenced:
            warnings.append(AppValidationIssue(
                severity="warning",
                file="dependencies",
                message=f"Unused dependency: '{dep}' is declared but not imported by any file",
            ))

    return AppValidationResponse(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# =============================================================================
# Export/Import Endpoints
# =============================================================================


@router.get(
    "/{app_id}/export",
    response_model=ApplicationPublic,
    summary="Export application to JSON",
)
async def export_application(
    app_id: UUID,
    ctx: Context,
    user: CurrentUser,
    version_id: UUID | None = Query(default=None, description="Version UUID to export (defaults to draft)"),
) -> ApplicationPublic:
    """
    Export full application to JSON for GitHub sync/portability.

    Returns the complete application structure including all files.
    Pass version_id to export a specific version, or omit to export draft.
    """
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )
    application = await get_application_by_id_or_404(ctx, app_id)
    export_data = await repo.export_application(application, version_id)

    return ApplicationPublic.model_validate(export_data)


# =============================================================================
# Rollback Endpoint
# =============================================================================


@router.post(
    "/{app_id}/rollback",
    response_model=ApplicationPublic,
    summary="Rollback to a previous version",
)
async def rollback_application(
    app_id: UUID,
    data: ApplicationRollbackRequest,
    ctx: Context,
    user: CurrentUser,
) -> ApplicationPublic:
    """
    Rollback the application's active (live) version to a previous version.

    Sets the specified version as the new active version.
    The draft version remains unchanged.
    """
    repo = ApplicationRepository(
        ctx.db,
        ctx.org_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
    )
    application = await get_application_by_id_or_404(ctx, app_id)

    try:
        await repo.rollback_to_version(application, data.version_id)
        await ctx.db.flush()
        await ctx.db.refresh(application)
        logger.info(f"Rolled back application {log_safe(app_id)} to version {log_safe(data.version_id)}")
        return await application_to_public(application, repo)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# =============================================================================
# Logo Endpoints
# =============================================================================


@router.post(
    "/{app_id}/logo",
    summary="Upload application logo",
)
async def upload_application_logo(
    app_id: UUID,
    ctx: Context,
    file: UploadFile = File(..., description="Logo image (PNG/JPEG/SVG, ≤5MB)"),
) -> dict:
    """Upload a square logo for an application.

    Requires the same permissions as updating the application.
    """
    application = await get_application_by_id_or_404(ctx, app_id)

    if file.content_type not in LOGO_ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(sorted(LOGO_ALLOWED_CONTENT_TYPES))}",
        )

    content = await file.read()
    if len(content) > LOGO_MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size: {LOGO_MAX_SIZE // 1024 // 1024} MB",
        )

    if file.content_type == "image/svg+xml":
        try:
            content = sanitize_svg(content)
        except SvgSanitizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid SVG: {exc}",
            )

    application.logo_data = content
    application.logo_content_type = file.content_type
    await ctx.db.commit()
    return {"ok": True}


@router.get(
    "/{app_id}/logo",
    summary="Get application logo",
    responses={
        200: {"content": {"image/png": {}, "image/jpeg": {}, "image/svg+xml": {}}},
        404: {"description": "No logo set"},
    },
)
async def get_application_logo(
    app_id: UUID,
    ctx: Context,
) -> Response:
    application = await get_application_by_id_or_404(ctx, app_id)
    if not application.logo_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not set",
        )
    return Response(
        content=application.logo_data,
        media_type=application.logo_content_type or "application/octet-stream",
    )


@router.delete(
    "/{app_id}/logo",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete application logo",
)
async def delete_application_logo(
    app_id: UUID,
    ctx: Context,
) -> Response:
    application = await get_application_by_id_or_404(ctx, app_id)
    application.logo_data = None
    application.logo_content_type = None
    await ctx.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
