"""Account invariants shared by every account and role mutation path."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.services.authorization.privilege import is_privileged_principal


LAST_ACTIVE_PLATFORM_ADMIN_MESSAGE = "At least one active person must hold the Platform Admin role"
EXTERNAL_PRIVILEGED_ROLE_MESSAGE = "External users cannot hold privileged roles"


class AccountGuardError(Exception):
    """An account invariant would be violated by a requested mutation."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.status_code = 409
        self.detail = detail


async def refuse_last_active_platform_admin_removal(
    session: AsyncSession, removing_user_ids: Iterable[UUID]
) -> None:
    """Refuse removing the final active human Platform Admin.

    Locking the stable Platform Admin role serializes the operations before a
    fresh query observes active assignments. Identities and service accounts
    have ``identity_kind`` set and therefore never count toward this invariant.
    """
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM

    removing = set(removing_user_ids)
    if not removing:
        return
    await _lock_roles(session, {PLATFORM_ADMIN_ROLE_ID})
    rows = await session.execute(
        select(UserRoleORM.user_id)
        .join(UserORM, UserORM.id == UserRoleORM.user_id)
        .where(
            UserRoleORM.role_id == PLATFORM_ADMIN_ROLE_ID,
            UserORM.is_active.is_(True),
            UserORM.is_system.is_(False),
            UserORM.identity_kind.is_(None),
        )
    )
    active_people = set(rows.scalars())
    if active_people & removing and active_people <= removing:
        raise AccountGuardError(LAST_ACTIVE_PLATFORM_ADMIN_MESSAGE)


async def assert_user_can_be_external(session: AsyncSession, user_id: UUID) -> None:
    """Refuse an external transition when the person already holds privilege.

    Taking the user lock first matches role assignment writes. Role locks then
    serialize this check against permission-set edits on every held role.
    """
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM

    base_role_id = await session.scalar(
        select(UserORM.base_role_id).where(UserORM.id == user_id).with_for_update()
    )
    if base_role_id is None:
        return
    role_ids = {
        base_role_id,
        *(
            await session.execute(
                select(UserRoleORM.role_id).where(UserRoleORM.user_id == user_id)
            )
        ).scalars(),
    }
    await _lock_roles(session, role_ids)
    if await _roles_are_privileged(session, role_ids):
        raise AccountGuardError(EXTERNAL_PRIVILEGED_ROLE_MESSAGE)


async def assert_role_assignment_allowed_for_user(
    session: AsyncSession, *, user_id: UUID, role_id: UUID
) -> None:
    """Refuse granting a privileged role to an external user.

    Holding the user row lock prevents a concurrent external transition from
    observing a stale non-external account. The role lock serializes this
    decision against a concurrent permission-set elevation.
    """
    from src.models import User as UserORM

    is_external = await session.scalar(
        select(UserORM.is_external).where(UserORM.id == user_id).with_for_update()
    )
    if not is_external:
        return
    await _lock_roles(session, {role_id})
    if await _roles_are_privileged(session, {role_id}):
        raise AccountGuardError(EXTERNAL_PRIVILEGED_ROLE_MESSAGE)


async def assert_role_permissions_allowed(
    session: AsyncSession, *, role_id: UUID, permissions: frozenset[str]
) -> None:
    """Refuse adding privileged permissions to a role external users hold.

    External transitions and assignments take this same role lock before
    writing. Query holders only after acquiring it, so a mutation that commits
    while we wait is included in the decision. No user locks are needed here.
    """
    await _lock_roles(session, {role_id})
    external_holders = await _external_role_holders(session, role_id)
    current = await _role_permissions(session, {role_id})
    added = permissions - current
    if not is_privileged_principal(added):
        return
    if external_holders:
        raise AccountGuardError(EXTERNAL_PRIVILEGED_ROLE_MESSAGE)


async def _lock_roles(session: AsyncSession, role_ids: Iterable[UUID]) -> None:
    from src.models import Role as RoleORM

    ids = sorted(set(role_ids), key=str)
    if ids:
        await session.execute(
            select(RoleORM.id)
            .where(RoleORM.id.in_(ids))
            .order_by(RoleORM.id)
            .with_for_update()
        )


async def _external_role_holders(session: AsyncSession, role_id: UUID) -> set[UUID]:
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM

    rows = await session.execute(
        select(UserORM.id)
        .outerjoin(
            UserRoleORM,
            (UserRoleORM.user_id == UserORM.id) & (UserRoleORM.role_id == role_id),
        )
        .where(
            UserORM.is_external.is_(True),
            or_(UserORM.base_role_id == role_id, UserRoleORM.role_id == role_id),
        )
    )
    return set(rows.scalars())


async def _role_permissions(session: AsyncSession, role_ids: Iterable[UUID]) -> frozenset[str]:
    from src.models import RolePermission as RolePermissionORM

    ids = set(role_ids)
    if not ids:
        return frozenset()
    rows = await session.execute(
        select(RolePermissionORM.permission).where(RolePermissionORM.role_id.in_(ids))
    )
    return frozenset(rows.scalars())


async def _roles_are_privileged(session: AsyncSession, role_ids: Iterable[UUID]) -> bool:
    return is_privileged_principal(await _role_permissions(session, role_ids))
