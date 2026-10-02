"""
User Repository

Data access for user entities.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import User
from src.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):  # type: ignore[type-var]
    """
    Repository for User entities.
    """

    model = User

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    async def get_by_email(self, email: str) -> User | None:
        """
        Get user by email address.

        Args:
            email: User email (case-insensitive)

        Returns:
            User or None if not found
        """
        result = await self.session.execute(
            select(User).where(User.email.ilike(email))
        )
        return result.scalar_one_or_none()

    async def get_by_organization(
        self,
        organization_id: UUID,
        limit: int = 100,
        offset: int = 0
    ) -> list[User]:
        """
        Get all users in an organization.

        Args:
            organization_id: Organization UUID
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of users
        """
        result = await self.session.execute(
            select(User)
            .where(User.organization_id == organization_id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_superusers(self) -> list[User]:
        """
        Get all superusers (platform admins).

        Returns:
            List of superuser accounts
        """
        result = await self.session.execute(
            select(User).where(User.is_superuser == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def has_any_users(self) -> bool:
        """
        Check if any real users exist in the system.

        Used for first-user detection during auto-provisioning.
        Excludes:
        - System user (SYSTEM_USER_UUID) - automated execution account
        - Users with NULL organization_id - system/global accounts

        Returns:
            True if at least one real human user exists, False otherwise
        """
        from sqlalchemy import exists

        from src.core.constants import SYSTEM_USER_UUID

        result = await self.session.execute(
            select(exists().where(
                User.id.isnot(None),
                User.id != SYSTEM_USER_UUID,
                User.organization_id.isnot(None),
            ))
        )
        return result.scalar() or False

    async def create_user(
        self,
        email: str,
        hashed_password: str | None = None,
        name: str | None = None,
        is_superuser: bool = False,
        organization_id: UUID | None = None,
    ) -> User:
        """
        Create a new user.

        The user model uses is_superuser + nullable organization_id as source of truth:
        - is_superuser=True, organization_id=UUID: Platform admin in an org
        - is_superuser=False, organization_id=UUID: Regular org user
        - is_superuser=True, organization_id=None: System account (global scope)

        Args:
            email: User email
            hashed_password: Pre-hashed password (None for OAuth users)
            name: Display name
            is_superuser: Whether user is platform admin
            organization_id: Organization membership (None for system accounts)

        Returns:
            Created user
        """
        from shared.builtin_roles import USER_ROLE_ID
        from shared.sdk_users import set_platform_admin, set_user_base_role

        user = User(
            email=email,
            hashed_password=hashed_password,
            name=name or email.split("@")[0],
            is_active=True,
            is_verified=False,
            organization_id=organization_id,
        )
        await set_user_base_role(self.session, user, USER_ROLE_ID)
        created = await self.create(user)
        await set_platform_admin(self.session, created, is_superuser, assigned_by="system")
        return created
