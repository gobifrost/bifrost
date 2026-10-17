"""Get/set a role's permission set, with vocabulary validation.

Builtin roles (Platform Admin, User, Platform Operator, Secrets Reader —
see ``shared.builtin_roles``) cannot be modified through this service: their
permission sets are fixed by migration/seed data. Platform Admin's access is
the wildcard permission, stored as its one ``role_permissions`` row like any
other role's permissions; no custom role can be given it. Custom roles' identity permissions are edited through
``replace_identity_permissions``; their other permissions are not editable
from the API yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import BUILTIN_ROLE_IDS, WILDCARD_PERMISSION
from src.models.contracts.permissions import parse_permission

if TYPE_CHECKING:
    from src.services.authorization.enforce import Caller


class RolePermissionError(Exception):
    """Permission-set operation failure with an HTTP-style status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_permission(permission: str) -> None:
    """Raise ``RolePermissionError(422, ...)`` unless `permission` is a
    well-formed ``<resource>.<action>[.all]`` string in the closed
    ``PERMISSION_DOMAINS`` vocabulary (see ``parse_permission``)."""
    try:
        parse_permission(permission)
    except ValueError as exc:
        raise RolePermissionError(422, str(exc)) from exc


async def get_role_permissions(session: AsyncSession, *, role_id: UUID) -> frozenset[str]:
    """The role's permission set."""
    from src.models import RolePermission as RolePermissionORM

    result = await session.execute(
        select(RolePermissionORM.permission).where(RolePermissionORM.role_id == role_id)
    )
    return frozenset(result.scalars().all())


async def set_role_permissions(
    session: AsyncSession, *, role_id: UUID, permissions: frozenset[str]
) -> None:
    """Replace a role's permission set wholesale. Refuses builtin roles (409)."""
    if role_id in BUILTIN_ROLE_IDS:
        raise RolePermissionError(409, "Builtin roles cannot be modified")
    for permission in permissions:
        validate_permission(permission)

    from shared.account_guards import AccountGuardError, assert_role_permissions_allowed

    try:
        await assert_role_permissions_allowed(session, role_id=role_id, permissions=permissions)
    except AccountGuardError as exc:
        raise RolePermissionError(exc.status_code, exc.detail) from None

    from src.models import RolePermission as RolePermissionORM

    await session.execute(delete(RolePermissionORM).where(RolePermissionORM.role_id == role_id))
    for permission in permissions:
        session.add(RolePermissionORM(role_id=role_id, permission=permission))
    await session.flush()


async def role_has_permission(
    session: AsyncSession, *, role_ids: list[UUID], permission: str
) -> bool:
    """Whether any of `role_ids` grants `permission`: it holds the permission,
    or the wildcard, which satisfies everything but
    ``WILDCARD_EXCLUDED_PERMISSIONS``."""
    if not role_ids:
        return False

    from src.models import RolePermission as RolePermissionORM
    from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS

    satisfying = {permission}
    if permission not in WILDCARD_EXCLUDED_PERMISSIONS:
        satisfying.add(WILDCARD_PERMISSION)
    result = await session.execute(
        select(RolePermissionORM.role_id)
        .where(
            RolePermissionORM.role_id.in_(role_ids),
            RolePermissionORM.permission.in_(satisfying),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# The permission domains a role's permission set can be edited for through
# ``PUT /api/roles/{role_id}/permissions`` (R3a). Other domains a role holds
# (for example ``agents.readwrite``, which agent promotion reads) are kept
# as they are.
IDENTITY_PERMISSION_DOMAINS = frozenset(
    {"users", "userlifecycle", "organizations", "roleassignments", "roles"}
)


def identity_permissions() -> tuple[str, ...]:
    """The identity permissions an editor offers, sorted: every permission
    the permission catalog lists for an identity domain (what the routes are
    decided by, and the privileged ones no route names, such as Impersonate
    Users) and the narrower per-field permissions."""
    from src.services.authorization.enforce import NARROWER_PERMISSIONS
    from src.services.permission_catalog import build_catalog

    found = {f"{entry.domain}.{action}" for entry in build_catalog() for action in entry.actions}
    found |= {p for permissions in NARROWER_PERMISSIONS.values() for p in permissions}
    return tuple(
        sorted(p for p in found if parse_permission(p).domain in IDENTITY_PERMISSION_DOMAINS)
    )


def _item(permission: str, *, editable: bool):
    from src.models.contracts.permissions import PRIVILEGED_PERMISSIONS
    from src.models.contracts.role_assignments import RolePermissionItem

    return RolePermissionItem(
        permission=permission,
        editable=editable,
        privileged=permission == WILDCARD_PERMISSION or permission in PRIVILEGED_PERMISSIONS,
    )


async def describe_role_permissions(session: AsyncSession, *, role_id: UUID):
    """Every permission the role holds, and the identity permissions an
    editor may choose from. Builtin roles are listed read-only."""
    from src.models import Role as RoleORM
    from src.models.contracts.role_assignments import RolePermissionsResponse

    role = await session.get(RoleORM, role_id)
    if role is None:
        raise RolePermissionError(404, "Role not found")
    editable_vocabulary = set(identity_permissions()) if not role.is_builtin else set()
    held = await get_role_permissions(session, role_id=role_id)
    return RolePermissionsResponse(
        role_id=role_id,
        is_builtin=role.is_builtin,
        permissions=[_item(p, editable=p in editable_vocabulary) for p in sorted(held)],
        identity_permissions=[
            _item(p, editable=not role.is_builtin) for p in identity_permissions()
        ],
    )


async def replace_identity_permissions(
    session: AsyncSession, *, caller: Caller, role_id: UUID, permissions: list[str]
):
    """Replace the role's identity permissions, keeping every other
    permission it holds. 422 for anything outside the identity set, 409 for
    a builtin role, 404 for a missing one."""
    from src.models import Role as RoleORM
    from src.services.audit import emit_audit
    from src.services.authorization.enforce import require_permission
    from src.services.authorization.privilege import is_privileged_principal

    # Serialize this decision with permission writes before reading the current
    # set. ``set_role_permissions`` applies its holder invariant to the actual
    # resulting permissions under the same row lock.
    role = (
        await session.execute(
            select(RoleORM).where(RoleORM.id == role_id).with_for_update()
        )
    ).scalar_one_or_none()
    if role is None:
        raise RolePermissionError(404, "Role not found")
    if role_id in BUILTIN_ROLE_IDS or role.is_builtin:
        raise RolePermissionError(409, "Builtin roles cannot be modified")
    vocabulary = set(identity_permissions())
    for permission in permissions:
        validate_permission(permission)
        if permission not in vocabulary:
            raise RolePermissionError(
                422, f"{permission!r} is not an identity permission editable here"
            )
    held = await get_role_permissions(session, role_id=role_id)
    kept = {p for p in held if parse_permission(p).domain not in IDENTITY_PERMISSION_DOMAINS}
    resulting = frozenset(kept | set(permissions))
    if is_privileged_principal(held) or is_privileged_principal(resulting):
        try:
            require_permission(caller, "privilegedaccess.readwrite", None)
        except HTTPException as exc:
            raise RolePermissionError(
                exc.status_code, "Global privileged access is required to edit a privileged role"
            ) from None
    before = sorted(held)
    await set_role_permissions(session, role_id=role_id, permissions=resulting)
    await emit_audit(
        session,
        "role.permissions_updated",
        resource_type="role",
        resource_id=role_id,
        details={"before": before, "after": sorted(resulting)},
    )
    return await describe_role_permissions(session, role_id=role_id)
