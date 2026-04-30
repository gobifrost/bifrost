"""
Unified Files Router

File operations with two storage modes:
- local: Local filesystem (CWD, /tmp/bifrost/temp, /tmp/bifrost/uploads)
- cloud: S3 storage (default)

Auth: CurrentSuperuser (platform admins and workflow engine)
"""

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context, CurrentSuperuser
from src.core.log_safety import log_safe
from src.models.contracts.files import (
    FilePullRequest,
    FilePullResponse,
    ManifestImportResponse,
    WatchSessionRequest,
)
from src.core.database import get_db
from src.models import (
    AffectedEntity,
    AvailableReplacement,
    FileContentRequest,
    FileContentResponse,
    FileConflictResponse,
    FileDiagnostic,
    FileMetadata,
    FileType,
    PendingDeactivation,
    SearchRequest,
    SearchResponse,
    WorkflowIdConflict,
)
from src.services.editor.search import search_files_db
from src.services.file_backend import get_backend
from src.services.file_storage import FileStorageService

# Watch session TTL — must be > CLI heartbeat interval (WATCH_HEARTBEAT_SECONDS in bifrost.cli)
WATCH_SESSION_TTL_SECONDS = 120

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["Files"])


# =============================================================================
# Request Models with Mode Parameter
# =============================================================================

Mode = Literal["local", "cloud"]

# Location is now a free string; reserved-vs-freeform validation lives in
# `shared.file_paths.validate_location_name` and is applied by the resolver.


class FileReadRequest(BaseModel):
    """Request to read a file."""
    path: str = Field(..., description="File path relative to location root")
    location: str = Field(default="workspace", description="Storage location: reserved (workspace, temp, uploads) or freeform")
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")
    binary: bool = Field(default=False, description="If true, return base64-encoded content")


class FileWriteRequest(BaseModel):
    """Request to write a file."""
    path: str = Field(..., description="File path relative to location root")
    content: str = Field(..., description="File content (text or base64 for binary)")
    location: str = Field(default="workspace", description="Storage location: reserved (workspace, temp, uploads) or freeform")
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")
    binary: bool = Field(default=False, description="If true, content is base64-encoded")


class FileDeleteRequest(BaseModel):
    """Request to delete a file."""
    path: str = Field(..., description="File path relative to location root")
    location: str = Field(default="workspace", description="Storage location: reserved (workspace, temp, uploads) or freeform")
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")


class FileListRequest(BaseModel):
    """Request to list files."""
    directory: str = Field(default="", description="Directory path relative to location root")
    location: str = Field(default="workspace", description="Storage location: reserved (workspace, temp, uploads) or freeform")
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")
    include_metadata: bool = Field(default=False, description="If true, return ETags + last_modified per file")


class FileExistsRequest(BaseModel):
    """Request to check file existence."""
    path: str = Field(..., description="File path relative to location root")
    location: str = Field(default="workspace", description="Storage location: reserved (workspace, temp, uploads) or freeform")
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")


class FileReadResponse(BaseModel):
    """Response for file read."""
    content: str = Field(..., description="File content (text or base64)")
    binary: bool = Field(default=False, description="True if content is base64-encoded")


class FileListMetadataItem(BaseModel):
    """File metadata item with path, etag, and last_modified."""
    path: str
    etag: str
    last_modified: str  # ISO 8601
    updated_by: str | None = None


class FileListResponse(BaseModel):
    """Response for file listing."""
    files: list[str] = Field(default_factory=list, description="List of file/folder paths")
    files_metadata: list[FileListMetadataItem] = Field(default_factory=list, description="Per-file metadata (when include_metadata=true)")


class FileExistsResponse(BaseModel):
    """Response for file existence check."""
    exists: bool = Field(..., description="True if file exists")


class SignedUrlRequest(BaseModel):
    """Request to generate a presigned S3 URL."""
    path: str = Field(..., description="File path relative to location root (NOT including scope segment)")
    method: Literal["PUT", "GET"] = Field(default="PUT", description="HTTP method: PUT for upload, GET for download")
    content_type: str = Field(default="application/octet-stream", description="MIME type (only used for PUT)")
    location: str = Field(default="uploads", description="Storage location. Defaults to 'uploads' for backwards compatibility with form upload flows.")
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")


class SignedUrlResponse(BaseModel):
    """Response with presigned URL."""
    url: str = Field(..., description="Presigned S3 URL")
    path: str = Field(..., description="Full S3 path")
    expires_in: int = Field(default=600, description="URL expiration in seconds")


# =============================================================================
# Basic CRUD Endpoints (SDK-focused)
# =============================================================================


@router.post("/read", response_model=FileReadResponse)
async def read_file(
    request: FileReadRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FileReadResponse:
    """Read a file from workspace, temp, or uploads."""
    try:
        backend = get_backend(request.mode, db)
        content = await backend.read(request.path, request.location, scope=request.scope)

        if request.binary:
            return FileReadResponse(content=base64.b64encode(content).decode(), binary=True)
        return FileReadResponse(content=content.decode("utf-8"), binary=False)

    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.path}",
        )
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is binary. Use binary=true to read as base64.",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/write", status_code=status.HTTP_204_NO_CONTENT)
async def write_file(
    request: FileWriteRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Write a file to workspace, temp, or uploads."""
    try:
        backend = get_backend(request.mode, db)

        if request.binary:
            content = base64.b64decode(request.content)
        else:
            content = request.content.encode("utf-8")

        updated_by = user.email if user else "system"
        await backend.write(request.path, content, request.location, updated_by, scope=request.scope)

        logger.info(f"Wrote file: {log_safe(request.path)} ({len(content)} bytes, mode={log_safe(request.mode)}, location={log_safe(request.location)})")

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    request: FileDeleteRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a file from workspace, temp, or uploads."""
    try:
        backend = get_backend(request.mode, db)
        await backend.delete(request.path, request.location, scope=request.scope)

        logger.info(f"Deleted file: {log_safe(request.path)} (mode={log_safe(request.mode)}, location={log_safe(request.location)})")

    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.path}",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/list", response_model=FileListResponse)
async def list_files_simple(
    request: FileListRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FileListResponse:
    """List files in a directory (simple SDK-focused endpoint)."""
    try:
        if request.include_metadata and request.mode == "cloud" and request.location == "workspace":
            # Return ETags + last_modified via RepoStorage
            from src.services.repo_storage import RepoStorage

            repo = RepoStorage()
            s3_metadata = await repo.list_with_metadata(request.directory)

            # Filter out .git/ objects
            s3_metadata = {
                path: meta for path, meta in s3_metadata.items()
                if not path.startswith(".git/")
            }

            # Look up updated_by from file_index
            from src.models.orm.file_index import FileIndex
            fi_result = await db.execute(
                select(FileIndex.path, FileIndex.updated_by).where(
                    FileIndex.path.in_(list(s3_metadata.keys()))
                )
            )
            author_lookup = {row.path: row.updated_by for row in fi_result.all()}

            return FileListResponse(
                files=sorted(s3_metadata.keys()),
                files_metadata=[
                    FileListMetadataItem(
                        path=path,
                        etag=meta.etag,
                        last_modified=meta.last_modified.isoformat(),
                        updated_by=author_lookup.get(path),
                    )
                    for path, meta in sorted(s3_metadata.items())
                ],
            )

        backend = get_backend(request.mode, db)
        files = await backend.list(request.directory, request.location, scope=request.scope)
        return FileListResponse(files=files)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/exists", response_model=FileExistsResponse)
async def file_exists(
    request: FileExistsRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FileExistsResponse:
    """Check if a file exists."""
    try:
        backend = get_backend(request.mode, db)
        exists = await backend.exists(request.path, request.location, scope=request.scope)
        return FileExistsResponse(exists=exists)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/signed-url", response_model=SignedUrlResponse)
async def get_signed_url(
    request: SignedUrlRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> SignedUrlResponse:
    """Generate a presigned S3 URL for direct file upload or download.

    Path resolution goes through `shared.file_paths.resolve_s3_key`, so the
    URL targets the same key as a `files.read`/`files.write` to the same
    `(location, scope, path)`.
    """
    from shared.file_paths import resolve_s3_key

    try:
        s3_path = resolve_s3_key(request.location, request.scope, request.path)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    file_storage = FileStorageService(db)

    if request.method == "PUT":
        url = await file_storage.generate_presigned_upload_url(
            path=s3_path,
            content_type=request.content_type,
        )
    else:
        url = await file_storage.generate_presigned_download_url(
            path=s3_path,
        )

    return SignedUrlResponse(
        url=url,
        path=s3_path,
    )


# =============================================================================
# Pull & Manifest Endpoints (CLI-focused)
# =============================================================================


@router.post("/pull", response_model=FilePullResponse)
async def pull_files(
    request: FilePullRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FilePullResponse:
    """
    Pull manifest files from server that differ from local state.

    Only returns regenerated .bifrost/*.yaml from DB state.
    Code file reconciliation is handled by git, not by this endpoint.
    """
    from src.services.manifest_generator import generate_manifest
    from bifrost.manifest import serialize_manifest_dir

    manifest_files: dict[str, str] = {}
    try:
        manifest = await generate_manifest(db)
        all_manifest_files = serialize_manifest_dir(manifest)
        for filename, content in all_manifest_files.items():
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            local_hash = None
            for key_candidate in [
                f".bifrost/{filename}",
                f"{request.prefix}/.bifrost/{filename}" if request.prefix else None,
                f"{request.prefix.rstrip('/')}/.bifrost/{filename}" if request.prefix else None,
            ]:
                if key_candidate and key_candidate in request.local_hashes:
                    local_hash = request.local_hashes[key_candidate]
                    break
            if local_hash != content_hash:
                manifest_files[filename] = content
    except Exception as e:
        logger.warning(f"Error generating manifest: {e}")

    return FilePullResponse(
        files={},
        deleted=[],
        manifest_files=manifest_files,
    )


@router.get("/manifest")
async def get_manifest(
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Return regenerated manifest files from DB state."""
    from src.services.manifest_generator import generate_manifest
    from bifrost.manifest import serialize_manifest_dir

    manifest = await generate_manifest(db)
    return serialize_manifest_dir(manifest)


class ManifestImportRequest(BaseModel):
    """Request body for manifest import."""
    delete_removed_entities: bool = False
    files: dict[str, str] = Field(default_factory=dict, description="Map of .bifrost/ path to base64-encoded content")
    dry_run: bool = False
    target_organization_id: UUID | None = Field(
        default=None,
        description=(
            "When set, every entity in the bundle has its organization_id rewritten to this "
            "value before upsert. Incompatible with a manifest that carries an organizations section."
        ),
    )
    role_resolution: Literal["uuid", "name"] = Field(
        default="uuid",
        description=(
            "How to interpret role references in the bundle. 'uuid' (default) assumes role UUIDs "
            "match the target env. 'name' reads role_names and resolves to UUIDs in the target; "
            "missing names fail with 422."
        ),
    )
    entity_ids: set[str] | None = Field(
        default=None,
        description=(
            "Optional subset of entity UUIDs to apply. When set, only entities whose id is in "
            "this set are written; all other diff entries are skipped. Use for interactive "
            "cherry-pick import where the user approves a subset of a dry-run diff."
        ),
    )


@router.post("/manifest/import", response_model=ManifestImportResponse)
async def import_manifest(
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
    request: ManifestImportRequest | None = None,
) -> ManifestImportResponse:
    """Import .bifrost/ manifest files from S3 into DB."""
    from src.services.manifest_import import import_manifest_from_repo

    # Write provided .bifrost/ files to S3
    if request and request.files:
        from src.services.repo_storage import RepoStorage
        import base64 as b64_mod
        repo = RepoStorage()
        for repo_path, content in request.files.items():
            try:
                content_bytes = b64_mod.b64decode(content)
                # Normalize: strip any prefix before .bifrost/
                parts = repo_path.replace("\\", "/").split("/")
                try:
                    bifrost_idx = parts.index(".bifrost")
                    canonical_path = "/".join(parts[bifrost_idx:])
                except ValueError:
                    canonical_path = repo_path
                await repo.write(canonical_path, content_bytes)
            except Exception as e:
                logger.warning(f"Error writing manifest file {repo_path}: {e}")

    delete_entities = request.delete_removed_entities if request else False
    dry_run = request.dry_run if request else False
    target_org = request.target_organization_id if request else None
    role_resolution = request.role_resolution if request else "uuid"
    entity_ids = request.entity_ids if request else None

    try:
        result = await import_manifest_from_repo(
            db,
            delete_removed_entities=delete_entities,
            dry_run=dry_run,
            target_organization_id=target_org,
            role_resolution=role_resolution,
            entity_ids=entity_ids,
        )
    except ValueError as e:
        # Cross-env rebinding precondition failure (orgs+target clash, unknown role, etc.)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    if not dry_run:
        await db.commit()

    return ManifestImportResponse(
        applied=result.applied,
        dry_run=result.dry_run,
        warnings=result.warnings,
        manifest_files=result.manifest_files,
        modified_files=result.modified_files,
        deleted_entities=result.deleted_entities,
        entity_changes=result.entity_changes,
    )


# =============================================================================
# Watch Session Endpoints (CLI watch mode)
# =============================================================================


@router.post("/watch")
async def manage_watch_session(
    request: WatchSessionRequest,
    user: CurrentSuperuser,
) -> dict:
    """Register, heartbeat, or deregister a CLI watch session."""
    from src.core.cache.redis_client import get_shared_redis
    from src.core.pubsub import publish_file_activity

    session_id = request.session_id or "unknown"
    key = f"bifrost:watch:{user.user_id}:{request.prefix}"
    r = await get_shared_redis()

    if request.action in ("start", "heartbeat"):
        await r.setex(key, WATCH_SESSION_TTL_SECONDS, json.dumps({
            "user_id": str(user.user_id),
            "user_name": user.name or user.email or "CLI",
            "prefix": request.prefix,
            "session_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }))
        if request.action == "start":
            await publish_file_activity(
                user_id=str(user.user_id),
                user_name=user.name or user.email or "CLI",
                activity_type="watch_start",
                prefix=request.prefix,
                session_id=session_id,
            )
    elif request.action == "stop":
        await r.delete(key)
        await publish_file_activity(
            user_id=str(user.user_id),
            user_name=user.name or user.email or "CLI",
            activity_type="watch_stop",
            prefix=request.prefix,
            session_id=session_id,
        )
    return {"ok": True}


@router.get("/watchers")
async def list_active_watchers(user: CurrentSuperuser) -> dict:
    """List active CLI watch sessions."""
    from src.core.cache.redis_client import get_shared_redis

    r = await get_shared_redis()
    keys = [k async for k in r.scan_iter("bifrost:watch:*")]
    watchers = []
    for key in keys:
        data = await r.get(key)
        if data:
            watchers.append(json.loads(data))
    return {"watchers": watchers}


# =============================================================================
# Editor Endpoints (Cloud mode only, with rich metadata)
# These endpoints are used by the browser-based editor and maintain
# backward compatibility with /api/editor/files/* functionality.
# =============================================================================



@router.get(
    "/editor",
    response_model=list[FileMetadata],
    summary="List directory contents (editor)",
)
async def list_files_editor(
    ctx: Context,
    user: CurrentSuperuser,
    path: str = Query(..., description="Directory path relative to workspace root"),
    recursive: bool = Query(default=False, description="If true, return all files recursively"),
    db: AsyncSession = Depends(get_db),
) -> list[FileMetadata]:
    """
    List files and folders in a directory with rich metadata.

    Cloud mode only - used by browser editor.
    Lists directly from S3 via RepoStorage (source of truth).
    """
    from src.services.repo_storage import RepoStorage

    try:
        repo = RepoStorage()

        # Normalize path: "." or "" means root
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"

        if recursive:
            from src.services.editor.file_filter import is_excluded_path
            all_paths = await repo.list(prefix)
            return [
                FileMetadata(
                    path=p,
                    name=p.split("/")[-1],
                    type=FileType.FILE,
                    size=None,
                    extension=p.split(".")[-1] if "." in p.split("/")[-1] else None,
                    modified=datetime.now(timezone.utc).isoformat(),
                )
                for p in sorted(all_paths)
                if not is_excluded_path(p)
            ]

        # Non-recursive: get direct children
        child_files, child_folders = await repo.list_directory(prefix)

        files: list[FileMetadata] = []

        # Folders first
        for folder_path in child_folders:
            clean = folder_path.rstrip("/")
            files.append(FileMetadata(
                path=clean,
                name=clean.split("/")[-1],
                type=FileType.FOLDER,
                size=None,
                extension=None,
                modified=datetime.now(timezone.utc).isoformat(),
            ))

        # Then files
        for file_path in child_files:
            name = file_path.split("/")[-1]
            files.append(FileMetadata(
                path=file_path,
                name=name,
                type=FileType.FILE,
                size=None,
                extension=name.split(".")[-1] if "." in name else None,
                modified=datetime.now(timezone.utc).isoformat(),
            ))

        return files

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/editor/content",
    response_model=FileContentResponse,
    summary="Read file content (editor)",
)
async def get_file_content_editor(
    ctx: Context,
    user: CurrentSuperuser,
    path: str = Query(..., description="File path relative to workspace root"),
    db: AsyncSession = Depends(get_db),
) -> FileContentResponse:
    """
    Read file content with rich metadata.

    Cloud mode only - used by browser editor.
    """
    try:
        storage = FileStorageService(db)
        content, _ = await storage.read_file(path)

        # Determine encoding
        encoding = "utf-8"
        try:
            content_str = content.decode("utf-8")
        except UnicodeDecodeError:
            encoding = "base64"
            content_str = base64.b64encode(content).decode("ascii")

        etag = hashlib.md5(content).hexdigest()

        return FileContentResponse(
            path=path,
            content=content_str,
            encoding=encoding,
            size=len(content),
            etag=etag,
            modified=datetime.now(timezone.utc).isoformat(),
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {path}")


@router.put(
    "/editor/content",
    response_model=FileContentResponse,
    summary="Write file content (editor)",
    responses={409: {"model": FileConflictResponse, "description": "File conflict"}},
)
async def put_file_content_editor(
    request: FileContentRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FileContentResponse:
    """
    Write file content with conflict detection.

    Cloud mode only - used by browser editor.
    """
    try:
        storage = FileStorageService(db)

        # Convert content to bytes
        if request.encoding == "base64":
            content = base64.b64decode(request.content)
        else:
            content = request.content.encode("utf-8")

        # Handle etag validation
        if request.expected_etag:
            try:
                existing_content, _ = await storage.read_file(request.path)
                existing_etag = hashlib.md5(existing_content).hexdigest()
                if existing_etag != request.expected_etag:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={"reason": "content_changed", "message": "File has been modified"}
                    )
            except FileNotFoundError:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"reason": "path_not_found", "message": "File was deleted"}
                )

        # Write file with deactivation protection
        updated_by = user.email if user else "system"
        write_result = await storage.write_file(
            request.path,
            content,
            updated_by,
            force_deactivation=request.force_deactivation,
            replacements=request.replacements,
            workflows_to_deactivate=request.workflows_to_deactivate,
        )

        # Check for pending deactivations - return 409 if any
        if write_result.pending_deactivations:
            pending = [
                PendingDeactivation(
                    id=pd.id,
                    name=pd.name,
                    function_name=pd.function_name,
                    path=pd.path,
                    description=pd.description,
                    decorator_type=pd.decorator_type,  # type: ignore[arg-type]
                    has_executions=pd.has_executions,
                    last_execution_at=pd.last_execution_at,
                    endpoint_enabled=pd.endpoint_enabled,
                    affected_entities=[
                        AffectedEntity(
                            entity_type=ae["entity_type"],  # type: ignore[arg-type]
                            id=ae["id"],
                            name=ae["name"],
                            reference_type=ae["reference_type"],
                        )
                        for ae in pd.affected_entities
                    ],
                )
                for pd in write_result.pending_deactivations
            ]
            replacements = [
                AvailableReplacement(
                    function_name=ar.function_name,
                    name=ar.name,
                    decorator_type=ar.decorator_type,  # type: ignore[arg-type]
                    similarity_score=ar.similarity_score,
                )
                for ar in (write_result.available_replacements or [])
            ]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason": "workflows_would_deactivate",
                    "message": f"{len(pending)} workflow(s) would be deactivated",
                    "pending_deactivations": [p.model_dump() for p in pending],
                    "available_replacements": [r.model_dump() for r in replacements],
                }
            )

        etag = hashlib.md5(write_result.final_content).hexdigest()

        if write_result.content_modified:
            response_content = write_result.final_content.decode("utf-8")
            response_encoding = "utf-8"
            response_size = len(write_result.final_content)
        else:
            response_content = request.content
            response_encoding = request.encoding
            response_size = len(content)

        # Convert conflicts to response model
        conflicts = []
        if write_result.workflow_id_conflicts:
            for c in write_result.workflow_id_conflicts:
                conflicts.append(WorkflowIdConflict(
                    name=c.name,
                    function_name=c.function_name,
                    existing_id=c.existing_id,
                    file_path=c.file_path,
                ))

        # Convert diagnostics to response model
        diagnostics = []
        if write_result.diagnostics:
            for d in write_result.diagnostics:
                diagnostics.append(FileDiagnostic(
                    severity=d.severity,  # type: ignore[arg-type]
                    message=d.message,
                    line=d.line,
                    column=d.column,
                    source=d.source,
                ))

        return FileContentResponse(
            path=request.path,
            content=response_content,
            encoding=response_encoding,
            size=response_size,
            etag=etag,
            modified=datetime.now(timezone.utc).isoformat(),
            content_modified=write_result.content_modified,
            needs_indexing=write_result.needs_indexing,
            workflow_id_conflicts=conflicts,
            diagnostics=diagnostics,
        )

    except HTTPException:
        raise
    except ValueError as e:
        error_msg = str(e)
        if error_msg.startswith("CONFLICT:"):
            parts = error_msg.split(":", 2)
            if len(parts) == 3:
                _, reason, message = parts
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"reason": reason, "message": message}
                )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)


@router.post(
    "/editor/folder",
    response_model=FileMetadata,
    status_code=status.HTTP_201_CREATED,
    summary="Create folder (editor)",
)
async def create_folder_editor(
    ctx: Context,
    user: CurrentSuperuser,
    path: str = Query(..., description="Folder path relative to workspace root"),
    db: AsyncSession = Depends(get_db),
) -> FileMetadata:
    """
    Create a new folder.

    Cloud mode only - used by browser editor.
    """
    try:
        storage = FileStorageService(db)
        updated_by = user.email if user else "system"
        await storage.create_folder(path, updated_by)

        clean_path = path.rstrip("/")
        return FileMetadata(
            path=clean_path,
            name=clean_path.split("/")[-1],
            type=FileType.FOLDER,
            size=None,
            extension=None,
            modified=datetime.now(timezone.utc).isoformat(),
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete(
    "/editor",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete file or folder (editor)",
)
async def delete_file_editor(
    ctx: Context,
    user: CurrentSuperuser,
    path: str = Query(..., description="File or folder path"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a file or folder recursively.

    Cloud mode only - used by browser editor.
    Uses S3 prefix listing to detect folders (no file_index markers needed).
    """
    from src.services.repo_storage import RepoStorage

    try:
        storage = FileStorageService(db)
        repo = RepoStorage()

        # Check if this is a folder by listing S3 for children
        folder_prefix = path.rstrip("/") + "/"
        children = await repo.list(folder_prefix)

        if children:
            # Folder delete: delete each child; any failure should fail request
            for child_path in children:
                await storage.delete_file(child_path)
        else:
            # Single file delete
            await storage.delete_file(path)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Not found: {path}")


@router.post(
    "/editor/rename",
    response_model=FileMetadata,
    summary="Rename or move file/folder (editor)",
)
async def rename_file_editor(
    ctx: Context,
    user: CurrentSuperuser,
    old_path: str = Query(..., description="Current path"),
    new_path: str = Query(..., description="New path"),
    db: AsyncSession = Depends(get_db),
) -> FileMetadata:
    """
    Rename or move a file or folder.

    For platform entities (workflows, forms, apps, agents), this updates the path
    in file_index and the entity table, preserving all metadata.

    For regular files, copies content in S3 and updates file_index.

    Cloud mode only - used by browser editor.
    """
    try:
        storage = FileStorageService(db)

        # Use move_file which preserves entity associations
        await storage.move_file(old_path, new_path)

        is_folder = new_path.endswith("/")
        return FileMetadata(
            path=new_path,
            name=new_path.split("/")[-1] if not is_folder else new_path.split("/")[-2],
            type=FileType.FOLDER if is_folder else FileType.FILE,
            size=None,
            extension=new_path.split(".")[-1] if "." in new_path and not is_folder else None,
            modified=datetime.now(timezone.utc).isoformat(),
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Not found: {old_path}")
    except FileExistsError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Already exists: {new_path}")


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Search file contents",
)
async def search_file_contents(
    request: SearchRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """
    Search file contents for text or regex patterns.

    Searches database directly - workflows, modules, forms, and agents.
    """
    try:
        results = await search_files_db(db, request, root_path="")
        return results

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
