"""
Unified Files Router

File operations with two storage modes:
- local: Local filesystem (CWD, /tmp/bifrost/temp, /tmp/bifrost/uploads)
- cloud: S3 storage (default)

Auth: CurrentSuperuser (platform admins and workflow engine)
"""

import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Literal, TypeVar, cast
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context, CurrentActiveUser, CurrentSuperuser
from src.core.org_filter import resolve_target_org
from src.core.principal import UserPrincipal
from src.core.log_safety import log_safe
from src.models.contracts.files import (
    FilePullRequest,
    FilePullResponse,
    WatchSessionRequest,
)
from src.models.contracts.policies import FileAction
from src.models.contracts.policies import FilePolicies
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
from shared.role_cache import get_user_roles

# Watch session TTL — must be > CLI heartbeat interval (WATCH_HEARTBEAT_SECONDS in bifrost.cli)
WATCH_SESSION_TTL_SECONDS = 120

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["Files"])
_USE_CONTEXT_SOLUTION_ID = object()
_T = TypeVar("_T")


# =============================================================================
# Request Models with Mode Parameter
# =============================================================================

Mode = Literal["local", "cloud"]

# Location is now a free string; managed-vs-freeform validation lives in
# `shared.file_paths.validate_location_name` and is applied by the resolver.
FILE_LOCATION_DESCRIPTION = (
    "Storage location. Special values: workspace (default), temp, uploads. "
    "Custom names like reports are accepted; internal prefixes _repo, _tmp, "
    "and _apps are blocked."
)


class FileReadRequest(BaseModel):
    """Request to read a file."""
    path: str = Field(..., description="File path relative to location root")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")
    binary: bool = Field(default=False, description="If true, return base64-encoded content")


class FileWriteRequest(BaseModel):
    """Request to write a file."""
    path: str = Field(..., description="File path relative to location root")
    content: str = Field(..., description="File content (text or base64 for binary)")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")
    binary: bool = Field(default=False, description="If true, content is base64-encoded")


class FileDeleteRequest(BaseModel):
    """Request to delete a file."""
    path: str = Field(..., description="File path relative to location root")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")


class FileListRequest(BaseModel):
    """Request to list files."""
    directory: str = Field(default="", description="Directory path relative to location root")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")
    include_metadata: bool = Field(default=False, description="If true, return ETags + last_modified per file")


class FileExistsRequest(BaseModel):
    """Request to check file existence."""
    path: str = Field(..., description="File path relative to location root")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)
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


class SignedUrlBatchRequest(BaseModel):
    """Request to generate several presigned URLs."""
    requests: list[SignedUrlRequest] = Field(..., min_length=1, max_length=100)


class SignedUrlBatchResult(BaseModel):
    """Per-path presigned URL result."""
    path: str = Field(..., description="Original request path")
    resolved_path: str | None = Field(default=None, description="Resolved S3 path")
    method: Literal["PUT", "GET"]
    url: str | None = None
    expires_in: int = 600
    error: str | None = None
    status_code: int = 200


class SignedUrlBatchResponse(BaseModel):
    """Batch presigned URL response."""
    results: list[SignedUrlBatchResult]


class SignedUploadCompleteRequest(BaseModel):
    """Request to finalize metadata after a successful browser presigned PUT."""
    path: str = Field(..., description="File path relative to location root")
    content_type: str = Field(default="application/octet-stream", description="Uploaded MIME type")
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    location: str = Field(default="uploads", description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")


class FileStructureRequest(BaseModel):
    """Request for the admin-only structural listing endpoint."""
    location: str | None = Field(default=None, description="Location to list; omit to discover shares")
    prefix: str = Field(default="", description="Prefix under the location")
    scope: str | None = Field(default=None, description="Org scope: None/'global' or a UUID")


class FileStructureResponse(BaseModel):
    """Structural listing result. `shares` for discover mode, `entries` for a prefix."""
    shares: list[dict] | None = None
    entries: list[dict] | None = None


class FilePolicyPublic(BaseModel):
    id: str
    organization_id: str | None = None
    location: str
    path: str
    policies: FilePolicies


class FilePolicyListResponse(BaseModel):
    policies: list[FilePolicyPublic] = Field(default_factory=list)


class FilePolicySetRequest(BaseModel):
    policies: FilePolicies | list[dict]


class FilePolicyAccessTestRequest(BaseModel):
    path: str
    location: str = "workspace"
    action: FileAction
    scope: str | None = None
    user_id: str | None = None


class FilePolicyAccessTestResponse(BaseModel):
    allowed: bool
    path: str
    location: str
    action: FileAction
    matched_policy: str | None = None
    matched_rule: str | None = None
    denial_reason: str | None = None


# =============================================================================
# File Policy Helpers
# =============================================================================


def _file_org_id(ctx: Context, location: str, requested_scope: str | None) -> UUID | None:
    """Resolve the target org for a file operation — the SAME rule the Tables
    SDK uses (`resolve_target_org`): a non-superuser is pinned to their own org
    and the requested `scope` is ignored (so they can never address another
    org's tree); a superuser honors `scope` (`None` → their context org,
    `"global"` → None, a UUID → that org). `workspace` is the one unscoped
    location (shared codebase), so it always resolves to None/global.

    NOTE: for any location with an active solution context, use
    `_resolve_effective_scope` instead — it returns the install UUID as the
    storage scope, which is NOT an org UUID.

    Returns the policy/DB org key: `UUID` for an org, `None` for global.
    """
    if location == "workspace":
        return None
    return resolve_target_org(ctx.user, requested_scope, ctx.org_id)


def _storage_scope(org_id: UUID | None) -> str | None:
    """The path segment `resolve_s3_key` writes under: the org UUID for an
    org-scoped file, the literal `"global"` for a global file (so global files
    get their own `{location}/global/` tree rather than colliding at the root).
    `workspace` callers pass this through unused (that location is unscoped)."""
    return str(org_id) if org_id is not None else "global"


def _resolve_effective_scope(
    ctx: Context, location: str, requested_scope: str | None
) -> str | None:
    """Return the storage-scope string for use in `resolve_s3_key` and policy
    evaluation, with solution-context taking priority over every other signal
    (including a superuser's explicit `requested_scope`).

    - ``ctx.solution_id`` → ``str(install_id)``
      (H6: ctx.solution_id wins over requested_scope, even for superusers).
    - All other cases → ``_storage_scope(_file_org_id(ctx, location, requested_scope))``.
    """
    if ctx.solution_id is not None:
        if location == "workspace":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="workspace is not available in solution file context",
            )
        return str(ctx.solution_id)
    return _storage_scope(_file_org_id(ctx, location, requested_scope))


def _ctx_solution_id(ctx: Context, location: str) -> UUID | None:
    """Return the install UUID from context when present. Used to forward
    solution_id to policy and metadata helpers so the solution-tier policy
    cascade (Task 3) and the C2 metadata column are both correct."""
    if ctx.solution_id is None:
        return None
    try:
        return UUID(str(ctx.solution_id))
    except (ValueError, AttributeError, TypeError):
        return None


async def _install_org_id(ctx: Context, solution_id: UUID | None) -> UUID | None:
    """Look up the Solution install's ``organization_id`` from the DB.

    Used when recording file metadata for solution writes (C2): the install's
    org must be stored in ``FileMetadata.organization_id``, not ``ctx.org_id``
    which may be None for platform-admin callers.  Returns ``ctx.org_id`` as
    fallback if the install row is not found.
    """
    if solution_id is None:
        return ctx.org_id
    from src.models.orm.solutions import Solution as SolutionORM
    row = (await ctx.db.execute(
        select(SolutionORM).where(SolutionORM.id == solution_id)
    )).scalar_one_or_none()
    return row.organization_id if row is not None else ctx.org_id


def _organization_id_for_policy(location: str, scope: str | None) -> UUID | None:
    """Parse a scope string to the policy org key — `None`/`"global"` → None,
    a UUID string → that org. Parse-only (no targeting decision): used by the
    SUPERUSER-only policy-management endpoints, where an admin may legitimately
    address any org/global. App-facing file ops use `_file_org_id` instead,
    which pins non-superusers to their own org."""
    if scope is None or scope == "global":
        return None
    return UUID(scope)


async def _authorize_file_policy(
    ctx: Context,
    *,
    action: str,
    location: str,
    scope: str | None,
    path: str,
    content_type: str | None = None,
    solution_id: UUID | None = None,
    organization_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
) -> bool:
    """Evaluate file policy access. `scope` is the storage-scope string the
    caller already derived via `_resolve_effective_scope` (a UUID string,
    install-id string, or `"global"`), so a non-superuser can never reach
    another org's tree here. `solution_id` is forwarded to the policy service
    so Task 3's own-solution cascade can resolve correctly.

    For solution-context requests, `scope` is the install UUID string (not an
    org UUID), so we derive `organization_id` from the install and forward
    `solution_id` separately rather than coercing the install UUID into org."""
    from src.services.file_policy_service import FilePolicyService

    policy_organization_id: UUID | None = None
    resolved_solution_id = solution_id
    if organization_id is not _USE_CONTEXT_SOLUTION_ID:
        policy_organization_id = cast(UUID | None, organization_id)
    elif location != "workspace":
        if scope is None:
            return False
        if resolved_solution_id is not None:
            # scope == str(install_id) — look up the install's org from DB so
            # the policy check uses the install's scope (not the caller's JWT
            # org, which may be None for a platform admin making test calls).
            policy_organization_id = await _install_org_id(ctx, resolved_solution_id)
        elif scope == "global":
            policy_organization_id = None
        else:
            try:
                policy_organization_id = UUID(scope)
            except ValueError:
                return False

    policy_action = {
        "exists": "read",
        "signed_get": "read",
        "signed_put": "write",
    }.get(action, action)

    service = FilePolicyService(ctx.db)
    return await service.is_allowed(
        cast(FileAction, policy_action),
        organization_id=policy_organization_id,
        location=location,
        path=path,
        user=ctx.user,
        solution_id=resolved_solution_id,
    )


async def _require_file_policy(
    ctx: Context,
    *,
    action: str,
    location: str,
    scope: str | None,
    path: str,
    content_type: str | None = None,
    solution_id: UUID | None = None,
    organization_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
) -> None:
    allowed = await _authorize_file_policy(
        ctx,
        action=action,
        location=location,
        scope=scope,
        path=path,
        content_type=content_type,
        solution_id=solution_id,
        organization_id=organization_id,
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def _require_declared_solution_file_location(
    ctx: Context,
    *,
    solution_id: UUID | None,
    location: str,
) -> None:
    if solution_id is None:
        return

    from src.services.solution_scope import solution_declares_file_location

    if not await solution_declares_file_location(ctx.db, solution_id, location):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File location '{location}' not found",
        )


def _relative_list_path(path: str, *, location: str, scope: str | None) -> str:
    if location == "workspace":
        return path
    from shared.file_paths import resolve_s3_key

    try:
        prefix = resolve_s3_key(location, scope, "")
    except ValueError:
        return path
    return path[len(prefix):] if path.startswith(prefix) else path


def _tiers_for_backend_mode(tiers: list[_T], mode: str) -> list[_T]:
    if mode == "local":
        return tiers[:1]
    return tiers


async def _filter_listed_paths(
    ctx: Context,
    *,
    paths: list[str],
    location: str,
    scope: str | None,
    action: str = "list",
    solution_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
    organization_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
) -> list[str]:
    resolved_solution_id = (
        _ctx_solution_id(ctx, location)
        if solution_id is _USE_CONTEXT_SOLUTION_ID
        else cast(UUID | None, solution_id)
    )
    allowed_paths = []
    for listed_path in paths:
        policy_path = _relative_list_path(listed_path, location=location, scope=scope)
        if await _authorize_file_policy(
            ctx,
            action=action,
            location=location,
            scope=scope,
            path=policy_path,
            solution_id=resolved_solution_id,
            organization_id=organization_id,
        ):
            allowed_paths.append(listed_path)
    return allowed_paths


def _policy_public(row) -> FilePolicyPublic:
    return FilePolicyPublic(
        id=str(row.id),
        organization_id=str(row.organization_id) if row.organization_id else None,
        location=row.location,
        path=row.path,
        policies=FilePolicies.model_validate(row.policies),
    )


def _policy_document(raw: FilePolicies | list[dict]) -> FilePolicies:
    if isinstance(raw, FilePolicies):
        return raw
    return FilePolicies.model_validate({"policies": raw})


async def _test_principal(
    ctx: Context,
    db: AsyncSession,
    user_id: str | None,
) -> UserPrincipal:
    if not user_id:
        return ctx.user

    if not ctx.user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Testing another user requires platform admin privileges",
        )

    from src.models.orm.users import User

    target_id = UUID(user_id)
    target = (await db.execute(select(User).where(User.id == target_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {user_id}",
        )
    role_ids, role_names = await get_user_roles(target.id, db)
    return UserPrincipal(
        user_id=target.id,
        email=target.email,
        organization_id=target.organization_id,
        name=target.name or "",
        is_active=target.is_active,
        is_superuser=target.is_superuser,
        is_verified=target.is_verified,
        is_external=target.is_external,
        role_ids=role_ids,
        role_names=role_names,
    )


# =============================================================================
# File Policy Admin Endpoints
# =============================================================================


@router.get("/policies", response_model=FilePolicyListResponse)
async def list_file_policies(
    ctx: Context,
    user: CurrentSuperuser,
    location: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> FilePolicyListResponse:
    """List file policies for a location and optional org scope."""
    from src.services.file_policy_service import FilePolicyService

    target_scope = organization_id if organization_id is not None else scope
    try:
        org_id = _organization_id_for_policy(location or "workspace", target_scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    rows = await FilePolicyService(db).list_policies(
        organization_id=org_id,
        location=location,
    )
    return FilePolicyListResponse(policies=[_policy_public(row) for row in rows])


@router.post("/policies/test", response_model=FilePolicyAccessTestResponse)
async def test_file_policy_access(
    request: FilePolicyAccessTestRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FilePolicyAccessTestResponse:
    """Evaluate effective access for a path using the real file policy service."""
    from src.services.file_policy_service import FilePolicyService

    try:
        solution_id = _ctx_solution_id(ctx, request.location)
        org_id = await _install_org_id(ctx, solution_id) if solution_id is not None else _file_org_id(ctx, request.location, request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    principal = await _test_principal(ctx, db, request.user_id)
    service = FilePolicyService(db)
    matched = await service.load_policy(
        organization_id=org_id,
        solution_id=solution_id,
        location=request.location,
        path=request.path,
    )
    allowed = await service.is_allowed(
        request.action,
        organization_id=org_id,
        location=request.location,
        path=request.path,
        user=principal,
        solution_id=solution_id,
    )
    return FilePolicyAccessTestResponse(
        allowed=allowed,
        path=request.path,
        location=request.location,
        action=request.action,
        matched_policy=str(matched.id) if matched is not None else None,
        matched_rule="allowing rule" if allowed else None,
        denial_reason=None if allowed else "No matching file policy rule allowed the action",
    )


@router.post("/structure", response_model=FileStructureResponse)
async def list_file_structure(
    request: FileStructureRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FileStructureResponse:
    """Admin-only STRUCTURAL listing (not policy-gated): what physically exists
    in a scope, so the explorer tree never orphans a file. Excludes reserved
    workspace/temp; flags uploads read-only. Omit `location` to discover shares."""
    from src.services.file_structure_service import FileStructureService

    try:
        org_id = _organization_id_for_policy(request.location or "workspace", request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    svc = FileStructureService(db)
    if request.location is None:
        shares = await svc.list_shares(org_id=org_id)
        return FileStructureResponse(shares=[s.model_dump() for s in shares])
    if request.location in {"workspace", "temp"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reserved location")
    try:
        entries = await svc.list_prefix(
            org_id=org_id, location=request.location, prefix=request.prefix
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return FileStructureResponse(entries=[e.model_dump() for e in entries])


@router.get("/policies/{policy_path:path}", response_model=FilePolicyPublic)
async def get_file_policy(
    policy_path: str,
    ctx: Context,
    user: CurrentSuperuser,
    location: str = Query(default="workspace"),
    scope: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> FilePolicyPublic:
    """Get the exact file policy for a location/path prefix."""
    from src.services.file_policy_service import FilePolicyService

    try:
        org_id = _organization_id_for_policy(location, scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    row = await FilePolicyService(db).get_policy_exact(
        organization_id=org_id,
        location=location,
        path=unquote(policy_path).strip("/"),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File policy not found")
    return _policy_public(row)


@router.put("/policies/{policy_path:path}", response_model=FilePolicyPublic)
async def set_file_policy(
    policy_path: str,
    request: FilePolicySetRequest,
    ctx: Context,
    user: CurrentSuperuser,
    location: str = Query(default="workspace"),
    scope: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> FilePolicyPublic:
    """Create or replace the file policy for a location/path prefix."""
    from src.services.file_policy_service import FilePolicyService

    try:
        org_id = _organization_id_for_policy(location, scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # Validate policy refs before persisting — raises 422 for unresolvable $ref names.
    parsed_doc = _policy_document(request.policies)
    from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound, resolve_policy_refs
    from src.repositories.policy_rule import PolicyRuleRepository
    ref_repo = PolicyRuleRepository(db, org_id=org_id, is_superuser=True)
    try:
        await resolve_policy_refs(parsed_doc.model_copy(deep=True), repo=ref_repo, action_domain="file")
    except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": [{"path": "$.policies", "message": str(exc)}]},
        ) from exc
    row = await FilePolicyService(db).upsert_policy(
        organization_id=org_id,
        location=location,
        path=unquote(policy_path).strip("/"),
        policies=_policy_document(request.policies),
        created_by=user.user_id,
    )
    changed_path = row.path
    await db.commit()
    from src.core.pubsub import publish_file_policy_changed

    await publish_file_policy_changed(
        location=location,
        scope=str(org_id) if org_id is not None else None,
        path=changed_path,
    )
    return _policy_public(row)


@router.delete("/policies/{policy_path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file_policy(
    policy_path: str,
    ctx: Context,
    user: CurrentSuperuser,
    location: str = Query(default="workspace"),
    scope: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete the exact file policy for a location/path prefix."""
    from src.services.file_policy_service import FilePolicyService

    try:
        org_id = _organization_id_for_policy(location, scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    deleted = await FilePolicyService(db).delete_policy(
        organization_id=org_id,
        location=location,
        path=unquote(policy_path).strip("/"),
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File policy not found")
    await db.commit()
    from src.core.pubsub import publish_file_policy_changed

    await publish_file_policy_changed(
        location=location,
        scope=str(org_id) if org_id is not None else None,
        path=unquote(policy_path).strip("/"),
    )


async def _build_signed_url(
    request: SignedUrlRequest,
    ctx: Context,
    db: AsyncSession,
) -> SignedUrlResponse:
    """Policy-check and generate a single presigned URL."""
    from shared.file_paths import resolve_s3_key

    solution_id = _ctx_solution_id(ctx, request.location)
    if request.method == "GET":
        from src.services.solution_scope import file_read_tiers

        try:
            tiers = await file_read_tiers(db, ctx, request.location, request.scope)
            if len(tiers) == 1:
                tier = tiers[0]
                s3_path = resolve_s3_key(request.location, tier.scope, request.path)
                await _require_file_policy(
                    ctx,
                    action="signed_get",
                    location=request.location,
                    scope=tier.scope,
                    path=request.path,
                    solution_id=tier.solution_id,
                    organization_id=tier.organization_id,
                )
            else:
                backend = get_backend("cloud", db)
                allowed_path: str | None = None
                for tier in tiers:
                    s3_path = resolve_s3_key(request.location, tier.scope, request.path)
                    if not await _authorize_file_policy(
                        ctx,
                        action="signed_get",
                        location=request.location,
                        scope=tier.scope,
                        path=request.path,
                        solution_id=tier.solution_id,
                        organization_id=tier.organization_id,
                    ):
                        continue
                    allowed_path = allowed_path or s3_path
                    if await backend.exists(
                        request.path,
                        request.location,
                        scope=tier.scope,
                    ):
                        allowed_path = s3_path
                        break
                if allowed_path is None:
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
                s3_path = allowed_path
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    else:
        effective_scope = _resolve_effective_scope(ctx, request.location, request.scope)
        await _require_declared_solution_file_location(
            ctx,
            solution_id=solution_id,
            location=request.location,
        )
        try:
            s3_path = resolve_s3_key(request.location, effective_scope, request.path)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        await _require_file_policy(
            ctx,
            action="signed_put",
            location=request.location,
            scope=effective_scope,
            path=request.path,
            content_type=request.content_type,
            solution_id=solution_id,
        )

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


async def _record_completed_signed_upload(
    request: SignedUploadCompleteRequest,
    ctx: Context,
    db: AsyncSession,
) -> None:
    """Record file metadata and publish changes after a browser PUT succeeds."""
    from shared.file_paths import resolve_s3_key

    effective_scope = _resolve_effective_scope(ctx, request.location, request.scope)
    solution_id = _ctx_solution_id(ctx, request.location)
    await _require_declared_solution_file_location(
        ctx,
        solution_id=solution_id,
        location=request.location,
    )
    await _require_file_policy(
        ctx,
        action="write",
        location=request.location,
        scope=effective_scope,
        path=request.path,
        content_type=request.content_type,
        solution_id=solution_id,
    )
    try:
        s3_path = resolve_s3_key(request.location, effective_scope, request.path)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    file_storage = FileStorageService(db)
    if not await file_storage.file_exists(s3_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uploaded object not found")

    await file_storage.record_signed_upload_metadata(
        location=request.location,
        scope=effective_scope,
        path=request.path,
        s3_path=s3_path,
        content_type=request.content_type,
        size_bytes=request.size_bytes,
        sha256=request.sha256,
        updated_by=ctx.user.email,
        user_id=str(ctx.user.user_id),
        solution_id=solution_id,
        org_id=await _install_org_id(ctx, solution_id),
    )
    await db.commit()

    from src.core.pubsub import publish_file_change

    await publish_file_change(
        location=request.location,
        scope=effective_scope,
        path=request.path,
        action="upload",
    )


# =============================================================================
# Basic CRUD Endpoints (SDK-focused)
# =============================================================================


@router.post("/read", response_model=FileReadResponse)
async def read_file(
    request: FileReadRequest,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> FileReadResponse:
    """Read a file from a managed or custom location."""
    try:
        from src.services.solution_scope import file_read_tiers

        tiers = _tiers_for_backend_mode(
            await file_read_tiers(db, ctx, request.location, request.scope),
            request.mode,
        )
        backend = get_backend(request.mode, db)
        content: bytes | None = None
        had_allowed_tier = False
        for tier in tiers:
            if not await _authorize_file_policy(
                ctx,
                action="read",
                location=request.location,
                scope=tier.scope,
                path=request.path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            ):
                continue
            had_allowed_tier = True
            try:
                content = await backend.read(
                    request.path,
                    request.location,
                    scope=tier.scope,
                )
                break
            except FileNotFoundError:
                continue

        if content is None:
            if not had_allowed_tier:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
            raise FileNotFoundError(f"File not found: {request.path}")

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
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Write a file to a managed or custom location."""
    try:
        effective_scope = _resolve_effective_scope(ctx, request.location, request.scope)
        solution_id = _ctx_solution_id(ctx, request.location)
        await _require_declared_solution_file_location(
            ctx,
            solution_id=solution_id,
            location=request.location,
        )
        await _require_file_policy(
            ctx,
            action="write",
            location=request.location,
            scope=effective_scope,
            path=request.path,
            solution_id=solution_id,
        )
        backend = get_backend(request.mode, db)

        if request.binary:
            content = base64.b64decode(request.content)
        else:
            content = request.content.encode("utf-8")

        updated_by = ctx.user.email if ctx.user else "system"
        await backend.write(request.path, content, request.location, updated_by, scope=effective_scope)
        if request.mode == "cloud":
            from shared.file_paths import resolve_s3_key
            from src.services.file_storage.s3_client import S3StorageClient
            from src.core.pubsub import publish_file_change

            s3_path = resolve_s3_key(request.location, effective_scope, request.path)
            await FileStorageService(db).record_file_write_metadata(
                location=request.location,
                scope=effective_scope,
                path=request.path,
                s3_path=s3_path,
                content_type=S3StorageClient.guess_content_type(request.path),
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                updated_by=updated_by,
                user_id=str(ctx.user.user_id),
                solution_id=solution_id,
                org_id=await _install_org_id(ctx, solution_id),
            )
            await publish_file_change(
                location=request.location,
                scope=effective_scope,
                path=request.path,
                action="write",
            )

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
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a file from a managed or custom location."""
    try:
        effective_scope = _resolve_effective_scope(ctx, request.location, request.scope)
        solution_id = _ctx_solution_id(ctx, request.location)
        await _require_declared_solution_file_location(
            ctx,
            solution_id=solution_id,
            location=request.location,
        )
        await _require_file_policy(
            ctx,
            action="delete",
            location=request.location,
            scope=effective_scope,
            path=request.path,
            solution_id=solution_id,
        )
        backend = get_backend(request.mode, db)
        await backend.delete(request.path, request.location, scope=effective_scope)
        if request.mode == "cloud":
            from src.core.pubsub import publish_file_change
            from src.services.file_policy_service import FilePolicyService

            await publish_file_change(
                location=request.location,
                scope=effective_scope,
                path=request.path,
                action="delete",
            )
            await FilePolicyService(db).delete_metadata(
                organization_id=await _install_org_id(ctx, solution_id),
                location=request.location,
                path=request.path,
                solution_id=solution_id,
            )
            await db.flush()

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
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> FileListResponse:
    """List files in a directory (simple SDK-focused endpoint)."""
    try:
        from src.services.solution_scope import file_read_tiers

        tiers = _tiers_for_backend_mode(
            await file_read_tiers(db, ctx, request.location, request.scope),
            request.mode,
        )
        if not tiers:
            return FileListResponse(files=[])
        primary_tier = tiers[0]
        directory_allowed = await _authorize_file_policy(
            ctx,
            action="list",
            location=request.location,
            scope=primary_tier.scope,
            path=request.directory,
            solution_id=primary_tier.solution_id,
            organization_id=primary_tier.organization_id,
        )
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
            allowed_paths = set(
                await _filter_listed_paths(
                    ctx,
                    paths=list(s3_metadata.keys()),
                    location=request.location,
                    scope=primary_tier.scope,
                    action="list",
                    solution_id=primary_tier.solution_id,
                    organization_id=primary_tier.organization_id,
                )
            )
            s3_metadata = {
                path: meta for path, meta in s3_metadata.items()
                if path in allowed_paths
            }
            if not directory_allowed and not s3_metadata:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

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
        files: list[str] = []
        seen: set[str] = set()
        any_directory_allowed = directory_allowed
        for tier in tiers:
            tier_directory_allowed = await _authorize_file_policy(
                ctx,
                action="list",
                location=request.location,
                scope=tier.scope,
                path=request.directory,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            )
            any_directory_allowed = any_directory_allowed or tier_directory_allowed
            if not tier_directory_allowed:
                continue
            tier_files = await backend.list(
                request.directory,
                request.location,
                scope=tier.scope,
            )
            tier_files = await _filter_listed_paths(
                ctx,
                paths=sorted(tier_files),
                location=request.location,
                scope=tier.scope,
                action="list",
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            )
            for path in tier_files:
                if path in seen:
                    continue
                seen.add(path)
                files.append(path)
        if not any_directory_allowed and not files:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
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
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> FileExistsResponse:
    """Check if a file exists."""
    try:
        from src.services.solution_scope import file_read_tiers

        tiers = _tiers_for_backend_mode(
            await file_read_tiers(db, ctx, request.location, request.scope),
            request.mode,
        )
        backend = get_backend(request.mode, db)
        for tier in tiers:
            allowed = await _authorize_file_policy(
                ctx,
                action="exists",
                location=request.location,
                scope=tier.scope,
                path=request.path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
            )
            if not allowed:
                continue
            exists = await backend.exists(
                request.path,
                request.location,
                scope=tier.scope,
            )
            if exists:
                return FileExistsResponse(exists=True)
        return FileExistsResponse(exists=False)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/signed-url", response_model=SignedUrlResponse)
async def get_signed_url(
    request: SignedUrlRequest,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> SignedUrlResponse:
    """Generate a presigned S3 URL for direct file upload or download.

    Path resolution goes through `shared.file_paths.resolve_s3_key`, so the
    URL targets the same key as a `files.read`/`files.write` to the same
    `(location, scope, path)`.
    """
    return await _build_signed_url(request, ctx, db)


@router.post("/complete-upload", status_code=status.HTTP_204_NO_CONTENT)
async def complete_signed_upload(
    request: SignedUploadCompleteRequest,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Finalize a successful direct browser upload."""
    await _record_completed_signed_upload(request, ctx, db)


@router.post("/signed-urls", response_model=SignedUrlBatchResponse)
async def get_signed_urls(
    request: SignedUrlBatchRequest,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> SignedUrlBatchResponse:
    """Generate presigned URLs with per-path allow/deny results."""
    results: list[SignedUrlBatchResult] = []
    for item in request.requests:
        try:
            signed = await _build_signed_url(item, ctx, db)
            results.append(
                SignedUrlBatchResult(
                    path=item.path,
                    resolved_path=signed.path,
                    method=item.method,
                    url=signed.url,
                    expires_in=signed.expires_in,
                    status_code=200,
                )
            )
        except HTTPException as exc:
            error = "forbidden" if exc.status_code == status.HTTP_403_FORBIDDEN else str(exc.detail)
            results.append(
                SignedUrlBatchResult(
                    path=item.path,
                    method=item.method,
                    error=error,
                    status_code=exc.status_code,
                )
            )
    return SignedUrlBatchResponse(results=results)


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
            # SeaweedFS can briefly retain an empty CommonPrefix after deleting
            # every object under it. Treat the non-delimited object list as the
            # source of truth before showing a folder in the editor.
            if not await repo.list(folder_path):
                continue

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
            # Folder delete: drain the prefix. Some S3-compatible stores can
            # report folder markers briefly after child deletion.
            for attempt in range(5):
                for child_path in sorted(set(children)):
                    if child_path.endswith("/"):
                        await repo.delete(child_path)
                    else:
                        await storage.delete_file(child_path)
                await repo.delete(path.rstrip("/"))
                await repo.delete(folder_prefix)

                children = await repo.list(folder_prefix)
                if not children:
                    break
                if attempt < 4:
                    await asyncio.sleep(0.1)
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
