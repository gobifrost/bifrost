"""Enforce operation permissions from current role assignments.

Typed runtime credentials keep the original user's roles and organization
reach, adding only the configured workflow capabilities. Acting identity is
separate. Lists use the same permission reach before counting or paging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from src.core.principal import UserPrincipal
from src.models.contracts.access_list import AccessEntry, CurrentGate
from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS
from src.services.authorization.context import (
    AuthorizationContext,
    BoundaryKind,
    build_authorization_context,
)
from src.services.authorization.evaluator import GLOBAL, Decision, Target, TargetKind, cross_org, decide
from src.services.authorization.privilege import is_privileged_principal
from src.services.authorization.reach import EVERYTHING, NOWHERE, OrgReach

logger = logging.getLogger(__name__)

# Operations whose fields or sub-operations are decided by a different
# permission than the entry's own (the entry names the one its usual
# request needs). ``require_operation(..., permission=...)`` accepts only
# these, so a handler cannot decide an operation by an arbitrary permission.
NARROWER_PERMISSIONS: dict[str, frozenset[str]] = {
    # Global or Platform Admin users (userlifecycle.readwrite at Global).
    "users.create": frozenset({"userlifecycle.readwrite"}),
    # Per supplied field: support fields are users.readwrite.
    "users.update": frozenset({"users.readwrite"}),
    # Per operation: set_active and replace_roles.
    "users.bulk_update": frozenset({"users.readwrite", "roleassignments.readwrite"}),
    # A base-role change.
    "PUT /api/users/{user_id}/role-assignments": frozenset({"userlifecycle.readwrite"}),
}

_ACTION_PHRASES: dict[str, str] = {
    "users.read": "view users",
    "users.readwrite": "manage users",
    "userlifecycle.readwrite": "create, move, or delete users or change their base role",
    "roleassignments.read": "view role assignments",
    "roleassignments.readwrite": "assign roles",
    "roles.read": "view roles",
    "roles.readwrite": "manage roles",
    "organizations.read": "view organizations",
    "organizations.readwrite": "manage organizations",
}

PROTECTED_TARGET_MESSAGE = (
    "You need Read and Write Privileged Access to change this account"
)


def operation_key(entry: AccessEntry) -> str:
    """The key a handler names an entry by."""
    if entry.operation_id is not None:
        return entry.operation_id
    return f"{entry.method} {entry.path}"


@cache
def _entries_by_operation() -> dict[str, AccessEntry]:
    from src.services.access_list import ACCESS_LIST

    index: dict[str, AccessEntry] = {}
    for entry in ACCESS_LIST:
        if entry.mcp_tool is not None or entry.current_gate != CurrentGate.EVALUATOR:
            continue
        key = operation_key(entry)
        assert key not in index, f"duplicate evaluator operation key {key!r}"
        index[key] = entry
    return index


def entry_for_operation(operation: str, permission: str | None = None) -> AccessEntry:
    """The evaluator entry for ``operation``, optionally decided by one of its
    ``NARROWER_PERMISSIONS`` instead of the entry's own permission."""
    entry = _entries_by_operation()[operation]
    if permission is None or permission == entry.permission:
        return entry
    if permission not in NARROWER_PERMISSIONS.get(operation, frozenset()):
        raise ValueError(f"{permission!r} does not decide {operation!r}")
    return entry.model_copy(update={"permission": permission})


@dataclass(frozen=True)
class Caller:
    """The verified principal and original user's current role context."""

    principal: UserPrincipal
    # None for embed sessions or an unknown user.
    ctx: AuthorizationContext | None

    @property
    def is_execution_credential(self) -> bool:
        return self.principal.runtime_authority is not None

    @property
    def is_platform_admin(self) -> bool:
        if self.principal.embed:
            return False
        return self.ctx is not None and self.ctx.is_platform_admin


async def load_caller(db: AsyncSession, principal: UserPrincipal) -> Caller:
    if principal.embed:
        return Caller(principal, None)
    if principal.runtime_authority is not None:
        return Caller(principal, principal.runtime_authority.run_user.ctx)
    if principal.engine_execution_id or principal.service_id or principal.service_attempt_id:
        return Caller(principal, None)
    try:
        ctx = await build_authorization_context(db, principal.user_id)
    except NoResultFound:
        ctx = None
    return Caller(principal, ctx)


def decide_for(caller: Caller, entry: AccessEntry, target: Target) -> Decision:
    """Decide from current user roles and any verified workflow powers."""
    if caller.principal.embed:
        return Decision(False, "denied:embed_session_only")
    runtime = caller.principal.runtime_authority
    if runtime is not None:
        from src.services.authorization.explain import check_target

        organization_id = runtime.organization_id if target.kind == TargetKind.HOME else target.organization_id
        trace = check_target(runtime.run_user, runtime.powers, organization_id, entry)
        stopped = next((step for step in trace.steps if step.status == "stopped"), None)
        return Decision(trace.outcome == "success", "runtime:allowed" if stopped is None else f"denied:{stopped.reason}")
    if caller.ctx is None:
        return Decision(False, "denied:unknown_user")
    return decide(caller.ctx, entry, target)


def _note_enforced_decision(caller: Caller, entry: AccessEntry, target: Target) -> None:
    if caller.ctx is None or entry.permission is None:
        return
    from src.services.authorization.explain import RunUser, check_target

    runtime = caller.principal.runtime_authority
    run_user = (
        RunUser(caller.ctx.user_id, caller.ctx, None, is_active=caller.principal.is_active)
        if runtime is None else runtime.run_user
    )
    powers = None if runtime is None else runtime.powers
    organization_id = (
        caller.ctx.home_organization_id if runtime is None else runtime.organization_id
    ) if target.kind == TargetKind.HOME else target.organization_id
    access_checks.note_decision(
        "permission", organization_id, run_user=run_user,
        trace=check_target(run_user, powers, organization_id, entry), permission=entry.permission,
    )


def denial_message(permission: str | None) -> str:
    phrase = _ACTION_PHRASES.get(permission or "", "perform this action")
    return f"You don't have permission to {phrase}"


def _denied(permission: str | None) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, denial_message(permission))


def allows_operation(
    caller: Caller,
    operation: str,
    target: Target,
    *,
    permission: str | None = None,
) -> bool:
    """``require_operation`` without raising, for per-row outcomes (bulk
    failures, what a caller may grant)."""
    return decide_for(caller, entry_for_operation(operation, permission), target).allowed


def require_operation(
    caller: Caller,
    operation: str,
    target: Target,
    *,
    permission: str | None = None,
) -> Decision:
    """Raise 403 unless ``caller`` may perform ``operation`` at ``target``."""
    entry = entry_for_operation(operation, permission)
    decision = decide_for(caller, entry, target)
    logger.debug(
        "authorization %s %s@%s -> %s",
        operation,
        entry.permission,
        target,
        decision.rule,
    )
    _note_enforced_decision(caller, entry, target)
    if not decision.allowed:
        raise _denied(entry.permission)
    return decision


async def authorize_operation(
    db: AsyncSession,
    principal: UserPrincipal,
    operation: str,
    target: Target,
    *,
    permission: str | None = None,
) -> Caller:
    """Load the caller and ``require_operation``; returns the caller for any
    further row-level decisions."""
    caller = await load_caller(db, principal)
    require_operation(caller, operation, target, permission=permission)
    return caller


def org_target(organization_id: UUID | None) -> Target:
    """The target for a row that belongs to ``organization_id`` (None: Global)."""
    return GLOBAL if organization_id is None else cross_org(organization_id)


def require_unprotected(caller: Caller, target_is_privileged: bool, target_organization_id: UUID | None) -> None:
    if not target_is_privileged:
        return
    from src.services.authorization.explain import permission_entry

    entry = permission_entry("privilegedaccess.readwrite")
    _note_enforced_decision(caller, entry, org_target(target_organization_id))
    if not allows_permission(caller, "privilegedaccess.readwrite", target_organization_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, PROTECTED_TARGET_MESSAGE)


def permitted_organizations(caller: Caller, permission: str) -> OrgReach:
    """Where ``caller`` holds ``permission``, as ``decide`` would rule for an
    organization-boundary entry of an evaluator-gated operation."""
    if caller.principal.embed:
        return NOWHERE
    runtime = caller.principal.runtime_authority
    if runtime is not None and runtime.powers is not None:
        from src.models.contracts.workflow_permissions import WorkflowPermissionMode

        if runtime.powers.mode == WorkflowPermissionMode.FULL or any(
            grant.permission == permission for grant in runtime.powers.grants
        ):
            return user_reach(caller)
    ctx = caller.ctx
    if ctx is None:
        return NOWHERE
    if ctx.is_platform_admin and permission not in WILDCARD_EXCLUDED_PERMISSIONS:
        return EVERYTHING
    organization_ids: set[UUID] = set()
    managed = False
    include_global = False
    if permission in ctx.base_permissions:
        if ctx.home_organization_id is None:
            include_global = True
        else:
            organization_ids.add(ctx.home_organization_id)
    for grant in ctx.role_grants:
        if permission not in grant.permissions:
            continue
        for boundary in grant.boundaries:
            if boundary.kind == BoundaryKind.ORGANIZATION and boundary.organization_id is not None:
                organization_ids.add(boundary.organization_id)
            elif boundary.kind == BoundaryKind.MANAGED_ORGANIZATIONS:
                managed = True
            elif boundary.kind == BoundaryKind.PLATFORM:
                include_global = True
    return OrgReach(
        organization_ids=frozenset(organization_ids),
        managed=managed,
        include_global=include_global,
    )


def user_reach(caller: Caller) -> OrgReach:
    """Permission-independent reach of the original user, including Global."""
    ctx = caller.ctx
    if ctx is None or caller.principal.embed:
        return NOWHERE
    if ctx.is_platform_admin:
        return EVERYTHING
    organizations = set() if ctx.home_organization_id is None else {ctx.home_organization_id}
    managed = False
    for grant in ctx.role_grants:
        for boundary in grant.boundaries:
            if boundary.kind == BoundaryKind.ORGANIZATION and boundary.organization_id is not None:
                organizations.add(boundary.organization_id)
            elif boundary.kind == BoundaryKind.MANAGED_ORGANIZATIONS:
                managed = True
    return OrgReach(organization_ids=frozenset(organizations), managed=managed, include_global=True)


def allows_permission(caller: Caller, permission: str, organization_id: UUID | None) -> bool:
    """Check a named power at the actual target, including runtime capabilities."""
    from src.services.authorization.explain import permission_entry

    return decide_for(caller, permission_entry(permission), org_target(organization_id)).allowed


class PermissionDenied(Exception):
    """A named authorization refusal shared by business services and HTTP adapters."""

    status_code = 403

    def __init__(self, permission: str) -> None:
        self.permission = permission
        self.detail = denial_message(permission)
        super().__init__(self.detail)


def require_named_permission(caller: Caller, permission: str, organization_id: UUID | None) -> None:
    """Enforce and record a named permission without an HTTP-specific exception."""
    from src.services.authorization.explain import permission_entry

    entry = permission_entry(permission)
    target = org_target(organization_id)
    _note_enforced_decision(caller, entry, target)
    if not decide_for(caller, entry, target).allowed:
        raise PermissionDenied(permission)


def require_permission(caller: Caller, permission: str, organization_id: UUID | None) -> None:
    try:
        require_named_permission(caller, permission, organization_id)
    except PermissionDenied as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


def external_credential_restrictions(caller: Caller) -> bool:
    """Direct external callers retain credential restrictions; workflow powers are explicit."""
    runtime = caller.principal.runtime_authority
    original_external = caller.ctx.is_external if caller.ctx is not None else caller.principal.is_external
    return original_external and (runtime is None or runtime.powers is None)


def operation_reach(caller: Caller, operation: str, *, permission: str | None = None) -> OrgReach:
    """The caller's reach for ``operation``; 403 when it reaches nowhere."""
    entry = entry_for_operation(operation, permission)
    assert entry.permission is not None and entry.boundary != "platform"
    reach = permitted_organizations(caller, entry.permission)
    if reach.is_empty:
        raise _denied(entry.permission)
    return reach


async def held_permissions_by_user(
    db: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, frozenset[str]]:
    """What each user holds at any boundary (base role and every additional
    role, the stored wildcard row of Platform Admin included), in three
    queries however many users are asked about.

    Every ``user_roles`` row counts, with or without a boundary row: for
    deciding whether someone is protected, an assignment that applies
    nowhere yet still counts.
    """
    from src.models.orm.users import RolePermission, User, UserRole

    if not user_ids:
        return {}
    base_rows = (
        await db.execute(select(User.id, User.base_role_id).where(User.id.in_(user_ids)))
    ).all()
    role_rows = (
        await db.execute(
            select(UserRole.user_id, UserRole.role_id).where(UserRole.user_id.in_(user_ids))
        )
    ).all()
    roles_by_user: dict[UUID, set[UUID]] = {user_id: {base} for user_id, base in base_rows}
    for user_id, role_id in role_rows:
        roles_by_user.setdefault(user_id, set()).add(role_id)
    all_role_ids = {role_id for roles in roles_by_user.values() for role_id in roles}
    permission_rows = (
        await db.execute(
            select(RolePermission.role_id, RolePermission.permission).where(
                RolePermission.role_id.in_(all_role_ids)
            )
        )
    ).all() if all_role_ids else []
    permissions_by_role: dict[UUID, set[str]] = {}
    for role_id, permission in permission_rows:
        permissions_by_role.setdefault(role_id, set()).add(permission)

    held: dict[UUID, frozenset[str]] = {}
    for user_id, roles in roles_by_user.items():
        permissions: set[str] = set()
        for role_id in roles:
            permissions |= permissions_by_role.get(role_id, set())
        held[user_id] = frozenset(permissions)
    return held


async def privileged_user_ids(db: AsyncSession, user_ids: list[UUID]) -> frozenset[UUID]:
    held = await held_permissions_by_user(db, user_ids)
    return frozenset(user_id for user_id, permissions in held.items() if is_privileged_principal(permissions))
