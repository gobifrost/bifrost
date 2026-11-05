"""SSO recovery invariant, serialized with the shared platform-admin role lock."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.models.contracts.oauth_config import OAuthLoginPreference

RECOVERY_ADMIN_REQUIRED = (
    "At least one active platform admin must remain excluded from SSO enforcement"
)


async def lock_login_policy(db: AsyncSession) -> None:
    from shared.account_guards import _lock_roles

    await _lock_roles(db, {PLATFORM_ADMIN_ROLE_ID})


async def validate_recovery_admin(
    db: AsyncSession,
    preference: OAuthLoginPreference,
    removing: set[UUID] | None = None,
) -> None:
    from src.models.orm.users import User, UserRole
    from src.services.oauth_config_service import OAuthLoginPreferenceError

    await lock_login_policy(db)
    if preference.excluded_user_ids is None:
        return  # Safe default excludes every platform admin.
    result = await db.execute(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .where(
            UserRole.role_id == PLATFORM_ADMIN_ROLE_ID,
            User.is_active.is_(True),
            User.is_system.is_(False),
            User.identity_kind.is_(None),
        )
    )
    active = set(result.scalars()) - (removing or set())
    if not active.intersection(preference.excluded_user_ids):
        raise OAuthLoginPreferenceError(RECOVERY_ADMIN_REQUIRED)
