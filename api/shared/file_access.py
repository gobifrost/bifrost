"""Transport-neutral file access helpers shared by the SDK file paths.

Single implementation of the policy/scope plumbing used by the cloud-mode
SDK file paths (``files.read``/``read_bytes``, ``files.list`` without
``include_metadata``, ``files.exists``, ``files.stat``, ``files.write``,
``files.delete``, and signed-URL presigning). Both the HTTP router
(``api/src/routers/files.py``) and the shared SDK service
(``api/shared/sdk_files.py``) import from here — no duplicated policy logic.

Only transport-neutral failures are raised (:class:`FileServiceError` with
an HTTP-style status). The HTTP adapter maps them to ``HTTPException``;
worker-local calls read the same status/detail.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import select, text

from src.services.audit import emit_file_policy_deny

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.core.principal import UserPrincipal

_USE_CONTEXT_SOLUTION_ID: Any = object()
_T = TypeVar("_T")


class FileServiceError(Exception):
    """Transport-neutral file failure with an HTTP-style status.

    Raised by this module and by ``shared.sdk_files`` so the HTTP handler
    (``HTTPException``) maps the same failure to an HTTP response; worker-local
    calls read the same status/detail.
    """

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass
class FileCaller:
    """Trusted caller for SDK file reads.

    Carries the full token-derived ``UserPrincipal`` (role/claim state for
    file policies), the DB session, the execution org, the Solution
    target/caller IDs, and the app ID. Built by the HTTP adapter from its
    authenticated context; engine children reach the same handler over the
    worker-local engine socket, so authority is always derived from the
    authenticated principal and never from child-supplied fields.
    """

    user: UserPrincipal
    db: AsyncSession
    org_id: UUID | None
    solution_id: str | None = None
    caller_solution_id: str | None = None
    app_id: str | None = None

    @classmethod
    def from_context(cls, ctx: Any) -> FileCaller:
        """Build a trusted caller from an authenticated execution context."""
        return cls(
            user=ctx.user,
            db=ctx.db,
            org_id=ctx.org_id,
            solution_id=ctx.solution_id,
            caller_solution_id=getattr(ctx, "caller_solution_id", None),
            app_id=getattr(ctx, "app_id", None),
        )


def file_org_id(caller_or_ctx: Any, location: str, requested_scope: str | None) -> UUID | None:
    """Resolve the target org — same rule the Tables SDK uses.

    A non-superuser is pinned to their own org and the requested ``scope``
    is ignored; a superuser honors ``scope``. ``workspace`` is unscoped.
    """
    from src.core.org_filter import resolve_target_org

    user = caller_or_ctx.user
    org_id = getattr(caller_or_ctx, "org_id", None)
    if location == "workspace":
        return None
    return resolve_target_org(user, requested_scope, org_id)


def storage_scope(org_id: UUID | None) -> str:
    """Path segment for scoped locations: org UUID, else literal ``global``."""
    return str(org_id) if org_id is not None else "global"


def resolve_effective_scope(
    caller_or_ctx: Any, location: str, requested_scope: str | None
) -> str | None:
    """Storage-scope string with solution-context priority.

    ``solution_id`` wins over every other signal (including a superuser's
    explicit ``requested_scope``). Raises :class:`FileServiceError` 400 for
    the workspace-in-solution case.
    """
    solution_id = getattr(caller_or_ctx, "solution_id", None)
    if solution_id is not None:
        if location == "workspace":
            raise FileServiceError(
                400, "workspace is not available in solution file context"
            )
        return str(solution_id)
    return storage_scope(file_org_id(caller_or_ctx, location, requested_scope))


def ctx_solution_id(caller_or_ctx: Any, location: str) -> UUID | None:
    """Install UUID from context. Canonical parse lives in solution_scope."""
    from src.services.solution_scope import parse_ctx_solution_id

    return parse_ctx_solution_id(caller_or_ctx)


async def install_org_id(caller_or_ctx: Any, solution_id: UUID | None) -> UUID | None:
    """Install's ``organization_id`` from the DB (fallback: caller org)."""
    if solution_id is None:
        return getattr(caller_or_ctx, "org_id", None)
    from src.models.orm.solutions import Solution as SolutionORM

    db = caller_or_ctx.db
    row = (
        await db.execute(
            select(SolutionORM).where(SolutionORM.id == solution_id)
        )
    ).scalar_one_or_none()
    return row.organization_id if row is not None else getattr(
        caller_or_ctx, "org_id", None
    )


async def authorize_file_policy(
    caller_or_ctx: Any,
    *,
    action: str,
    location: str,
    scope: str | None,
    path: str,
    content_type: str | None = None,
    solution_id: UUID | None = None,
    organization_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
) -> bool:
    """Evaluate file policy access (non-final per-tier probe, no audit)."""
    from src.services.file_policy_service import FilePolicyService
    from src.models.contracts.policies import FileAction

    user = caller_or_ctx.user
    db = caller_or_ctx.db
    if location == "workspace":
        return bool(user.is_superuser)

    policy_organization_id: UUID | None = None
    resolved_solution_id = solution_id
    if organization_id is not _USE_CONTEXT_SOLUTION_ID:
        policy_organization_id = cast(UUID | None, organization_id)
    else:
        if scope is None:
            return False
        if resolved_solution_id is not None:
            policy_organization_id = await install_org_id(
                caller_or_ctx, resolved_solution_id
            )
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

    service = FilePolicyService(db)
    return await service.is_allowed(
        cast(FileAction, policy_action),
        organization_id=policy_organization_id,
        location=location,
        path=path,
        user=user,
        solution_id=resolved_solution_id,
    )


async def deny_file_policy(
    caller_or_ctx: Any,
    *,
    action: str,
    location: str,
    path: str,
    scope: str | None = None,
    solution_id: UUID | None = None,
) -> None:
    """Record a ``policy.deny`` audit row and raise 403 (final denials only).

    The single choke point for every *final* denial — call exactly once per
    request at rejection time, not per per-tier probe. Mirrors the tables.py
    pattern: emit then commit, so the audit row survives the error.
    """
    db = caller_or_ctx.db
    await emit_file_policy_deny(
        db,
        policy_action=action,
        location=location,
        path=path,
        scope=scope,
        solution_id=solution_id,
    )
    await db.commit()
    raise FileServiceError(
        403,
        {
            "message": "File policy denied",
            "action": action,
            "location": location,
            "path": path,
            "scope": scope,
            "solution_id": str(solution_id) if solution_id else None,
        },
    )


async def require_file_policy(
    caller_or_ctx: Any,
    *,
    action: str,
    location: str,
    scope: str | None,
    path: str,
    content_type: str | None = None,
    solution_id: UUID | None = None,
    organization_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
) -> None:
    allowed = await authorize_file_policy(
        caller_or_ctx,
        action=action,
        location=location,
        scope=scope,
        path=path,
        solution_id=solution_id,
        organization_id=organization_id,
    )
    if not allowed:
        await deny_file_policy(
            caller_or_ctx,
            action=action,
            location=location,
            path=path,
            scope=scope,
            solution_id=solution_id,
        )


async def require_declared_solution_file_location(
    caller_or_ctx: Any,
    *,
    solution_id: UUID | None,
    location: str,
) -> None:
    if solution_id is None:
        return
    from src.services.solution_scope import solution_declares_file_location

    if not await solution_declares_file_location(
        caller_or_ctx.db, solution_id, location
    ):
        raise FileServiceError(404, f"File location '{location}' not found")


def relative_list_path(path: str, *, location: str, scope: str | None) -> str:
    if location == "workspace":
        return path
    from shared.file_paths import resolve_s3_key

    try:
        prefix = resolve_s3_key(location, scope, "")
    except ValueError:
        return path
    return path[len(prefix):] if path.startswith(prefix) else path


def tiers_for_backend_mode(tiers: list[_T], mode: str) -> list[_T]:
    if mode == "local":
        return tiers[:1]
    return tiers


async def lock_file_mutation(
    db: AsyncSession,
    *,
    location: str,
    scope: str | None,
    path: str,
) -> None:
    """Serialize competing file mutations for one logical file path."""
    lock_key = f"{location}:{scope or ''}:{path}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )


async def filter_listed_paths(
    caller_or_ctx: Any,
    *,
    paths: list[str],
    location: str,
    scope: str | None,
    action: str = "list",
    solution_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
    organization_id: UUID | None | object = _USE_CONTEXT_SOLUTION_ID,
) -> list[str]:
    resolved_solution_id = (
        ctx_solution_id(caller_or_ctx, location)
        if solution_id is _USE_CONTEXT_SOLUTION_ID
        else cast(UUID | None, solution_id)
    )
    allowed_paths = []
    for listed_path in paths:
        policy_path = relative_list_path(
            listed_path, location=location, scope=scope
        )
        if await authorize_file_policy(
            caller_or_ctx,
            action=action,
            location=location,
            scope=scope,
            path=policy_path,
            solution_id=resolved_solution_id,
            organization_id=organization_id,
        ):
            allowed_paths.append(listed_path)
    return allowed_paths


def content_version(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


async def get_file_stat(
    db: AsyncSession,
    path: str,
    location: str,
    scope: str | None,
    mode: str,
) -> Any:
    """Load file metadata for conflict detection and stat output."""
    from src.services.file_backend import get_backend

    try:
        backend = get_backend(mode, db)
        content = await backend.read(path, location, scope=scope)
    except FileNotFoundError:
        from src.models import FileStatResponse

        return FileStatResponse(path=path, exists=False)

    meta = None
    if location == "workspace" and mode == "cloud":
        from src.models.orm.file_index import FileIndex

        row = await db.execute(
            select(FileIndex.updated_at, FileIndex.updated_by).where(
                FileIndex.path == path
            )
        )
        meta = row.first()
    from src.models import FileStatResponse

    return FileStatResponse(
        path=path,
        exists=True,
        version=content_version(content),
        size=len(content),
        last_modified=meta.updated_at.isoformat() if meta and meta.updated_at else None,
        updated_by=meta.updated_by if meta else None,
    )
