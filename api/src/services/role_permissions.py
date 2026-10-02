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

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import BUILTIN_ROLE_IDS, WILDCARD_PERMISSION
from src.models.contracts.permissions import parse_permission


class RolePermissionError(Exception):
    """Permission-set operation failure with an HTTP-style status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_permission(permission: str) -> None:
    """Raise ``RolePermissionError(422, ...)`` unless `permission` is a
    well-formed ``<domain>.<read|readwrite|execute>[.all]`` string in the
    closed ``PERMISSION_DOMAINS`` vocabulary (see ``parse_permission``)."""
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
    {"users", "users.lifecycle", "organizations", "roleassignments", "roles"}
)


def identity_permissions() -> tuple[str, ...]:
    """The identity permissions an editor offers: every permission the
    identity routes are decided by (their access-list entries and the
    narrower per-field permissions), sorted."""
    from src.services.access_list import ACCESS_LIST
    from src.services.authorization.enforce import NARROWER_PERMISSIONS

    found = {entry.permission for entry in ACCESS_LIST if entry.permission}
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
    session: AsyncSession, *, role_id: UUID, permissions: list[str]
):
    """Replace the role's identity permissions, keeping every other
    permission it holds. 422 for anything outside the identity set, 409 for
    a builtin role, 404 for a missing one."""
    from src.models import Role as RoleORM
    from src.services.audit import emit_audit

    role = await session.get(RoleORM, role_id)
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
    before = sorted(held)
    await set_role_permissions(session, role_id=role_id, permissions=frozenset(kept | set(permissions)))
    await emit_audit(
        session,
        "role.permissions_updated",
        resource_type="role",
        resource_id=role_id,
        details={"before": before, "after": sorted(kept | set(permissions))},
    )
    return await describe_role_permissions(session, role_id=role_id)
