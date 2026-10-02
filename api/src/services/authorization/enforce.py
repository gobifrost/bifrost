"""Enforcing the authorization evaluator on cut-over routes (R3a).

A route is cut over when its access-list entry has
``current_gate=evaluator``: its dependency is ``CurrentActiveUser`` and the
handler (or the service it calls) decides with this module, naming the
entry by its operation key (``operation_key``: the operation-catalog id, or
``"<METHOD> <path>"`` for an uncatalogued route).

Callers. ``load_caller`` reads the person's ``AuthorizationContext`` from the
database on every request, so Platform Admin is the assignment of the
Platform Admin role as stored now, not the token's ``is_superuser`` claim. Execution credentials are
the exception, decided by one explicit, staged rule (see ``decide_for``).

Targets. A row-level target is the target object's own organization:
``org_target(user.organization_id)`` for a user,
``org_target(org.id)`` for an organization, ``GLOBAL`` for Global users and
for platform-boundary entries (role definitions, creating organizations).

Existence before permission. A caller with no reach at all for the
operation's permission gets 403 before anything is looked up, so they learn
nothing about which rows exist. A caller with reach somewhere gets 404 for a
missing row and 403 for a row outside their reach.

Protected targets. A user who holds any privileged permission at any
boundary, or the Platform Admin wildcard, can only be changed (support,
lifecycle, role assignment, invites, signing out) by a Platform Admin
(``require_unprotected``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.models.contracts.access_list import AccessEntry, CurrentGate
from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS
from src.services.authorization.context import (
    AuthorizationContext,
    BoundaryKind,
    build_authorization_context,
)
from src.services.authorization.evaluator import GLOBAL, Decision, Target, cross_org, decide
from src.services.authorization.privilege import is_privileged_principal

logger = logging.getLogger(__name__)

# Operations whose fields or sub-operations are decided by a different
# permission than the entry's own (the entry names the one its usual
# request needs). ``require_operation(..., permission=...)`` accepts only
# these, so a handler cannot decide an operation by an arbitrary permission.
NARROWER_PERMISSIONS: dict[str, frozenset[str]] = {
    # Global or Platform Admin users (users.lifecycle.readwrite at Global).
    "users.create": frozenset({"users.lifecycle.readwrite"}),
    # Per supplied field: support fields are users.readwrite.
    "users.update": frozenset({"users.readwrite"}),
    # Per operation: set_active and replace_roles.
    "users.bulk_update": frozenset({"users.readwrite", "roleassignments.readwrite"}),
    # A base-role change.
    "PUT /api/users/{user_id}/role-assignments": frozenset({"users.lifecycle.readwrite"}),
}

_ACTION_PHRASES: dict[str, str] = {
    "users.read": "view users",
    "users.readwrite": "manage users",
    "users.lifecycle.readwrite": "create, move, or delete users or change their base role",
    "roleassignments.read": "view role assignments",
    "roleassignments.readwrite": "assign roles",
    "roles.read": "view roles",
    "roles.readwrite": "manage roles",
    "organizations.read": "view organizations",
    "organizations.readwrite": "manage organizations",
}

PROTECTED_TARGET_MESSAGE = (
    "Only a Platform Admin can change a Platform Admin or another user with privileged access"
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
    """Who is asking: a person (with their context), or an execution
    credential (engine or service token, no context)."""

    principal: UserPrincipal
    # None for execution credentials and embed sessions, and for a token
    # whose user no longer exists.
    ctx: AuthorizationContext | None

    @property
    def is_execution_credential(self) -> bool:
        return bool(self.principal.engine_execution_id or self.principal.service_id)

    @property
    def is_platform_admin(self) -> bool:
        if self.principal.embed:
            return False
        if self.is_execution_credential:
            return self.principal.is_superuser
        return self.ctx is not None and self.ctx.is_platform_admin


async def load_caller(db: AsyncSession, principal: UserPrincipal) -> Caller:
    if principal.embed or principal.engine_execution_id or principal.service_id:
        return Caller(principal, None)
    try:
        ctx = await build_authorization_context(db, principal.user_id)
    except NoResultFound:
        ctx = None
    return Caller(principal, ctx)


def decide_for(caller: Caller, entry: AccessEntry, target: Target) -> Decision:
    """The enforced decision for ``caller`` on an evaluator entry.

    Execution credentials (a token carrying ``engine_execution_id`` or
    ``service_id``) are decided exactly as the superuser dependency these
    routes had decided them: allowed iff the token is a superuser token. A
    workflow's engine token (the system user, superuser) keeps reaching the
    SDK user/role/organization routes over the worker socket; a supervised
    service token (never superuser) keeps being refused. This is staged
    enforcement: R3b replaces it with a typed engine execution principal.
    """
    if caller.principal.embed:
        return Decision(False, "denied:embed_session_only")
    if caller.is_execution_credential:
        if caller.principal.is_superuser:
            return Decision(True, "execution_credential:superuser")
        return Decision(False, "denied:execution_credential_not_superuser")
    if caller.ctx is None:
        return Decision(False, "denied:unknown_user")
    return decide(caller.ctx, entry, target)


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


def require_unprotected(caller: Caller, target_is_privileged: bool) -> None:
    if target_is_privileged and not caller.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, PROTECTED_TARGET_MESSAGE)


@dataclass(frozen=True)
class OrgReach:
    """The organizations an operation reaches for a caller, for filtering
    lists (and the counts that go with them) before paging.

    ``everything``: every organization and Global (Platform Admin, or a
    superuser execution credential). Otherwise the union of explicit
    ``organization_ids`` (``organization`` boundaries, and the home org when
    the base role holds the permission), ``managed`` (every org except the
    provider org; never Global), and ``include_global`` (a ``platform``
    boundary, which covers Global rows only, not every organization).
    """

    everything: bool = False
    organization_ids: frozenset[UUID] = frozenset()
    managed: bool = False
    include_global: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.everything or self.organization_ids or self.managed or self.include_global)

    def covers(self, organization_id: UUID | None) -> bool:
        if self.everything:
            return True
        if organization_id is None:
            return self.include_global
        return organization_id in self.organization_ids or (
            self.managed and organization_id != PROVIDER_ORG_ID
        )

    def where(self, column) -> ColumnElement[bool] | None:
        """SQL predicate on an organization-id ``column``; None = no filter."""
        if self.everything:
            return None
        clauses: list[ColumnElement[bool]] = []
        if self.organization_ids:
            clauses.append(column.in_(self.organization_ids))
        if self.managed:
            clauses.append(and_(column.is_not(None), column != PROVIDER_ORG_ID))
        if self.include_global:
            clauses.append(column.is_(None))
        if not clauses:
            return false()
        return or_(*clauses)


EVERYTHING = OrgReach(everything=True)
NOWHERE = OrgReach()


def permitted_organizations(caller: Caller, permission: str) -> OrgReach:
    """Where ``caller`` holds ``permission``, as ``decide`` would rule for an
    organization-boundary entry of an evaluator-gated operation."""
    if caller.principal.embed:
        return NOWHERE
    if caller.is_execution_credential:
        return EVERYTHING if caller.principal.is_superuser else NOWHERE
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
