"""Role assignments: a user's base role and additional roles with boundaries.

Every path that adds a ``user_roles`` row writes its boundaries here
(``insert_assignment``); removing a ``user_roles`` row removes its boundaries
through the database cascade. The default boundary is the user's home
organization, or ``platform`` for a Global user.

The grant ceiling (``src.services.authorization.privilege``): an actor who is
not a Platform Admin may grant or remove only roles that carry no
permissions and are not builtin, never on a privileged user, and may write
only ``organization`` boundaries their own ``roleassignments.readwrite``
reach covers. A Platform Admin may assign anything, with limits that hold for
everyone: a base role is User or a custom role, never a builtin role beyond
User; Platform Operator is held only by people in the provider organization
and applies only at managed organizations or at customer organizations (not
the provider org, not Platform); Platform Admin is an additional role held only
by people in the provider organization or Global users, always at the
``platform`` boundary, and only a Platform Admin may grant or remove it; and
Secrets Reader has one fixed set of boundaries (``SECRETS_READER_BOUNDARIES``)
that nobody chooses. Every one of these carries permissions, so only a
Platform Admin can grant or remove them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import (
    BASE_ROLE_IDS,
    DECRYPTION_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
)
from shared.system_account_guard import SYSTEM_ACCOUNT_ROLE_MESSAGE, is_system_account
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.role_assignments import (
    AssignableRole,
    AssignedRole,
    AuthorizationBaseRole,
    AuthorizationBoundary,
    AuthorizationGrant,
    AuthorizationSummary,
    RoleBoundaryInput,
    RoleBoundaryPublic,
    RoleSummary,
    UserRoleAssignmentsResponse,
    UserRoleAssignmentsUpdate,
)
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.services.authorization.context import (
    Boundary,
    BoundaryKind,
    build_authorization_context,
)
from src.services.authorization.enforce import (
    Caller,
    allows_operation,
    held_permissions_by_user,
    operation_reach,
    org_target,
    permitted_organizations,
    require_operation,
    require_unprotected,
)
from src.services.authorization.reach import OrgReach
from src.services.authorization.privilege import (
    is_privileged_principal,
    may_change_role_assignment,
)

GET_OPERATION = "GET /api/users/{user_id}/role-assignments"
PUT_OPERATION = "PUT /api/users/{user_id}/role-assignments"

CEILING_MESSAGE = (
    "You can only assign or remove roles that carry no permissions, on users "
    "without privileged access"
)
BOUNDARY_REACH_MESSAGE = (
    "You can only make a role apply at organizations where you can assign roles"
)
ADMIN_ROLE_MESSAGE = "Only a Platform Admin can grant or remove the Platform Admin role"
OPERATOR_HOLDER_MESSAGE = (
    "Platform Operator can only be given to people in the provider organization"
)
SECRETS_READER_BOUNDARY_MESSAGE = "Secrets Reader applies everywhere"
ADMIN_HOLDER_MESSAGE = (
    "Move the user to the provider organization before making them a Platform Admin"
)
ADMIN_REMOVAL_MESSAGE = (
    "Move the user into an organization before removing the Platform Admin role"
)


class RoleAssignmentError(Exception):
    """A refused role-assignment change, with an HTTP-style status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class RoleInfo:
    id: UUID
    name: str
    description: str | None
    is_builtin: bool
    permissions: frozenset[str]


# Secrets Reader applies at exactly these boundaries. A secret is decrypted at
# a target: Global is covered by ``platform`` only, the provider organization
# by an ``organization`` boundary naming it, and every customer organization by
# ``managed_organizations``; none of the three reaches the others' targets.
SECRETS_READER_BOUNDARIES = frozenset(
    {
        Boundary(BoundaryKind.PLATFORM),
        Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),
        Boundary(BoundaryKind.ORGANIZATION, PROVIDER_ORG_ID),
    }
)


def default_boundaries(home_organization_id: UUID | None) -> frozenset[Boundary]:
    if home_organization_id is None:
        return frozenset({Boundary(BoundaryKind.PLATFORM)})
    return frozenset({Boundary(BoundaryKind.ORGANIZATION, home_organization_id)})


async def insert_assignment(
    session: AsyncSession,
    *,
    user_id: UUID,
    role_id: UUID,
    boundaries: Iterable[Boundary],
    assigned_by: str,
) -> None:
    """Add a ``user_roles`` row and its boundary rows."""
    session.add(
        UserRole(
            user_id=user_id,
            role_id=role_id,
            assigned_by=assigned_by,
            assigned_at=datetime.now(timezone.utc),
        )
    )
    # The boundary rows reference the assignment by a composite foreign key.
    await session.flush()
    _add_boundaries(session, user_id, role_id, boundaries)
    await session.flush()


def _add_boundaries(
    session: AsyncSession, user_id: UUID, role_id: UUID, boundaries: Iterable[Boundary]
) -> None:
    for boundary in boundaries:
        session.add(
            UserRoleBoundary(
                user_id=user_id,
                role_id=role_id,
                kind=boundary.kind.value,
                organization_id=boundary.organization_id,
            )
        )


async def load_roles(session: AsyncSession, role_ids: Iterable[UUID]) -> dict[UUID, RoleInfo]:
    ids = set(role_ids)
    if not ids:
        return {}
    roles = (await session.execute(select(Role).where(Role.id.in_(ids)))).scalars().all()
    permission_rows = (
        await session.execute(
            select(RolePermission.role_id, RolePermission.permission).where(
                RolePermission.role_id.in_(ids)
            )
        )
    ).all()
    permissions: dict[UUID, set[str]] = {}
    for role_id, permission in permission_rows:
        permissions.setdefault(role_id, set()).add(permission)
    return {
        role.id: RoleInfo(
            id=role.id,
            name=role.name,
            description=role.description,
            is_builtin=role.is_builtin,
            permissions=frozenset(permissions.get(role.id, ())),
        )
        for role in roles
    }


def check_role_change(caller: Caller, role: RoleInfo, target_permissions: frozenset[str]) -> None:
    """The grant ceiling for granting or removing one role on one user."""
    if not may_change_role_assignment(
        actor_is_platform_admin=caller.is_platform_admin,
        role_id=role.id,
        role_permissions=role.permissions,
        target_permissions=target_permissions,
    ):
        raise RoleAssignmentError(403, CEILING_MESSAGE)


def may_hold(role: RoleInfo, holder_organization_id: UUID | None) -> bool:
    """Whether someone in ``holder_organization_id`` may be given ``role``:
    Platform Operator is for people in the provider organization only, and
    Platform Admin for people in the provider organization or Global users.
    Removing an assignment someone already holds is never refused by this."""
    if role.id == PLATFORM_ADMIN_ROLE_ID:
        return holder_organization_id in (None, PROVIDER_ORG_ID)
    return role.id != PLATFORM_OPERATOR_ROLE_ID or holder_organization_id == PROVIDER_ORG_ID


def check_holder(role: RoleInfo, holder_organization_id: UUID | None) -> None:
    if not may_hold(role, holder_organization_id):
        if role.id == PLATFORM_ADMIN_ROLE_ID:
            raise RoleAssignmentError(409, ADMIN_HOLDER_MESSAGE)
        raise RoleAssignmentError(422, OPERATOR_HOLDER_MESSAGE)


def _role_boundary_rule(role: RoleInfo) -> tuple[frozenset[BoundaryKind], bool]:
    """The boundary kinds ``role`` may apply at, and whether an
    ``organization`` boundary may name the provider org, whoever assigns it."""
    if role.id == PLATFORM_OPERATOR_ROLE_ID:
        return frozenset({BoundaryKind.ORGANIZATION, BoundaryKind.MANAGED_ORGANIZATIONS}), False
    if role.id == PLATFORM_ADMIN_ROLE_ID:
        return frozenset({BoundaryKind.PLATFORM}), True
    return frozenset(BoundaryKind), True


def boundary_placement(caller: Caller, role: RoleInfo) -> tuple[frozenset[BoundaryKind], bool]:
    """Where ``caller`` may make ``role`` apply (``check_boundaries`` decides
    the same): the role's own rule, narrowed to ``organization`` boundaries
    for anyone but a Platform Admin (and those only within their reach)."""
    kinds, provider_allowed = _role_boundary_rule(role)
    if not caller.is_platform_admin:
        kinds = kinds & {BoundaryKind.ORGANIZATION}
    return kinds, provider_allowed


def check_boundaries(
    caller: Caller, reach: OrgReach, role: RoleInfo, boundaries: frozenset[Boundary]
) -> None:
    """Where ``role`` may be written to apply."""
    if not boundaries:
        raise RoleAssignmentError(422, f"'{role.name}' needs at least one boundary")
    if role.id == DECRYPTION_ROLE_ID:
        if boundaries != SECRETS_READER_BOUNDARIES:
            raise RoleAssignmentError(422, SECRETS_READER_BOUNDARY_MESSAGE)
        return
    kinds, provider_allowed = _role_boundary_rule(role)
    for boundary in boundaries:
        if boundary.kind not in kinds or (
            not provider_allowed
            and boundary.kind == BoundaryKind.ORGANIZATION
            and boundary.organization_id == PROVIDER_ORG_ID
        ):
            if role.id == PLATFORM_ADMIN_ROLE_ID:
                raise RoleAssignmentError(422, "Platform Admin applies everywhere")
            raise RoleAssignmentError(
                422,
                "Platform Operator applies only at managed organizations or at "
                "customer organizations",
            )
    if caller.is_platform_admin:
        return
    for boundary in boundaries:
        if boundary.kind != BoundaryKind.ORGANIZATION or not reach.covers(boundary.organization_id):
            raise RoleAssignmentError(403, BOUNDARY_REACH_MESSAGE)


@dataclass(frozen=True)
class AssignmentTarget:
    user: User
    held: frozenset[str]

    @property
    def is_privileged(self) -> bool:
        return is_privileged_principal(self.held)


async def _load_target(session: AsyncSession, user_id: UUID) -> AssignmentTarget | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    held = await held_permissions_by_user(session, [user_id])
    return AssignmentTarget(user, held.get(user_id, frozenset()))


async def _current_assignments(
    session: AsyncSession, user_id: UUID
) -> dict[UUID, frozenset[Boundary]]:
    role_ids = (
        await session.execute(select(UserRole.role_id).where(UserRole.user_id == user_id))
    ).scalars().all()
    boundary_rows = (
        await session.execute(
            select(
                UserRoleBoundary.role_id, UserRoleBoundary.kind, UserRoleBoundary.organization_id
            ).where(UserRoleBoundary.user_id == user_id)
        )
    ).all()
    boundaries: dict[UUID, set[Boundary]] = {role_id: set() for role_id in role_ids}
    for role_id, kind, organization_id in boundary_rows:
        if role_id in boundaries:
            boundaries[role_id].add(Boundary(BoundaryKind(kind), organization_id))
    return {role_id: frozenset(items) for role_id, items in boundaries.items()}


async def _all_roles(session: AsyncSession) -> dict[UUID, RoleInfo]:
    role_ids = (await session.execute(select(Role.id))).scalars().all()
    return await load_roles(session, role_ids)


def _assignable_roles(
    caller: Caller,
    target: AssignmentTarget,
    roles: dict[UUID, RoleInfo],
    held_role_ids: frozenset[UUID],
) -> list[AssignableRole]:
    """Roles ``caller`` may grant to ``target``, or remove from them: the
    same rules ``replace_role_assignments`` applies, so the UI never
    re-implements them. A role ``target`` holds (``held_role_ids``) but can
    no longer be given stays listed with ``can_be_additional`` false, so it
    can be removed."""
    org = org_target(target.user.organization_id)
    if not allows_operation(caller, PUT_OPERATION, org):
        return []
    if target.is_privileged and not caller.is_platform_admin:
        return []
    current_base = roles[target.user.base_role_id]
    may_change_base = allows_operation(
        caller, PUT_OPERATION, org, permission="users.lifecycle.readwrite"
    ) and _may_change(caller, current_base, target.held)
    out: list[AssignableRole] = []
    for role in sorted(roles.values(), key=lambda r: (not r.is_builtin, r.name.lower())):
        if not _may_change(caller, role, target.held):
            continue
        # A Global user can't lose Platform Admin, so it is neither offered
        # nor removable.
        if role.id == PLATFORM_ADMIN_ROLE_ID and target.user.organization_id is None:
            continue
        can_be_base = may_change_base and (
            role.id in BASE_ROLE_IDS or not role.is_builtin
        )
        can_be_additional = role.id not in BASE_ROLE_IDS and may_hold(
            role, target.user.organization_id
        )
        # A role the user already holds stays listed (the caller may remove
        # it) even when it can no longer be given to them.
        if not (can_be_base or can_be_additional or role.id in held_role_ids):
            continue
        kinds, provider_allowed = boundary_placement(caller, role)
        fixed = role.id == DECRYPTION_ROLE_ID
        out.append(
            AssignableRole(
                id=role.id,
                name=role.name,
                description=role.description,
                is_builtin=role.is_builtin,
                permissions=sorted(role.permissions),
                can_be_base=can_be_base,
                can_be_additional=can_be_additional,
                boundary_kinds=[]
                if fixed
                else [kind.value for kind in BoundaryKind if kind in kinds],
                provider_organization_allowed=provider_allowed,
                fixed_boundaries=_fixed_boundaries() if fixed else [],
            )
        )
    return out


def _fixed_boundaries() -> list[RoleBoundaryInput]:
    return [
        RoleBoundaryInput(kind=boundary.kind.value, organization_id=boundary.organization_id)
        for boundary in sorted(
            SECRETS_READER_BOUNDARIES, key=lambda b: (b.kind.value, str(b.organization_id))
        )
    ]


def _may_change(caller: Caller, role: RoleInfo, target_permissions: frozenset[str]) -> bool:
    try:
        check_role_change(caller, role, target_permissions)
    except RoleAssignmentError:
        return False
    return True


async def _response(
    session: AsyncSession, caller: Caller, target: AssignmentTarget
) -> UserRoleAssignmentsResponse:
    assignments = await _current_assignments(session, target.user.id)
    roles = await _all_roles(session)
    organization_ids = {
        boundary.organization_id
        for boundaries in assignments.values()
        for boundary in boundaries
        if boundary.organization_id is not None
    }
    names: dict[UUID, str] = {}
    if organization_ids:
        rows = (
            await session.execute(
                select(Organization.id, Organization.name).where(
                    Organization.id.in_(organization_ids)
                )
            )
        ).all()
        names = {org_id: name for org_id, name in rows}
    base = roles[target.user.base_role_id]
    additional = [
        AssignedRole(
            role_id=role_id,
            name=roles[role_id].name,
            description=roles[role_id].description,
            is_builtin=roles[role_id].is_builtin,
            permissions=sorted(roles[role_id].permissions),
            boundaries=[
                RoleBoundaryPublic(
                    kind=boundary.kind.value,
                    organization_id=boundary.organization_id,
                    organization_name=names.get(boundary.organization_id)
                    if boundary.organization_id
                    else None,
                )
                for boundary in sorted(
                    boundaries, key=lambda b: (b.kind.value, str(b.organization_id))
                )
            ],
        )
        for role_id, boundaries in sorted(
            assignments.items(), key=lambda item: roles[item[0]].name.lower()
        )
    ]
    return UserRoleAssignmentsResponse(
        base_role=RoleSummary(
            id=base.id, name=base.name, description=base.description, is_builtin=base.is_builtin
        ),
        additional=additional,
        is_protected=target.is_privileged,
        assignable_roles=_assignable_roles(caller, target, roles, frozenset(assignments)),
    )


async def require_assignment_target(
    session: AsyncSession, caller: Caller, user_id: UUID, operation: str
) -> AssignmentTarget:
    """The user ``operation`` is about, once ``caller`` may perform it at
    their organization (404 when they do not exist)."""
    # Reach anywhere first: a caller with none learns nothing about which
    # users exist.
    operation_reach(caller, operation)
    target = await _load_target(session, user_id)
    if target is None:
        raise RoleAssignmentError(404, "User not found")
    require_operation(caller, operation, org_target(target.user.organization_id))
    return target


async def get_role_assignments(
    session: AsyncSession, caller: Caller, *, user_id: UUID
) -> UserRoleAssignmentsResponse:
    target = await require_assignment_target(session, caller, user_id, GET_OPERATION)
    return await _response(session, caller, target)


def _requested_boundaries(
    boundaries, home_organization_id: UUID | None, role_id: UUID
) -> frozenset[Boundary]:
    if boundaries is None:
        if role_id == PLATFORM_ADMIN_ROLE_ID:
            return frozenset({Boundary(BoundaryKind.PLATFORM)})
        if role_id == DECRYPTION_ROLE_ID:
            return SECRETS_READER_BOUNDARIES
        return default_boundaries(home_organization_id)
    return frozenset(
        Boundary(BoundaryKind(item.kind), item.organization_id) for item in boundaries
    )


async def replace_role_assignments(
    session: AsyncSession,
    caller: Caller,
    *,
    user_id: UUID,
    request: UserRoleAssignmentsUpdate,
) -> UserRoleAssignmentsResponse:
    """Replace a user's base role and additional roles atomically.

    Only what changes is checked against the ceiling: a delegate can save a
    set that still contains an assignment they could not have made, as long
    as they leave it as it is.
    """
    from shared.role_cache import invalidate_user as invalidate_user_role_cache
    from shared.sdk_users import set_platform_admin, set_user_base_role
    from src.core.cache import invalidate_role_users
    from src.services.audit import emit_audit

    target = await require_assignment_target(session, caller, user_id, PUT_OPERATION)
    user = target.user
    if is_system_account(user.id):
        raise RoleAssignmentError(422, SYSTEM_ACCOUNT_ROLE_MESSAGE)
    if user.id == caller.principal.user_id:
        raise RoleAssignmentError(400, "You can't change your own role assignments")
    require_unprotected(caller, target.is_privileged)

    requested: dict[UUID, frozenset[Boundary]] = {}
    for item in request.additional:
        if item.role_id in requested:
            raise RoleAssignmentError(422, f"Role {item.role_id} is listed twice")
        requested[item.role_id] = _requested_boundaries(
            item.boundaries, user.organization_id, item.role_id
        )
    current = await _current_assignments(session, user.id)
    roles = await load_roles(
        session, {request.base_role_id, user.base_role_id, *requested, *current}
    )
    missing = ({request.base_role_id} | set(requested)) - set(roles)
    if missing:
        raise RoleAssignmentError(422, f"Role {sorted(missing)[0]} not found")

    base_changed = request.base_role_id != user.base_role_id
    if base_changed:
        _check_base_change(caller, target, roles[user.base_role_id], roles[request.base_role_id])
    if request.base_role_id in requested:
        raise RoleAssignmentError(422, "The base role can't also be an additional role")

    reach = permitted_organizations(caller, "roleassignments.readwrite")
    added = set(requested) - set(current)
    removed = set(current) - set(requested)
    rebounded = {
        role_id for role_id in set(requested) & set(current) if requested[role_id] != current[role_id]
    }
    for role_id in added | rebounded:
        if role_id in BASE_ROLE_IDS:
            raise RoleAssignmentError(422, f"'{roles[role_id].name}' is a base role")
    if PLATFORM_ADMIN_ROLE_ID in added | removed:
        if not caller.is_platform_admin:
            raise RoleAssignmentError(403, ADMIN_ROLE_MESSAGE)
        if PLATFORM_ADMIN_ROLE_ID in removed and user.organization_id is None:
            raise RoleAssignmentError(409, ADMIN_REMOVAL_MESSAGE)
    for role_id in added | removed | rebounded:
        check_role_change(caller, roles[role_id], target.held)
    for role_id in added | rebounded:
        check_holder(roles[role_id], user.organization_id)
        check_boundaries(caller, reach, roles[role_id], requested[role_id])
    await _require_organizations_exist(
        session,
        {
            boundary.organization_id
            for role_id in added | rebounded
            for boundary in requested[role_id]
            if boundary.organization_id is not None
        },
    )

    before = _audit_view(user.base_role_id, current)
    if base_changed:
        await set_user_base_role(session, user, request.base_role_id)
    if removed - {PLATFORM_ADMIN_ROLE_ID}:
        await session.execute(
            delete(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.role_id.in_(removed - {PLATFORM_ADMIN_ROLE_ID}),
            )
        )
    for role_id in rebounded:
        await session.execute(
            delete(UserRoleBoundary).where(
                UserRoleBoundary.user_id == user.id, UserRoleBoundary.role_id == role_id
            )
        )
        _add_boundaries(session, user.id, role_id, requested[role_id])
    await session.flush()
    if PLATFORM_ADMIN_ROLE_ID in added | removed:
        await set_platform_admin(
            session,
            user,
            PLATFORM_ADMIN_ROLE_ID in added,
            assigned_by=caller.principal.email,
        )
    for role_id in added - {PLATFORM_ADMIN_ROLE_ID}:
        await insert_assignment(
            session,
            user_id=user.id,
            role_id=role_id,
            boundaries=requested[role_id],
            assigned_by=caller.principal.email,
        )
    user.updated_at = datetime.now(timezone.utc)
    await session.flush()

    for role_id in added | removed | rebounded:
        await invalidate_role_users(None, str(role_id))
    await invalidate_user_role_cache(user.id)
    await emit_audit(
        session,
        "user.role_assignments_updated",
        resource_type="user",
        resource_id=user.id,
        details={
            "before": before,
            "after": _audit_view(request.base_role_id, requested),
        },
    )
    refreshed = await _load_target(session, user.id)
    assert refreshed is not None
    return await _response(session, caller, refreshed)


def _check_base_change(caller: Caller, target: AssignmentTarget, old: RoleInfo, new: RoleInfo) -> None:
    org = org_target(target.user.organization_id)
    require_operation(caller, PUT_OPERATION, org, permission="users.lifecycle.readwrite")
    if new.is_builtin and new.id not in BASE_ROLE_IDS:
        raise RoleAssignmentError(422, f"'{new.name}' can't be a base role")
    check_role_change(caller, old, target.held)
    check_role_change(caller, new, target.held)


async def _require_organizations_exist(session: AsyncSession, organization_ids: set[UUID]) -> None:
    if not organization_ids:
        return
    found = set(
        (
            await session.execute(
                select(Organization.id).where(Organization.id.in_(organization_ids))
            )
        ).scalars()
    )
    missing = organization_ids - found
    if missing:
        raise RoleAssignmentError(422, f"Organization {sorted(missing)[0]} not found")


def _audit_view(base_role_id: UUID, assignments: dict[UUID, frozenset[Boundary]]) -> dict:
    return {
        "base_role_id": str(base_role_id),
        "additional": {
            str(role_id): sorted(
                f"{b.kind.value}:{b.organization_id}" if b.organization_id else b.kind.value
                for b in boundaries
            )
            for role_id, boundaries in sorted(assignments.items(), key=lambda item: str(item[0]))
        },
    }


async def authorization_summary(session: AsyncSession, user_id: UUID) -> AuthorizationSummary:
    """The signed-in user's own authorization, read from the database."""
    ctx = await build_authorization_context(session, user_id)
    base_name = await session.scalar(select(Role.name).where(Role.id == ctx.base_role_id))
    grants = [
        AuthorizationGrant(
            permission=permission,
            boundary=AuthorizationBoundary(kind="home", organization_id=ctx.home_organization_id),
        )
        for permission in sorted(ctx.base_permissions)
    ]
    for grant in ctx.role_grants:
        for boundary in grant.boundaries:
            grants.extend(
                AuthorizationGrant(
                    permission=permission,
                    boundary=AuthorizationBoundary(
                        kind=boundary.kind.value,
                        organization_id=boundary.organization_id,
                    ),
                )
                for permission in sorted(grant.permissions)
            )
    return AuthorizationSummary(
        is_platform_admin=ctx.is_platform_admin,
        home_organization_id=ctx.home_organization_id,
        provider_organization_id=PROVIDER_ORG_ID,
        base_role=AuthorizationBaseRole(id=ctx.base_role_id, name=base_name or ""),
        grants=grants,
    )
