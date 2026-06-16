"""
App Code Files Router

CRUD operations for code source files in App Builder applications.

Read/list: served from _apps/{app_id}/preview/ or _apps/{app_id}/live/ in S3.
Write/delete: goes through FileStorageService (_repo/ + file_index), which
also syncs to _apps/{app_id}/preview/.

Endpoints use UUID for app_id with relative file paths.
Path can contain slashes (e.g., 'pages/clients/[id].tsx').

Path conventions:
- Root: _layout, _providers only
- pages/: index, _layout, [param]/, named subfolders
- components/: files or subfolders (free naming)
- modules/: files or subfolders (free naming)
"""

import asyncio
import logging
import re
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from src.core.auth import Context, CurrentUser
from src.core.exceptions import AccessDeniedError
from src.core.log_safety import log_safe
from src.services.solutions.guard import assert_entity_id_not_solution_managed
from src.models.contracts.applications import (
    AppFileUpdate,
    AppRenderResponse,
    RenderFileResponse,
    SimpleFileListResponse,
    SimpleFileResponse,
)
from src.models.orm.applications import Application
from src.routers.applications import ApplicationRepository
from src.services.app_storage import AppStorageService
from src.services.repo_storage import RepoStorage
from src.services.file_storage.service import get_file_storage_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/applications/{app_id}/files",
    tags=["App Code Files"],
)

render_router = APIRouter(
    prefix="/api/applications/{app_id}",
    tags=["App Rendering"],
)


# =============================================================================
# Path Validation
# =============================================================================

# Valid root-level files (no directory prefix)
ROOT_ALLOWED_FILES = {"_layout", "_providers"}

# Valid top-level directories
VALID_TOP_DIRS = {"pages", "components", "modules"}

# Pattern for dynamic route segments like [id] or [slug], with optional .ts/.tsx extension
DYNAMIC_SEGMENT_PATTERN = re.compile(r"^\[[\w-]+\](\.tsx?)?$")

# Pattern for valid folder names (alphanumeric, underscore, hyphen)
VALID_NAME_PATTERN = re.compile(r"^[\w-]+$")

# Pattern for valid file names (requires .ts or .tsx extension)
VALID_FILENAME_PATTERN = re.compile(r"^[\w-]+\.tsx?$")

# Validation patterns for npm dependencies
_PKG_NAME_RE = re.compile(r"^(@[a-z0-9-]+/)?[a-z0-9][a-z0-9._-]*$")
_VERSION_RE = re.compile(r"^\^?~?\d+(\.\d+){0,2}$")
_MAX_DEPENDENCIES = 20


def validate_file_path(path: str) -> None:
    """Validate file path against conventions.

    Path conventions:
    - Root: only _layout, _providers allowed
    - pages/: index, _layout, [param]/, named subfolders
    - components/: files or subfolders (free naming)
    - modules/: files or subfolders (free naming)

    Raises:
        HTTPException 400 if path is invalid
    """
    if not path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File path cannot be empty",
        )

    # Normalize path (remove leading/trailing slashes)
    path = path.strip("/")

    # Split into segments
    segments = path.split("/")

    # Check for empty segments (double slashes)
    if any(not seg for seg in segments):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path cannot contain empty segments (double slashes)",
        )

    # Root level file (no directory)
    if len(segments) == 1:
        filename = segments[0]

        # Allow styles.css at root
        if filename == "styles.css":
            return

        # Must have .ts or .tsx extension
        if not re.search(r"\.tsx?$", filename):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Files must have a .ts or .tsx extension",
            )

        # Check root name without extension
        root_name = re.sub(r"\.tsx?$", "", filename)
        if root_name not in ROOT_ALLOWED_FILES:
            allowed = ", ".join(sorted(f"{f}.tsx" for f in ROOT_ALLOWED_FILES))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Root-level file must be one of: {allowed}. "
                f"Use pages/, components/, or modules/ directories for other files.",
            )
        return

    # Check top-level directory
    top_dir = segments[0]
    if top_dir not in VALID_TOP_DIRS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Files must be in one of: {', '.join(sorted(VALID_TOP_DIRS))}. "
            f"Got: '{top_dir}'",
        )

    # Validate remaining segments
    remaining_segments = segments[1:]
    for i, segment in enumerate(remaining_segments):
        is_last_segment = i == len(remaining_segments) - 1

        # Dynamic segments only allowed in pages/
        if DYNAMIC_SEGMENT_PATTERN.match(segment):
            if top_dir != "pages":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Dynamic segments like [{segment[1:-1]}] are only allowed in pages/",
                )
            # For last segment, require extension
            if is_last_segment and not segment.endswith((".ts", ".tsx")):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Files must have a .ts or .tsx extension. Got: '{segment}'",
                )
            continue

        # Validate segment name - use filename pattern for last segment
        pattern = VALID_FILENAME_PATTERN if is_last_segment else VALID_NAME_PATTERN
        if not pattern.match(segment):
            if is_last_segment:
                # Check if missing extension
                if VALID_NAME_PATTERN.match(segment):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Files must have a .ts or .tsx extension. Got: '{segment}'",
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid filename '{segment}'. "
                    "Use alphanumeric characters, underscores, hyphens, with .ts or .tsx extension.",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid path segment '{segment}'. "
                "Use only alphanumeric characters, underscores, and hyphens.",
            )

        # Strip extension for special file checks
        segment_name = re.sub(r"\.tsx?$", "", segment)

        # Special files in pages/
        if top_dir == "pages" and segment_name in ("index", "_layout"):
            continue

        # _layout only allowed in pages/ at any level
        if segment_name == "_layout" and top_dir != "pages":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="_layout files are only allowed in pages/",
            )


# =============================================================================
# Helper Functions
# =============================================================================


async def get_application_or_404(ctx: Context, app_id: UUID) -> Application:
    """Get application by UUID with access control.

    Uses ApplicationRepository for cascade scoping and role-based access.
    Returns 404 for both not found and access denied to avoid leaking
    existence information.

    Returns:
        Application if found and accessible

    Raises:
        HTTPException 404 if not found or access denied
    """
    if ctx.user.embed is True:
        # Embed pre-auth (OPEN-D): the token is HMAC-bound to exactly ONE
        # app (its app_id claim). Tier/role checks don't apply; identity
        # binding does — only the bound app's files resolve.
        if ctx.user.app_id == str(app_id):
            app = await ctx.db.get(Application, app_id)
            if app is not None:
                return app
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_id}' not found",
        )

    repo = ApplicationRepository(
        session=ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_platform_admin,
        is_external=ctx.user.is_external,
    )
    try:
        return await repo.can_access(id=app_id)
    except AccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_id}' not found",
        )


class FileMode(str, Enum):
    draft = "draft"
    live = "live"


# =============================================================================
# S3-backed App File Endpoints
# =============================================================================


@router.get(
    "",
    response_model=SimpleFileListResponse,
    summary="List app files",
)
async def list_app_files(
    app_id: UUID = Path(..., description="Application UUID"),
    mode: FileMode = FileMode.draft,
    *,
    ctx: Context,
    _user: CurrentUser,
) -> SimpleFileListResponse:
    """List all files for an application.

    Source content is read from S3 (_repo/apps/{slug}/).
    Compiled content is read from _apps/{app_id}/{mode}/.
    The compiled field is only set when it differs from source.
    """
    app = await get_application_or_404(ctx, app_id)
    app_storage = AppStorageService()
    repo = RepoStorage()

    # List source files from S3 (source of truth)
    repo_prefix = app.repo_prefix
    source_paths = await repo.list(repo_prefix)

    if not source_paths:
        return SimpleFileListResponse(files=[], total=0)

    storage_mode = "preview" if mode == FileMode.draft else "live"

    # Read source from S3 and compiled from _apps/
    files: list[SimpleFileResponse] = []
    for full_path in sorted(source_paths):
        # Derive relative path by stripping the repo prefix
        rel_path = full_path[len(repo_prefix):]
        if not rel_path:
            continue
        # Skip folder marker entries
        if rel_path.endswith("/"):
            continue

        # Source from S3 (_repo/)
        try:
            source = (await repo.read(full_path)).decode("utf-8", errors="replace")
        except Exception:
            source = ""

        # Compiled from _apps/{app_id}/{mode}/
        compiled: str | None = None
        try:
            compiled_bytes = await app_storage.read_file(str(app.id), storage_mode, rel_path)
            compiled_str = compiled_bytes.decode("utf-8", errors="replace")
            if compiled_str != source:
                compiled = compiled_str
        except FileNotFoundError:
            # No compiled artifact (file not yet built / non-compiled type) — return source only
            logger.debug(f"no compiled artifact for app {app.id} {storage_mode} {rel_path}")

        files.append(SimpleFileResponse(path=rel_path, source=source, compiled=compiled))

    return SimpleFileListResponse(files=files, total=len(files))


@router.get(
    "/{file_path:path}",
    response_model=SimpleFileResponse,
    summary="Read a single app file",
)
async def read_app_file(
    app_id: UUID = Path(..., description="Application UUID"),
    file_path: str = Path(..., description="Relative file path (can contain slashes)"),
    mode: FileMode = FileMode.draft,
    *,
    ctx: Context,
    _user: CurrentUser,
) -> SimpleFileResponse:
    """Read a single file by relative path.

    Source content is read from the file_index (_repo/apps/{slug}/).
    Compiled content is read from _apps/{app_id}/{mode}/.
    The compiled field is only set when it differs from source.
    """
    app = await get_application_or_404(ctx, app_id)
    app_storage = AppStorageService()

    # Source from S3 (_repo/)
    repo_path = f"{app.repo_prefix}{file_path}"
    repo = RepoStorage()
    try:
        source = (await repo.read(repo_path)).decode("utf-8", errors="replace")
    except Exception:
        source = None
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_path}' not found",
        )

    # Compiled from _apps/{app_id}/{mode}/
    compiled: str | None = None
    storage_mode = "preview" if mode == FileMode.draft else "live"
    try:
        compiled_bytes = await app_storage.read_file(str(app.id), storage_mode, file_path)
        compiled_str = compiled_bytes.decode("utf-8", errors="replace")
        # Only set compiled if it differs from source
        if compiled_str != source:
            compiled = compiled_str
    except FileNotFoundError:
        # No compiled artifact yet — return source only
        logger.debug(f"no compiled artifact for app {app.id} {log_safe(storage_mode)} {log_safe(file_path)}")

    return SimpleFileResponse(path=file_path, source=source, compiled=compiled)


@router.put(
    "/{file_path:path}",
    response_model=SimpleFileResponse,
    summary="Create or update an app file",
)
async def write_app_file(
    data: AppFileUpdate,
    app_id: UUID = Path(..., description="Application UUID"),
    file_path: str = Path(..., description="Relative file path (can contain slashes)"),
    *,
    ctx: Context,
    user: CurrentUser,
) -> SimpleFileResponse:
    """Create or update a file at the given path.

    Validates the path, then writes via FileStorageService (which handles
    S3 _repo/ storage, file_index update, pubsub, and preview sync).
    """
    app = await get_application_or_404(ctx, app_id)
    # Solution-managed app source is read-only on the platform — only deploy
    # may write it. The before_flush backstop can't see this: it writes to S3 +
    # file_index, never dirtying the Application ORM row. (criterion 6)
    await assert_entity_id_not_solution_managed(ctx.db, Application, app_id)

    # Validate path conventions
    validate_file_path(file_path)

    prefix = app.repo_prefix
    full_path = f"{prefix}{file_path}"
    source = data.source or ""

    storage = get_file_storage_service(ctx.db)
    await storage.write_file(
        path=full_path,
        content=source.encode("utf-8"),
        updated_by=user.email or "unknown",
    )

    # Compile server-side and return compiled code in the response
    compiled_js: str | None = None
    if file_path.endswith((".tsx", ".ts")):
        from src.services.app_compiler import AppCompilerService
        compiler = AppCompilerService()
        result = await compiler.compile_file(source, file_path)
        if result.success:
            compiled_js = result.compiled

    logger.info(f"Wrote app file '{log_safe(file_path)}' for app {log_safe(app_id)} (slug={log_safe(app.slug)})")
    return SimpleFileResponse(path=file_path, source=source, compiled=compiled_js)


@router.delete(
    "/{file_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an app file",
)
async def delete_app_file(
    app_id: UUID = Path(..., description="Application UUID"),
    file_path: str = Path(..., description="Relative file path (can contain slashes)"),
    *,
    ctx: Context,
    _user: CurrentUser,
) -> None:
    """Delete a file at the given path.

    Deletes via FileStorageService (which handles S3 _repo/ deletion,
    file_index cleanup, pubsub, and preview sync).
    """
    app = await get_application_or_404(ctx, app_id)
    # Read-only for solution-managed apps (S3 delete bypasses the ORM backstop).
    await assert_entity_id_not_solution_managed(ctx.db, Application, app_id)
    prefix = app.repo_prefix
    full_path = f"{prefix}{file_path}"

    storage = get_file_storage_service(ctx.db)
    await storage.delete_file(full_path)

    logger.info(f"Deleted app file '{log_safe(file_path)}' from app {log_safe(app_id)} (slug={log_safe(app.slug)})")


# =============================================================================
# Render endpoint — compiled JS only, reads from S3 (_apps/)
# =============================================================================

@render_router.get(
    "/render",
    response_model=AppRenderResponse,
    summary="Get all compiled files for rendering",
)
async def render_app(
    app_id: UUID = Path(..., description="Application UUID"),
    mode: FileMode = FileMode.draft,
    *,
    ctx: Context,
    _user: CurrentUser,
) -> AppRenderResponse:
    """Return all files as compiled JS, ready for client-side execution.

    Reads entirely from S3 (_apps/{app_id}/{mode}/). Compilation happens on
    write in file_ops; this endpoint only serves what's there.

    Unlike /files, this returns only `path` + `code` (no source).
    """
    app = await get_application_or_404(ctx, app_id)
    app_storage = AppStorageService()
    storage_mode = "preview" if mode == FileMode.draft else "live"
    app_id_str = str(app.id)

    # Read dependencies from DB
    dependencies: dict[str, str] = app.dependencies or {}

    # 1. Try Redis cache first
    cached = await app_storage.get_render_cache(app_id_str, storage_mode)
    if cached:
        cached_css: dict[str, str] = {}
        cached_code: list[tuple[str, str]] = []
        for p, c in sorted(cached.items()):
            if p.endswith(".css"):
                cached_css[p] = c
            else:
                cached_code.append((p, c))
        files = [RenderFileResponse(path=p, code=c) for p, c in cached_code]
        return AppRenderResponse(
            files=files, total=len(files), dependencies=dependencies, styles=cached_css,
        )

    # 2. Cache miss — read compiled files from S3
    rel_paths = await app_storage.list_files(app_id_str, storage_mode)
    if not rel_paths:
        return AppRenderResponse(files=[], total=0, dependencies=dependencies)

    # Filter out non-renderable files (app.yaml, .bifrost-lock, editor turds).
    # Only code (tsx/ts/jsx/js) and css are served to the runtime.
    renderable = [
        p for p in rel_paths
        if p.endswith((".tsx", ".ts", ".jsx", ".js", ".mjs", ".css"))
        and ".tmp." not in p
    ]

    # Read all files concurrently — serial was ~20ms * N on cold cache.
    contents = await asyncio.gather(*[
        app_storage.read_file(app_id_str, storage_mode, p) for p in renderable
    ])

    css_files: dict[str, str] = {}
    code_files: dict[str, str] = {}
    for rel_path, content_bytes in zip(renderable, contents):
        content = content_bytes.decode("utf-8", errors="replace")
        if rel_path.endswith(".css"):
            css_files[rel_path] = content
        else:
            code_files[rel_path] = content

    # 3. Generate Tailwind CSS from class candidates in compiled code
    from src.services.app_compiler import AppTailwindService

    tailwind_css = await AppTailwindService.generate_css(list(code_files.values()))
    if tailwind_css:
        css_files["_tailwind.css"] = tailwind_css

    # 4. Warm the Redis cache (include CSS files so cache is complete)
    all_files_for_cache = {**code_files, **css_files}
    await app_storage.set_render_cache(app_id_str, storage_mode, all_files_for_cache)

    # 5. Build response
    files = [
        RenderFileResponse(path=rel_path, code=code)
        for rel_path, code in sorted(code_files.items())
    ]

    return AppRenderResponse(
        files=files, total=len(files), dependencies=dependencies, styles=css_files,
    )


# =============================================================================
# Bundle manifest + asset endpoints (esbuild-bundled path)
# =============================================================================


@render_router.get(
    "/bundle-manifest",
    summary="Get the bundle manifest for an app (esbuild path)",
)
async def get_bundle_manifest(
    app_id: UUID = Path(..., description="Application UUID"),
    mode: FileMode = FileMode.draft,
    *,
    ctx: Context,
    _user: CurrentUser,
) -> dict:
    """Return the manifest.json describing the bundled app.

    Manifest includes entry JS, CSS (if any), and a base URL where the
    hashed chunk files can be fetched. Chunks are served by
    /bundle-asset/{filename}.

    If no manifest exists yet, triggers a build and returns that one.
    """
    app = await get_application_or_404(ctx, app_id)
    app_storage = AppStorageService()
    storage_mode = "preview" if mode == FileMode.draft else "live"
    app_id_str = str(app.id)

    # standalone_v2 apps have NO esbuild bundle/manifest — they're a Vite build
    # served from _apps/{id}/dist/. Surface the hashed entry JS + CSS from the
    # built dist so the shell loads them into a same-document mount (NOT an
    # iframe — the iframe never updated the address bar, breaking deep-links /
    # refresh; Codex P1-b/G7). The client reads these instead of scraping
    # index.html.
    if app.app_model == "standalone_v2":
        import re as _re

        from src.services.solutions.app_build import SolutionAppBuilder

        # The entry chunk + CSS are whatever index.html references — Vite may emit
        # several .js chunks (vendor splits), so the <script type=module src> and
        # <link rel=stylesheet href> in index.html are the source of truth, not a
        # "first .js" guess.
        entry: str | None = None
        css: str | None = None
        try:
            html = (await SolutionAppBuilder().read_dist(app_id_str, "index.html")).decode()
            if m := _re.search(r'<script[^>]+type="module"[^>]+src="([^"]+)"', html):
                entry = m.group(1).split("/dist/")[-1].lstrip("/")
            if m := _re.search(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', html):
                css = m.group(1).split("/dist/")[-1].lstrip("/")
        except Exception:  # noqa: BLE001 - missing/unbuilt dist → entry stays None, shell shows a clear error
            pass
        return {
            "entry": entry,
            "css": css,
            "base_url": f"/api/applications/{app_id}/dist",
            "mode": storage_mode,
            "dependencies": app.dependencies or {},
            "migrated": False,
            "organization_id": str(app.organization_id) if app.organization_id else None,
            "app_model": "standalone_v2",
        }

    import json as _json
    from src.services.app_bundler import SCHEMA_VERSION, build_with_migrate
    from src.core.cache import get_shared_redis

    # A manifest is "stale" when it's missing the schema_version field or has
    # an older value. We treat stale manifests the same as missing manifests:
    # run auto-migration against _repo/<app>/, then rebuild. This is how a
    # deploy that bumps SCHEMA_VERSION transparently heals every app (preview
    # AND live) — the first viewer after deploy pays the migrate+rebuild
    # cost, subsequent views are cached.
    manifest_bytes: bytes | None = None
    try:
        manifest_bytes = await app_storage.read_file(
            app_id_str, storage_mode, "manifest.json"
        )
    except FileNotFoundError:
        manifest_bytes = None

    needs_rebuild = manifest_bytes is None
    if manifest_bytes is not None:
        try:
            parsed = _json.loads(manifest_bytes)
        except (ValueError, TypeError):
            parsed = None
        if parsed is None:
            needs_rebuild = True
        else:
            existing_version = parsed.get("schema_version")
            if not isinstance(existing_version, int) or existing_version < SCHEMA_VERSION:
                needs_rebuild = True

    if needs_rebuild:
        repo_prefix = app.repo_prefix
        # Serialize migrate+rebuild across concurrent first-viewers so two
        # requests don't double-migrate or race on writes. Hold the lock
        # across migrate+build so the second caller sees a valid manifest on
        # retry rather than partial state. Separate locks per-mode so a
        # preview rebuild doesn't block a live viewer on the same app.
        lock_key = f"bifrost:automigrate:{app_id_str}:{storage_mode}"
        lock_ttl = 60
        migrated = False
        redis = await get_shared_redis()
        acquired = bool(await redis.set(lock_key, "1", nx=True, ex=lock_ttl))
        if not acquired:
            # Another request is building. Wait briefly, then retry — the
            # holder will have written a fresh manifest with the current
            # schema_version by the time we re-read.
            import asyncio as _asyncio

            for _ in range(40):  # ~20s total
                await _asyncio.sleep(0.5)
                try:
                    manifest_bytes = await app_storage.read_file(
                        app_id_str, storage_mode, "manifest.json"
                    )
                    m = _json.loads(manifest_bytes)
                    v = m.get("schema_version")
                    if isinstance(v, int) and v >= SCHEMA_VERSION:
                        return {
                            "entry": m.get("entry"),
                            "css": m.get("css"),
                            "base_url": f"/api/applications/{app_id}/bundle-asset",
                            "mode": storage_mode,
                            "dependencies": m.get("dependencies") or (app.dependencies or {}),
                            "migrated": False,
                            "organization_id": str(app.organization_id) if app.organization_id else None,
                            "app_model": app.app_model,
                        }
                except FileNotFoundError:
                    continue
            raise HTTPException(
                status_code=503,
                detail="Auto-migrate lock held by another request; manifest never appeared",
            )
        try:
            result, migrated = await build_with_migrate(
                app_id_str, repo_prefix, storage_mode,
                dependencies=app.dependencies or {},
            )
            if not migrated:
                logger.info(f"No migration needed for app={app_id_str}")
        finally:
            await redis.delete(lock_key)

        if not result.success or result.manifest is None:
            first_err = (result.errors or [None])[0]
            err_text = first_err.text if first_err else "unknown error"
            raise HTTPException(status_code=500, detail=f"Bundle build failed: {err_text}")
        manifest = result.manifest
        return {
            "entry": manifest.entry,
            "css": manifest.css,
            "base_url": f"/api/applications/{app_id}/bundle-asset",
            "mode": storage_mode,
            "dependencies": manifest.dependencies,
            # Surface the banner on the build that actually rewrote source,
            # so the developer knows to pull.
            "migrated": migrated,
            "organization_id": str(app.organization_id) if app.organization_id else None,
            "app_model": app.app_model,
        }

    assert manifest_bytes is not None
    m = _json.loads(manifest_bytes)
    return {
        "entry": m.get("entry"),
        "css": m.get("css"),
        "base_url": f"/api/applications/{app_id}/bundle-asset",
        "mode": storage_mode,
        "dependencies": m.get("dependencies") or (app.dependencies or {}),
        "migrated": False,
        # Org-scoped app: clients use this to default `scope` for tables.* /
        # useTable calls inside the app, mirroring how org-scoped workflows
        # always run as their org. Global apps return null and fall back to
        # caller's-org behavior.
        "organization_id": str(app.organization_id) if app.organization_id else None,
        "app_model": app.app_model,
    }


@render_router.get(
    "/bundle-asset/{filename:path}",
    summary="Serve a bundled asset file (JS/CSS/sourcemap)",
)
async def get_bundle_asset(
    app_id: UUID = Path(..., description="Application UUID"),
    filename: str = Path(..., description="Bundle asset filename"),
    mode: FileMode = FileMode.draft,
    *,
    ctx: Context,
    _user: CurrentUser,
):
    """Stream a bundled asset file from S3.

    The browser loads these via <script type="module" src="...">, so
    correct MIME types matter.
    """
    from fastapi.responses import Response

    app = await get_application_or_404(ctx, app_id)
    app_storage = AppStorageService()
    storage_mode = "preview" if mode == FileMode.draft else "live"

    try:
        data = await app_storage.read_file(str(app.id), storage_mode, filename)
    except FileNotFoundError:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Asset not found: {filename}")

    if filename.endswith(".js"):
        media_type = "application/javascript"
    elif filename.endswith(".css"):
        media_type = "text/css"
    elif filename.endswith(".map"):
        media_type = "application/json"
    else:
        media_type = "application/octet-stream"

    # Hashed filenames are immutable — cache aggressively.
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@render_router.get(
    "/dist/{path:path}",
    summary="Serve a standalone_v2 app's built dist/ file from _apps/{id}/dist/",
)
async def get_v2_dist_asset(
    app_id: UUID = Path(..., description="Application UUID"),
    path: str = Path(..., description="Path within the app's dist/ (e.g. index.html)"),
    *,
    ctx: Context,
    _user: CurrentUser,
):
    """Stream a built dist/ file for a standalone_v2 app.

    BundledAppShell mounts a v2 app SAME-DOCUMENT (not an iframe): it reads the
    hashed entry/css from the bundle-manifest and loads them from this dist/
    prefix; the app's own bundle pulls any further chunks from here too.
    """
    from botocore.exceptions import ClientError
    from fastapi import HTTPException
    from fastapi.responses import Response

    from src.services.solutions.app_build import SolutionAppBuilder

    app = await get_application_or_404(ctx, app_id)
    rel = path or "index.html"
    try:
        data = await SolutionAppBuilder().read_dist(str(app.id), rel)
    except ClientError as exc:
        # read_dist surfaces a missing key as the raw botocore ClientError from
        # get_object (Code=NoSuchKey). Only THAT is a 404 — anything else is a
        # real storage failure and must surface, not masquerade as not-found.
        if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
            raise HTTPException(
                status_code=404, detail=f"dist asset not found: {rel}"
            ) from exc
        logger.exception("dist asset read failed: app=%s rel=%s", app.id, log_safe(rel))
        raise
    except Exception:
        logger.exception("dist asset read failed: app=%s rel=%s", app.id, log_safe(rel))
        raise

    if rel.endswith(".html"):
        media_type = "text/html"
    elif rel.endswith(".js"):
        media_type = "application/javascript"
    elif rel.endswith(".css"):
        media_type = "text/css"
    elif rel.endswith(".map") or rel.endswith(".json"):
        media_type = "application/json"
    else:
        media_type = "application/octet-stream"

    # index.html must not be cached (it references hashed assets); assets are
    # immutable.
    cache = "no-cache" if rel.endswith(".html") else "public, max-age=31536000, immutable"
    return Response(content=data, media_type=media_type, headers={"Cache-Control": cache})


# =============================================================================
# Dependencies endpoints — read/write Application.dependencies
# =============================================================================


@render_router.get(
    "/dependencies",
    response_model=dict[str, str],
    summary="Get app dependencies",
)
async def get_dependencies(
    app_id: UUID = Path(..., description="Application UUID"),
    *,
    ctx: Context,
    _user: CurrentUser,
) -> dict[str, str]:
    """Return the app's npm dependencies."""
    app = await get_application_or_404(ctx, app_id)
    return app.dependencies or {}


@render_router.put(
    "/dependencies",
    response_model=dict[str, str],
    summary="Update app dependencies",
)
async def put_dependencies(
    deps: dict[str, str],
    app_id: UUID = Path(..., description="Application UUID"),
    *,
    ctx: Context,
    _user: CurrentUser,
) -> dict[str, str]:
    """Replace the app's npm dependencies.

    Validates every package name and version, enforces the max-dependency limit.
    """
    app = await get_application_or_404(ctx, app_id)
    # Dependencies are solution-owned metadata — read-only for managed apps.
    await assert_entity_id_not_solution_managed(ctx.db, Application, app_id)

    # Validate
    if len(deps) > _MAX_DEPENDENCIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many dependencies (max {_MAX_DEPENDENCIES})",
        )
    for name, version in deps.items():
        if not _PKG_NAME_RE.match(name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid package name: {name}",
            )
        if not _VERSION_RE.match(version):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid version for {name}: {version}",
            )

    # Update DB
    app.dependencies = deps if deps else None
    await ctx.db.commit()

    # Invalidate render cache
    app_storage = AppStorageService()
    await app_storage.invalidate_render_cache(str(app.id))

    return deps
