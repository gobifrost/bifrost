"""Get/set a role's permission set, with vocabulary validation.

Builtin roles (Platform Admin, User, Platform Operator — see
``shared.builtin_roles``) cannot be modified through this service: their
permission sets are fixed by migration/seed data until R3a's UI ships a
supported way to change them. Platform Admin never has ``role_permissions``
rows; its access is the wildcard permission represented in code.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import BUILTIN_ROLE_IDS, PLATFORM_ADMIN_ROLE_ID, WILDCARD_PERMISSION
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
    """The role's permission set. Platform Admin returns the wildcard."""
    if role_id == PLATFORM_ADMIN_ROLE_ID:
        return frozenset({WILDCARD_PERMISSION})

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
    """Whether any of `role_ids` grants `permission` (or holds the Platform
    Admin wildcard)."""
    if PLATFORM_ADMIN_ROLE_ID in role_ids:
        return True
    if not role_ids:
        return False

    from src.models import RolePermission as RolePermissionORM

    result = await session.execute(
        select(RolePermissionORM.role_id)
        .where(
            RolePermissionORM.role_id.in_(role_ids),
            RolePermissionORM.permission == permission,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None
