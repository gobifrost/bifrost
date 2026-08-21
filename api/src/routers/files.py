"""
Unified Files Router

File operations with two storage modes:
- local: Local filesystem (CWD, /tmp/bifrost/temp, /tmp/bifrost/uploads)
- cloud: S3 storage (default)

Auth: Role/boundary authorization for source workspace operations; file
policies for managed runtime locations.
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context, CurrentActiveUser
from src.core.org_filter import resolve_target_org
from src.core.principal import UserPrincipal
from src.core.log_safety import log_safe
from src.models.contracts.files import (
    FilePullRequest,
    FilePullResponse,
    WatchSessionRequest,
    WorkspaceFilePatchRequest,
    WorkspaceFilePatchResponse,
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
    FileStatResponse,
    PendingDeactivation,
    SearchRequest,
    SearchResponse,
    WorkflowIdConflict,
)
from src.services.audit import emit_audit
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    AuthorizationContext,
    CurrentAuthorizationContext,
    get_authorization_context,
    policy_principal_for_authorization,
    resolve_authorization_context,
)
from src.services.editor.search import search_files_db
from src.services.file_backend import get_backend
from src.services.file_storage import FileStorageService
from src.services.operation_catalog import operation_route
from src.services.solutions.guard import assert_workspace_path_not_solution_managed

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
    expected_version: str | None = Field(
        default=None,
        description="Write only when the current content has this version",
    )
    create_only: bool = Field(
        default=False,
        description="Create the file only when the path does not already exist",
    )


class FileDeleteRequest(BaseModel):
    """Request to delete a file."""
    path: str = Field(..., description="File path relative to location root")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope. Required for non-workspace, non-uploads locations.")
    mode: Mode = Field(default="cloud", description="Storage mode: local or cloud")
    expected_version: str | None = Field(
        default=None,
        description="Delete only when the current content has this version",
    )


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
    # Non-null for deploy-owned solution-tier rows (read-only via CRUD).
    solution_id: str | None = None


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
    cascade (Task 3) and the C2 metadata column are both correct. Canonical
    parse lives in services/solution_scope.py."""
    from src.services.solution_scope import parse_ctx_solution_id

    return parse_ctx_solution_id(ctx)


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


def _parse_solution_param(solution: str | None) -> UUID | None:
    """Parse the admin ``solution`` query param (an install UUID) or None.

    Used by the SUPERUSER-only policy-management endpoints to address an
    install's deploy-owned solution tier explicitly (reads only — writes are
    refused as deploy-owned)."""
    if solution is None or solution == "":
        return None
    try:
        return UUID(solution)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid solution id: {solution!r}",
        ) from exc


async def _policy_solution_organization_id(
    ctx: Context,
    solution_id: UUID,
) -> UUID | None:
    from src.models.orm.solutions import Solution as SolutionORM

    solution = await ctx.db.scalar(
        select(SolutionORM).where(SolutionORM.id == solution_id)
    )
    if solution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Solution not found",
        )
    return solution.organization_id


async def _require_policy_read_boundary(
    ctx: Context,
    authorization: AuthorizationContext,
    organization_id: UUID | None,
) -> None:
    if (
        authorization.selected_boundary.kind
        is not AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS
    ):
        authorization.require_resource_boundary(organization_id)
        return
    if organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one managed organization to read this file policy",
        )
    from src.models.orm.organizations import Organization

    is_provider = await ctx.db.scalar(
        select(Organization.is_provider).where(
            Organization.id == organization_id
        )
    )
    if is_provider is not False:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File policy not found",
        )


def _require_policy_write_boundary(
    authorization: AuthorizationContext,
    organization_id: UUID | None,
) -> None:
    if (
        authorization.selected_boundary.kind
        is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization before changing a file policy",
        )
    authorization.require_resource_boundary(organization_id)


_SOLUTION_POLICY_READONLY = (
    "Solution-tier file policies are managed by deployment methods and cannot be "
    "edited directly."
)


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
    workspace_authorization: AuthorizationContext | None = None,
) -> bool:
    """Evaluate file policy access. `scope` is the storage-scope string the
    caller already derived via `_resolve_effective_scope` (a UUID string,
    install-id string, or `"global"`), so a non-superuser can never reach
    another org's tree here. `solution_id` is forwarded to the policy service
    so Task 3's own-solution cascade can resolve correctly.

    For solution-context requests, `scope` is the install UUID string (not an
    org UUID), so we derive `organization_id` from the install and forward
    `solution_id` separately rather than coercing the install UUID into org.

    `workspace` is the shared platform codebase. It is governed by the
    repository capability at the Platform boundary rather than file policies.
    The caller supplies the already-resolved authorization context so this
    helper never falls back to legacy superuser state."""
    from src.services.file_policy_service import FilePolicyService

    if location == "workspace":
        return workspace_authorization is not None

    # Past this point location is never "workspace" (handled above).
    policy_organization_id: UUID | None = None
    resolved_solution_id = solution_id
    if organization_id is not _USE_CONTEXT_SOLUTION_ID:
        policy_organization_id = cast(UUID | None, organization_id)
    else:
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


async def _deny_file_policy(
    ctx: Context,
    *,
    action: str,
    location: str,
    path: str,
    scope: str | None = None,
    solution_id: UUID | None = None,
) -> None:
    """Record a `policy.deny` audit row and raise 403. This is the single
    choke point for every *final* file-policy denial (read or write) — call
    it exactly once per request, at the point where the request is actually
    being rejected, not at each per-tier/per-path `_authorize_file_policy`
    probe along the way (those are non-final "is this tier usable" checks
    and would over-count if audited individually).

    Mirrors the tables.py pattern: emit then commit, because letting the
    HTTPException propagate rolls back the request-scoped session and loses
    the audit row.
    """
    await emit_audit(
        ctx.db,
        "policy.deny",
        resource_type="file",
        outcome="failure",
        details={
            "policy_action": action,
            "location": location,
            "path": path,
        },
    )
    await ctx.db.commit()
    # A policy denial must identify its scope inputs (no user/token data —
    # every field is caller-supplied or derived from it): a scope-loss bug
    # reads as solution_id=null instead of a bare "Forbidden".
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "message": "File policy denied",
            "action": action,
            "location": location,
            "path": path,
            "scope": scope,
            "solution_id": str(solution_id) if solution_id else None,
        },
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
    workspace_authorization: AuthorizationContext | None = None,
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
        workspace_authorization=workspace_authorization,
    )
    if not allowed:
        await _deny_file_policy(
            ctx,
            action=action,
            location=location,
            path=path,
            scope=scope,
            solution_id=solution_id,
        )


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
    workspace_authorization: AuthorizationContext | None = None,
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
            workspace_authorization=workspace_authorization,
        ):
            allowed_paths.append(listed_path)
    return allowed_paths


async def _authorize_workspace_operation(
    http_request: Request,
    *,
    user: UserPrincipal,
    db: AsyncSession,
    location: str,
    operation_id: str,
) -> AuthorizationContext | None:
    """Authorize a global source-workspace operation through Roles.

    Managed-file locations keep their existing file-policy contract. The
    shared `_repo` workspace is a Platform resource, so it requires both the
    catalogued repository capability and the explicit Platform boundary.
    """
    if location != "workspace":
        return None
    authorization = await get_authorization_context(http_request, user, db)
    authorization.require_operation(operation_id)
    authorization.require_resource_boundary(None)
    return authorization


def _require_platform_workspace_operation(
    authorization: AuthorizationContext,
    operation_id: str,
) -> None:
    """Require the catalogued workspace operation at the Platform boundary."""

    authorization.require_operation(operation_id)
    authorization.require_resource_boundary(None)


def _policy_public(row) -> FilePolicyPublic:
    return FilePolicyPublic(
        id=str(row.id),
        organization_id=str(row.organization_id) if row.organization_id else None,
        location=row.location,
        path=row.path,
        policies=FilePolicies.model_validate(row.policies),
        solution_id=str(row.solution_id) if getattr(row, "solution_id", None) else None,
    )


def _policy_document(raw: FilePolicies | list[dict]) -> FilePolicies:
    if isinstance(raw, FilePolicies):
        return raw
    return FilePolicies.model_validate({"policies": raw})


async def _test_principal(
    ctx: Context,
    db: AsyncSession,
    user_id: str | None,
    authorization: AuthorizationContext,
) -> UserPrincipal:
    if not user_id or user_id == str(ctx.user.user_id):
        return policy_principal_for_authorization(ctx.user, authorization)

    from src.models.orm.users import User

    try:
        target_id = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id must be a UUID",
        ) from exc
    target = (await db.execute(select(User).where(User.id == target_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {user_id}",
        )
    if not authorization.has_delegated_capability("filepolicies.read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Testing another user requires delegated file-policy access",
        )
    await _require_policy_read_boundary(
        ctx,
        authorization,
        target.organization_id,
    )
    principal = UserPrincipal(
        user_id=target.id,
        email=target.email,
        organization_id=target.organization_id,
        name=target.name or "",
        is_active=target.is_active,
        is_superuser=target.is_superuser,
        is_verified=target.is_verified,
        is_external=target.is_external,
    )
    target_authorization = await resolve_authorization_context(
        db,
        requester=principal,
        selected_boundary=authorization.selected_boundary,
    )
    return policy_principal_for_authorization(principal, target_authorization)


# =============================================================================
# File Policy Admin Endpoints
# =============================================================================


@router.get(
    "/policies",
    response_model=FilePolicyListResponse,
    **operation_route("files.policies.list"),
)
async def list_file_policies(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    location: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    solution: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> FilePolicyListResponse:
    """List file policies for a location and optional org/solution scope.

    ``solution`` (an install UUID) lists that install's deploy-owned solution
    tier — admins may SEE these; edits stay blocked (deploy-owned)."""
    from src.services.file_policy_service import FilePolicyService

    authorization.require_operation("files.policies.list")
    solution_id = _parse_solution_param(solution)
    if solution_id is not None:
        solution_org_id = await _policy_solution_organization_id(
            ctx, solution_id
        )
        await _require_policy_read_boundary(
            ctx, authorization, solution_org_id
        )
        rows = await FilePolicyService(db).list_policies(
            organization_id=None,
            location=location,
            solution_id=solution_id,
        )
        return FilePolicyListResponse(policies=[_policy_public(row) for row in rows])

    target_scope = organization_id if organization_id is not None else scope
    if (
        authorization.selected_boundary.kind
        is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS
        and target_scope is None
    ):
        rows = await FilePolicyService(db).list_managed_organization_policies(
            location=location
        )
        return FilePolicyListResponse(
            policies=[_policy_public(row) for row in rows]
        )
    try:
        org_id = _organization_id_for_policy(location or "workspace", target_scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _require_policy_read_boundary(ctx, authorization, org_id)
    rows = await FilePolicyService(db).list_policies(
        organization_id=org_id,
        location=location,
    )
    return FilePolicyListResponse(policies=[_policy_public(row) for row in rows])


@router.post(
    "/policies/test",
    response_model=FilePolicyAccessTestResponse,
    **operation_route("files.policies.test"),
)
async def test_file_policy_access(
    request: FilePolicyAccessTestRequest,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    db: AsyncSession = Depends(get_db),
) -> FilePolicyAccessTestResponse:
    """Evaluate effective access for a path using the real file policy service."""
    from src.services.file_policy_service import FilePolicyService

    authorization.require_operation("files.policies.test")
    try:
        solution_id = _ctx_solution_id(ctx, request.location)
        org_id = await _install_org_id(ctx, solution_id) if solution_id is not None else _file_org_id(ctx, request.location, request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _require_policy_read_boundary(ctx, authorization, org_id)
    principal = await _test_principal(
        ctx,
        db,
        request.user_id,
        authorization,
    )

    # Workspace access is capability/boundary-governed rather than file-policy
    # governed. Resolve the tested person's Platform grants so this diagnostic
    # reports the same decision as the canonical source endpoints.
    if request.location == "workspace":
        tested_authorization = await resolve_authorization_context(
            db,
            requester=principal,
            selected_boundary=AuthorizationBoundary.platform(),
        )
        required_capability = (
            "repository.read"
            if request.action in {"read", "list"}
            else "repository.readwrite"
        )
        allowed = tested_authorization.has_capability(required_capability)
        return FilePolicyAccessTestResponse(
            allowed=allowed,
            path=request.path,
            location=request.location,
            action=request.action,
            matched_policy=None,
            matched_rule=(
                f"{required_capability} at Platform boundary" if allowed else None
            ),
            denial_reason=(
                None
                if allowed
                else f"Missing {required_capability} at Platform boundary"
            ),
        )

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


@router.post(
    "/structure",
    response_model=FileStructureResponse,
    **operation_route("files.structure.list"),
)
async def list_file_structure(
    request: FileStructureRequest,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    db: AsyncSession = Depends(get_db),
) -> FileStructureResponse:
    """Admin-only STRUCTURAL listing (not policy-gated): what physically exists
    in a scope, so the explorer tree never orphans a file. Excludes reserved
    workspace/temp; flags uploads read-only. Omit `location` to discover shares."""
    from src.services.file_structure_service import FileStructureService

    authorization.require_operation("files.structure.list")
    try:
        org_id = _organization_id_for_policy(request.location or "workspace", request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _require_policy_read_boundary(ctx, authorization, org_id)

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


@router.get(
    "/policies/{policy_path:path}",
    response_model=FilePolicyPublic,
    **operation_route("files.policies.get"),
)
async def get_file_policy(
    policy_path: str,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    location: str = Query(default="workspace"),
    scope: str | None = Query(default=None),
    solution: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> FilePolicyPublic:
    """Get the exact file policy for a location/path prefix.

    ``solution`` reads the install's deploy-owned solution tier (read-only)."""
    from src.services.file_policy_service import FilePolicyService

    authorization.require_operation("files.policies.get")
    solution_id = _parse_solution_param(solution)
    if solution_id is not None:
        solution_org_id = await _policy_solution_organization_id(
            ctx, solution_id
        )
        await _require_policy_read_boundary(
            ctx, authorization, solution_org_id
        )
        row = await FilePolicyService(db).get_solution_policy_exact(
            solution_id=solution_id,
            location=location,
            path=unquote(policy_path).strip("/"),
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File policy not found")
        return _policy_public(row)

    try:
        org_id = _organization_id_for_policy(location, scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _require_policy_read_boundary(ctx, authorization, org_id)
    row = await FilePolicyService(db).get_policy_exact(
        organization_id=org_id,
        location=location,
        path=unquote(policy_path).strip("/"),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File policy not found")
    return _policy_public(row)


@router.put(
    "/policies/{policy_path:path}",
    response_model=FilePolicyPublic,
    **operation_route("files.policies.set"),
)
async def set_file_policy(
    policy_path: str,
    request: FilePolicySetRequest,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    location: str = Query(default="workspace"),
    scope: str | None = Query(default=None),
    solution: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> FilePolicyPublic:
    """Create or replace the file policy for a location/path prefix.

    Solution-tier rows (``solution`` set) are deploy-owned and refused (409)."""
    from src.services.file_policy_service import FilePolicyService

    authorization.require_operation("files.policies.set")
    if _parse_solution_param(solution) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_SOLUTION_POLICY_READONLY,
        )

    try:
        org_id = _organization_id_for_policy(location, scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _require_policy_write_boundary(authorization, org_id)
    # Validate policy refs before persisting — raises 422 for unresolvable $ref names.
    parsed_doc = _policy_document(request.policies)
    from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound, resolve_policy_refs
    from src.repositories.policy_rule import PolicyRuleRepository
    ref_repo = PolicyRuleRepository(db, org_id=org_id, bypass_resource_admission=True)
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
        created_by=authorization.requester.user_id,
    )
    changed_path = row.path
    await emit_audit(
        db,
        "file_policy.set",
        resource_type="file_policy",
        resource_id=row.id,
        details={
            "organization_id": str(org_id) if org_id else None,
            "location": location,
            "path": changed_path,
        },
    )
    await db.commit()
    from src.core.pubsub import publish_file_policy_changed

    await publish_file_policy_changed(
        location=location,
        scope=str(org_id) if org_id is not None else None,
        path=changed_path,
    )
    return _policy_public(row)


@router.delete(
    "/policies/{policy_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    **operation_route("files.policies.delete"),
)
async def delete_file_policy(
    policy_path: str,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    location: str = Query(default="workspace"),
    scope: str | None = Query(default=None),
    solution: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete the exact file policy for a location/path prefix.

    Solution-tier rows (``solution`` set) are deploy-owned and refused (409)."""
    from src.services.file_policy_service import FilePolicyService

    authorization.require_operation("files.policies.delete")
    if _parse_solution_param(solution) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_SOLUTION_POLICY_READONLY,
        )

    try:
        org_id = _organization_id_for_policy(location, scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _require_policy_write_boundary(authorization, org_id)
    service = FilePolicyService(db)
    existing = await service.get_policy_exact(
        organization_id=org_id,
        location=location,
        path=unquote(policy_path).strip("/"),
    )
    deleted = await service.delete_policy(
        organization_id=org_id,
        location=location,
        path=unquote(policy_path).strip("/"),
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File policy not found")
    await emit_audit(
        db,
        "file_policy.delete",
        resource_type="file_policy",
        resource_id=existing.id if existing else None,
        details={
            "organization_id": str(org_id) if org_id else None,
            "location": location,
            "path": unquote(policy_path).strip("/"),
        },
    )
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
            if request.location != "workspace":
                await _require_declared_solution_file_location(
                    ctx,
                    solution_id=solution_id,
                    location=request.location,
                )
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
                    await _deny_file_policy(
                        ctx,
                        action="signed_get",
                        location=request.location,
                        path=request.path,
                        scope=request.scope,
                        solution_id=solution_id,
                    )
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


@router.post(
    "/read",
    response_model=FileReadResponse,
    **operation_route("workspace.files.read"),
)
async def read_file(
    request: FileReadRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> FileReadResponse:
    """Read a file from a managed or custom location."""
    try:
        from src.services.solution_scope import file_read_tiers

        workspace_authorization = await _authorize_workspace_operation(
            http_request,
            user=user,
            db=db,
            location=request.location,
            operation_id="workspace.files.read",
        )

        if request.location != "workspace":
            await _require_declared_solution_file_location(
                ctx,
                solution_id=_ctx_solution_id(ctx, request.location),
                location=request.location,
            )
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
                action="exists",
                location=request.location,
                scope=tier.scope,
                path=request.path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
                workspace_authorization=workspace_authorization,
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
                await _deny_file_policy(
                    ctx,
                    action="read",
                    location=request.location,
                    path=request.path,
                    scope=request.scope,
                    solution_id=_ctx_solution_id(ctx, request.location),
                )
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


@router.post(
    "/write",
    status_code=status.HTTP_204_NO_CONTENT,
    **operation_route("workspace.files.write"),
)
async def write_file(
    request: FileWriteRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Write a file to a managed or custom location."""
    try:
        workspace_authorization = await _authorize_workspace_operation(
            http_request,
            user=user,
            db=db,
            location=request.location,
            operation_id="workspace.files.write",
        )
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
            workspace_authorization=workspace_authorization,
        )
        if request.mode == "cloud" and request.location == "workspace":
            await assert_workspace_path_not_solution_managed(db, request.path)
        backend = get_backend(request.mode, db)

        if request.create_only and request.expected_version is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="create_only and expected_version cannot be combined",
            )
        await _lock_file_mutation(
            db,
            location=request.location,
            scope=effective_scope,
            path=request.path,
        )

        current_stat = None
        if request.create_only or request.expected_version is not None:
            current_stat = await _get_file_stat(
                db,
                request.path,
                request.location,
                effective_scope,
                request.mode,
            )
            if request.create_only and current_stat.exists:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "reason": "file_exists",
                        "path": request.path,
                        "message": "File already exists; read it before replacing it.",
                        "current_version": current_stat.version,
                        "current_last_modified": current_stat.last_modified,
                        "current_updated_by": current_stat.updated_by,
                    },
                )
            if request.expected_version is not None:
                if not current_stat.exists:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "reason": "file_missing",
                            "path": request.path,
                            "expected_version": request.expected_version,
                            "message": "File no longer exists.",
                        },
                    )
                if current_stat.version != request.expected_version:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "reason": "version_conflict",
                            "path": request.path,
                            "expected_version": request.expected_version,
                            "current_version": current_stat.version,
                            "message": "File changed after it was read.",
                            "current_last_modified": current_stat.last_modified,
                            "current_updated_by": current_stat.updated_by,
                        },
                    )

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
            # The yielded DB dependency commits after the response body is sent.
            # Commit here so a successful write response and its notification
            # never race ahead of durable metadata.
            await db.commit()
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


@router.post(
    "/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    **operation_route("workspace.files.delete"),
)
async def delete_file(
    request: FileDeleteRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a file from a managed or custom location."""
    try:
        workspace_authorization = await _authorize_workspace_operation(
            http_request,
            user=user,
            db=db,
            location=request.location,
            operation_id="workspace.files.delete",
        )
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
            workspace_authorization=workspace_authorization,
        )
        if request.mode == "cloud" and request.location == "workspace":
            await assert_workspace_path_not_solution_managed(db, request.path)
        backend = get_backend(request.mode, db)

        await _lock_file_mutation(
            db,
            location=request.location,
            scope=effective_scope,
            path=request.path,
        )

        if request.expected_version is not None:
            current_stat = await _get_file_stat(
                db,
                request.path,
                request.location,
                effective_scope,
                request.mode,
            )
            if not current_stat.exists:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "reason": "file_missing",
                        "path": request.path,
                        "expected_version": request.expected_version,
                        "message": "File no longer exists.",
                    },
                )
            if current_stat.version != request.expected_version:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "reason": "version_conflict",
                        "path": request.path,
                        "expected_version": request.expected_version,
                        "current_version": current_stat.version,
                        "message": "File changed after it was read.",
                        "current_last_modified": current_stat.last_modified,
                        "current_updated_by": current_stat.updated_by,
                    },
                )
        await backend.delete(request.path, request.location, scope=effective_scope)
        if request.mode == "cloud":
            from src.core.pubsub import publish_file_change
            from src.services.file_policy_service import FilePolicyService

            await FilePolicyService(db).delete_metadata(
                organization_id=await _install_org_id(ctx, solution_id),
                location=request.location,
                path=request.path,
                solution_id=solution_id,
            )
            # Do not acknowledge or publish the deletion while its metadata is
            # still visible to another transaction.
            await db.commit()
            await publish_file_change(
                location=request.location,
                scope=effective_scope,
                path=request.path,
                action="delete",
            )

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


def _content_version(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


async def _lock_file_mutation(
    db: AsyncSession,
    *,
    location: str,
    scope: str | None,
    path: str,
) -> None:
    """Serialize competing API mutations for one logical file path."""
    lock_key = f"{location}:{scope or ''}:{path}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )


async def _get_file_stat(
    db: AsyncSession,
    path: str,
    location: str,
    scope: str | None,
    mode: str,
) -> FileStatResponse:
    """Load file metadata for conflict detection and stat output."""
    try:
        backend = get_backend(mode, db)
        content = await backend.read(path, location, scope=scope)
    except FileNotFoundError:
        return FileStatResponse(path=path, exists=False)

    meta = None
    if location == "workspace" and mode == "cloud":
        from src.models.orm.file_index import FileIndex

        row = await db.execute(
            select(FileIndex.updated_at, FileIndex.updated_by).where(FileIndex.path == path)
        )
        meta = row.first()
    return FileStatResponse(
        path=path,
        exists=True,
        version=_content_version(content),
        size=len(content),
        last_modified=meta.updated_at.isoformat() if meta and meta.updated_at else None,
        updated_by=meta.updated_by if meta else None,
    )


@router.post(
    "/list",
    response_model=FileListResponse,
    **operation_route("workspace.files.list"),
)
async def list_files_simple(
    request: FileListRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> FileListResponse:
    """List files in a directory (simple SDK-focused endpoint)."""
    try:
        from src.services.solution_scope import file_read_tiers

        workspace_authorization = await _authorize_workspace_operation(
            http_request,
            user=user,
            db=db,
            location=request.location,
            operation_id="workspace.files.list",
        )

        if request.location != "workspace":
            await _require_declared_solution_file_location(
                ctx,
                solution_id=_ctx_solution_id(ctx, request.location),
                location=request.location,
            )
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
            workspace_authorization=workspace_authorization,
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
                    workspace_authorization=workspace_authorization,
                )
            )
            s3_metadata = {
                path: meta for path, meta in s3_metadata.items()
                if path in allowed_paths
            }
            if not directory_allowed and not s3_metadata:
                await _deny_file_policy(
                    ctx,
                    action="list",
                    location=request.location,
                    path=request.directory,
                    scope=request.scope,
                    solution_id=primary_tier.solution_id,
                )

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
        for index, tier in enumerate(tiers):
            tier_directory_allowed = await _authorize_file_policy(
                ctx,
                action="list",
                location=request.location,
                scope=tier.scope,
                path=request.directory,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
                workspace_authorization=workspace_authorization,
            )
            any_directory_allowed = any_directory_allowed or tier_directory_allowed
            # The primary tier (index 0 — the caller's own scope) is always
            # enumerated and filtered per-file, so a per-file policy (e.g. a
            # creator-scoped list) can surface individual paths even when the
            # directory isn't broadly listable. Fallback tiers (solution org/
            # global cascade) are gated by their directory-level list policy:
            # if the directory is denied for that tier, the whole tier is
            # hidden rather than leaking its files through per-file grants.
            if index > 0 and not tier_directory_allowed:
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
                workspace_authorization=workspace_authorization,
            )
            for path in tier_files:
                if path in seen:
                    continue
                seen.add(path)
                files.append(path)
        if not any_directory_allowed and not files:
            await _deny_file_policy(
                ctx,
                action="list",
                location=request.location,
                path=request.directory,
                scope=request.scope,
                solution_id=primary_tier.solution_id,
            )
        return FileListResponse(files=files)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/exists",
    response_model=FileExistsResponse,
    **operation_route("workspace.files.exists"),
)
async def file_exists(
    request: FileExistsRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> FileExistsResponse:
    """Check if a file exists."""
    try:
        from src.services.solution_scope import file_read_tiers

        workspace_authorization = await _authorize_workspace_operation(
            http_request,
            user=user,
            db=db,
            location=request.location,
            operation_id="workspace.files.exists",
        )

        if request.location != "workspace":
            await _require_declared_solution_file_location(
                ctx,
                solution_id=_ctx_solution_id(ctx, request.location),
                location=request.location,
            )
        tiers = _tiers_for_backend_mode(
            await file_read_tiers(db, ctx, request.location, request.scope),
            request.mode,
        )
        backend = get_backend(request.mode, db)
        for tier in tiers:
            allowed = await _authorize_file_policy(
                ctx,
                action="read",
                location=request.location,
                scope=tier.scope,
                path=request.path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
                workspace_authorization=workspace_authorization,
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


@router.post(
    "/stat",
    response_model=FileStatResponse,
    **operation_route("workspace.files.stat"),
)
async def file_stat(
    request: FileReadRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> FileStatResponse:
    """Return file metadata for guarded CLI workflows."""
    try:
        from src.services.solution_scope import file_read_tiers

        workspace_authorization = await _authorize_workspace_operation(
            http_request,
            user=user,
            db=db,
            location=request.location,
            operation_id="workspace.files.stat",
        )

        if request.location != "workspace":
            await _require_declared_solution_file_location(
                ctx,
                solution_id=_ctx_solution_id(ctx, request.location),
                location=request.location,
            )
        tiers = _tiers_for_backend_mode(
            await file_read_tiers(db, ctx, request.location, request.scope),
            request.mode,
        )
        for tier in tiers:
            allowed = await _authorize_file_policy(
                ctx,
                action="read",
                location=request.location,
                scope=tier.scope,
                path=request.path,
                solution_id=tier.solution_id,
                organization_id=tier.organization_id,
                workspace_authorization=workspace_authorization,
            )
            if not allowed:
                continue
            stat = await _get_file_stat(
                db,
                request.path,
                request.location,
                tier.scope,
                request.mode,
            )
            if stat.exists:
                return stat
        return FileStatResponse(path=request.path, exists=False)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/patch",
    response_model=WorkspaceFilePatchResponse,
    responses={409: {"model": FileConflictResponse, "description": "File conflict"}},
    **operation_route("workspace.files.patch"),
)
async def patch_workspace_file(
    request: WorkspaceFilePatchRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> WorkspaceFilePatchResponse:
    """Replace one unique text fragment in the global source workspace."""
    await _authorize_workspace_operation(
        http_request,
        user=user,
        db=db,
        location="workspace",
        operation_id="workspace.files.patch",
    )
    await assert_workspace_path_not_solution_managed(db, request.path)
    await _lock_file_mutation(
        db,
        location="workspace",
        scope=None,
        path=request.path,
    )

    current_stat = await _get_file_stat(
        db,
        request.path,
        "workspace",
        None,
        "cloud",
    )
    if not current_stat.exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.path}",
        )
    if (
        request.expected_version is not None
        and current_stat.version != request.expected_version
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "version_conflict",
                "message": "File changed after it was read.",
                "path": request.path,
                "expected_version": request.expected_version,
                "current_version": current_stat.version,
                "current_last_modified": current_stat.last_modified,
                "current_updated_by": current_stat.updated_by,
            },
        )

    storage = FileStorageService(db)
    raw_content, _ = await storage.read_file(request.path)
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is binary and cannot be patched as text.",
        ) from exc

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    old_string = request.old_string.replace("\r\n", "\n").replace("\r", "\n")
    new_string = request.new_string.replace("\r\n", "\n").replace("\r", "\n")
    match_count = content.count(old_string)
    if match_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "string_not_found",
                "message": "old_string was not found in the file.",
                "path": request.path,
            },
        )
    if match_count > 1:
        locations = [
            {"line": index + 1, "preview": line.strip()[:80]}
            for index, line in enumerate(content.split("\n"))
            if old_string in line
        ]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "string_not_unique",
                "message": (
                    f"old_string matched {match_count} locations; include more "
                    "context to make it unique."
                ),
                "path": request.path,
                "match_locations": locations,
            },
        )

    patched = content.replace(old_string, new_string, 1).encode("utf-8")
    write_result = await storage.write_file(
        request.path,
        patched,
        user.email or "system",
        force_deactivation=request.force_deactivation,
        replacements=request.replacements,
        workflows_to_deactivate=request.workflows_to_deactivate,
    )
    if write_result.pending_deactivations:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "workflows_would_deactivate",
                "message": (
                    f"{len(write_result.pending_deactivations)} workflow(s) "
                    "would be deactivated"
                ),
                "pending_deactivations": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "function_name": item.function_name,
                        "path": item.path,
                        "description": item.description,
                        "decorator_type": item.decorator_type,
                        "has_executions": item.has_executions,
                        "last_execution_at": item.last_execution_at,
                        "endpoint_enabled": item.endpoint_enabled,
                        "affected_entities": item.affected_entities,
                    }
                    for item in write_result.pending_deactivations
                ],
                "available_replacements": [
                    {
                        "function_name": item.function_name,
                        "name": item.name,
                        "decorator_type": item.decorator_type,
                        "similarity_score": item.similarity_score,
                    }
                    for item in (write_result.available_replacements or [])
                ],
            },
        )

    await db.commit()
    final_content = write_result.final_content
    return WorkspaceFilePatchResponse(
        path=request.path,
        version=_content_version(final_content),
        lines_changed=max(old_string.count("\n") + 1, new_string.count("\n") + 1),
        content_modified=write_result.content_modified,
        needs_indexing=write_result.needs_indexing,
        diagnostics=[
            {
                "severity": item.severity,
                "message": item.message,
                "line": item.line,
                "column": item.column,
                "source": item.source,
            }
            for item in (write_result.diagnostics or [])
        ],
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


@router.post(
    "/pull",
    response_model=FilePullResponse,
    **operation_route("workspace.files.pull"),
)
async def pull_files(
    request: FilePullRequest,
    authorization: CurrentAuthorizationContext,
    db: AsyncSession = Depends(get_db),
) -> FilePullResponse:
    """
    Pull manifest files from server that differ from local state.

    Only returns regenerated .bifrost/*.yaml from DB state.
    Code file reconciliation is handled by git, not by this endpoint.
    """
    _require_platform_workspace_operation(authorization, "workspace.files.pull")
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


@router.get(
    "/manifest",
    **operation_route("workspace.files.manifest"),
)
async def get_manifest(
    authorization: CurrentAuthorizationContext,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Return regenerated manifest files from DB state."""
    _require_platform_workspace_operation(authorization, "workspace.files.manifest")
    from src.services.manifest_generator import generate_manifest
    from bifrost.manifest import serialize_manifest_dir

    manifest = await generate_manifest(db)
    return serialize_manifest_dir(manifest)


# =============================================================================
# Watch Session Endpoints (CLI watch mode)
# =============================================================================


@router.post(
    "/watch",
    **operation_route("workspace.files.watch"),
)
async def manage_watch_session(
    request: WatchSessionRequest,
    authorization: CurrentAuthorizationContext,
) -> dict:
    """Register, heartbeat, or deregister a CLI watch session."""
    _require_platform_workspace_operation(authorization, "workspace.files.watch")
    from src.core.cache.redis_client import get_shared_redis
    from src.core.pubsub import publish_file_activity

    requester = authorization.requester
    session_id = request.session_id or "unknown"
    key = f"bifrost:watch:{requester.user_id}:{request.prefix}"
    r = await get_shared_redis()

    if request.action in ("start", "heartbeat"):
        await r.setex(key, WATCH_SESSION_TTL_SECONDS, json.dumps({
            "user_id": str(requester.user_id),
            "user_name": requester.name or requester.email or "CLI",
            "prefix": request.prefix,
            "session_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }))
        if request.action == "start":
            await publish_file_activity(
                user_id=str(requester.user_id),
                user_name=requester.name or requester.email or "CLI",
                activity_type="watch_start",
                prefix=request.prefix,
                session_id=session_id,
            )
    elif request.action == "stop":
        await r.delete(key)
        await publish_file_activity(
            user_id=str(requester.user_id),
            user_name=requester.name or requester.email or "CLI",
            activity_type="watch_stop",
            prefix=request.prefix,
            session_id=session_id,
        )
    return {"ok": True}


@router.get(
    "/watchers",
    **operation_route("workspace.files.watchers"),
)
async def list_active_watchers(
    authorization: CurrentAuthorizationContext,
) -> dict:
    """List active CLI watch sessions."""
    _require_platform_workspace_operation(authorization, "workspace.files.watchers")
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
    **operation_route("workspace.files.editor.list"),
)
async def list_files_editor(
    authorization: CurrentAuthorizationContext,
    path: str = Query(..., description="Directory path relative to workspace root"),
    recursive: bool = Query(default=False, description="If true, return all files recursively"),
    db: AsyncSession = Depends(get_db),
) -> list[FileMetadata]:
    """
    List files and folders in a directory with rich metadata.

    Cloud mode only - used by browser editor.
    Lists directly from S3 via RepoStorage (source of truth).
    """
    _require_platform_workspace_operation(authorization, "workspace.files.editor.list")
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
    **operation_route("workspace.files.editor.read"),
)
async def get_file_content_editor(
    authorization: CurrentAuthorizationContext,
    path: str = Query(..., description="File path relative to workspace root"),
    db: AsyncSession = Depends(get_db),
) -> FileContentResponse:
    """
    Read file content with rich metadata.

    Cloud mode only - used by browser editor.
    """
    _require_platform_workspace_operation(authorization, "workspace.files.editor.read")
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
    **operation_route("workspace.files.editor.write"),
)
async def put_file_content_editor(
    request: FileContentRequest,
    authorization: CurrentAuthorizationContext,
    db: AsyncSession = Depends(get_db),
) -> FileContentResponse:
    """
    Write file content with conflict detection.

    Cloud mode only - used by browser editor.
    """
    _require_platform_workspace_operation(authorization, "workspace.files.editor.write")
    try:
        await assert_workspace_path_not_solution_managed(db, request.path)
        storage = FileStorageService(db)
        await _lock_file_mutation(
            db,
            location="workspace",
            scope=None,
            path=request.path,
        )

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
        updated_by = (
            authorization.requester.email
            or authorization.requester.name
            or "system"
        )
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
    **operation_route("workspace.files.editor.folder.create"),
)
async def create_folder_editor(
    authorization: CurrentAuthorizationContext,
    path: str = Query(..., description="Folder path relative to workspace root"),
    db: AsyncSession = Depends(get_db),
) -> FileMetadata:
    """
    Create a new folder.

    Cloud mode only - used by browser editor.
    """
    _require_platform_workspace_operation(
        authorization,
        "workspace.files.editor.folder.create",
    )
    try:
        await assert_workspace_path_not_solution_managed(db, path)
        storage = FileStorageService(db)
        updated_by = (
            authorization.requester.email
            or authorization.requester.name
            or "system"
        )
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
    **operation_route("workspace.files.editor.delete"),
)
async def delete_file_editor(
    authorization: CurrentAuthorizationContext,
    path: str = Query(..., description="File or folder path"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a file or folder recursively.

    Cloud mode only - used by browser editor.
    Uses S3 prefix listing to detect folders (no file_index markers needed).
    """
    _require_platform_workspace_operation(authorization, "workspace.files.editor.delete")
    from src.services.repo_storage import RepoStorage

    try:
        await assert_workspace_path_not_solution_managed(db, path, recursive=True)
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
    **operation_route("workspace.files.editor.rename"),
)
async def rename_file_editor(
    authorization: CurrentAuthorizationContext,
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
    _require_platform_workspace_operation(authorization, "workspace.files.editor.rename")
    try:
        await assert_workspace_path_not_solution_managed(db, old_path, recursive=True)
        await assert_workspace_path_not_solution_managed(db, new_path, recursive=True)
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
    **operation_route("workspace.files.search"),
)
async def search_file_contents(
    request: SearchRequest,
    http_request: Request,
    ctx: Context,
    user: CurrentActiveUser,
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """
    Search file contents for text or regex patterns.

    Searches database directly - workflows, modules, forms, and agents.
    """
    try:
        await _authorize_workspace_operation(
            http_request,
            user=user,
            db=db,
            location="workspace",
            operation_id="workspace.files.search",
        )
        results = await search_files_db(db, request, root_path="")
        return results

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
