"""
User Auto-Provisioning Service

Handles automatic user creation and organization assignment for FastAPI.
Adapted from shared/user_provisioning.py for PostgreSQL repositories.

Key Features:
- First user becomes a superuser automatically (is_superuser=True)
- Subsequent users auto-join by email domain matching
- Idempotent - safe to call multiple times
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.core.log_safety import log_safe
from src.models import User
from src.repositories.organizations import OrganizationRepository
from src.repositories.users import UserRepository

logger = logging.getLogger(__name__)


@dataclass
class ProvisioningResult:
    """Result of user provisioning attempt.

    User type is now derived from is_superuser + organization_id:
    - is_superuser=True, org_id=UUID: Platform admin in an org
    - is_superuser=False, org_id=UUID: Regular org user
    - is_superuser=True, org_id=None: System account (global scope)
    """

    user: User
    is_platform_admin: bool
    organization_id: UUID | None
    was_created: bool

    @property
    def roles(self) -> list[str]:
        """Get roles for this user (for JWT claims)."""
        return ["authenticated"]


async def ensure_user_provisioned(
    db: AsyncSession,
    email: str,
    name: str | None = None,
) -> ProvisioningResult:
    """
    Ensure user exists in the system, creating if necessary.

    This function is idempotent and safe to call on every login.

    Auto-Provisioning Rules:
    1. First user in system -> superuser (is_superuser=True)
    2. Subsequent users -> Match email domain to organization
    3. No domain match -> Raise error (user must be manually added)

    Args:
        db: Database session
        email: User's email address
        name: Optional display name

    Returns:
        ProvisioningResult with user info, type, admin status, and org_id

    Raises:
        ValueError: If email is invalid format or no matching org found
    """
    if not email or "@" not in email:
        raise ValueError(f"Invalid email format: {email}")

    email = email.lower()
    logger.info(f"Processing user provisioning for {log_safe(email)}")

    user_repo = UserRepository(db)
    org_repo = OrganizationRepository(db)

    # Check if user already exists
    user = await user_repo.get_by_email(email)

    if user:
        logger.info(f"Found existing user: {log_safe(email)}")
        return ProvisioningResult(
            user=user,
            is_platform_admin=user.is_superuser,
            organization_id=user.organization_id,
            was_created=False,
        )

    # User doesn't exist - check if first user
    logger.info(f"User {log_safe(email)} not found, checking provisioning rules")

    has_users = await user_repo.has_any_users()
    is_first_user = not has_users

    if is_first_user:
        # First user in system - create as superuser
        logger.info(f"First user login detected! Auto-promoting {log_safe(email)} to superuser")

        user = await user_repo.create_user(
            email=email,
            name=name or email.split("@")[0],
            is_superuser=True,
            organization_id=PROVIDER_ORG_ID,
        )
        await db.commit()
        await db.refresh(user)

        logger.info(f"Successfully created first user as superuser: {log_safe(email)}")

        return ProvisioningResult(
            user=user,
            is_platform_admin=True,
            organization_id=user.organization_id,
            was_created=True,
        )

    # Not first user - try domain-based auto-provisioning
    logger.info(f"Attempting domain-based auto-provisioning for {log_safe(email)}")

    # Extract domain from email
    user_domain = email.split("@")[1].lower()
    logger.info(f"Looking for organization with domain: {log_safe(user_domain)}")

    # Query organizations with matching domain
    matched_org = await org_repo.get_by_domain(user_domain)

    if not matched_org:
        logger.warning(f"No organization found with domain: {log_safe(user_domain)}")
        raise ValueError(
            f"No organization configured for domain: {user_domain}. "
            f"Contact your administrator to be added manually."
        )

    logger.info(f"Found matching organization: {log_safe(matched_org.name)} with domain {log_safe(matched_org.domain)}")

    # Create new ORG user
    user = await user_repo.create_user(
        email=email,
        name=name or email.split("@")[0],
        is_superuser=False,
        organization_id=matched_org.id,
    )
    await db.commit()
    await db.refresh(user)

    logger.info(f"Auto-created ORG user: {log_safe(email)} for org {matched_org.id}")

    return ProvisioningResult(
        user=user,
        is_platform_admin=False,
        organization_id=matched_org.id,
        was_created=True,
    )


async def get_user_roles(
    db: AsyncSession,
    user_id: UUID,
) -> list[str]:
    """
    Get all role names for a user.

    Args:
        db: Database session
        user_id: User UUID

    Returns:
        List of role names
    """
    from sqlalchemy import select
    from src.models import Role, UserRole

    result = await db.execute(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return list(result.scalars().all())
