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
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    ORGANIZATION_MEMBER_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
    PROVIDER_ORG_ID,
)
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
        changed = await sync_platform_admin_role(
            db,
            user_id=user.id,
            enabled=user.is_superuser and not user.is_system,
        )
        changed = (
            await sync_organization_member_role(
                db,
                user_id=user.id,
                organization_id=user.organization_id,
                is_system=user.is_system,
            )
            or changed
        )
        changed = (
            await ensure_platform_operator_role(
                db,
                user_id=user.id,
                organization_id=user.organization_id,
                is_superuser=user.is_superuser,
                is_system=user.is_system,
            )
            or changed
        )
        if changed:
            await db.commit()
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
        logger.info(
            f"First user login detected! Auto-promoting {log_safe(email)} to superuser"
        )

        user = await user_repo.create_user(
            email=email,
            name=name or email.split("@")[0],
            is_superuser=True,
            organization_id=PROVIDER_ORG_ID,
        )
        await sync_platform_admin_role(
            db,
            user_id=user.id,
            enabled=True,
        )
        await sync_organization_member_role(
            db,
            user_id=user.id,
            organization_id=user.organization_id,
            is_system=user.is_system,
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

    logger.info(
        f"Found matching organization: {log_safe(matched_org.name)} with domain {log_safe(matched_org.domain)}"
    )

    # Create new ORG user
    user = await user_repo.create_user(
        email=email,
        name=name or email.split("@")[0],
        is_superuser=False,
        organization_id=matched_org.id,
    )
    await sync_organization_member_role(
        db,
        user_id=user.id,
        organization_id=user.organization_id,
        is_system=user.is_system,
    )
    await ensure_platform_operator_role(
        db,
        user_id=user.id,
        organization_id=user.organization_id,
        is_superuser=user.is_superuser,
        is_system=user.is_system,
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


async def sync_platform_admin_role(
    db: AsyncSession,
    *,
    user_id: UUID,
    enabled: bool,
) -> bool:
    """Keep the legacy administrator bit and built-in role in sync."""

    from sqlalchemy import delete, select

    from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary

    assignment = (
        await db.execute(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == PLATFORM_ADMIN_ROLE_ID,
            )
        )
    ).scalar_one_or_none()
    if enabled:
        if assignment is not None:
            return False
        db.add(
            RoleAssignment(
                user_id=user_id,
                role_id=PLATFORM_ADMIN_ROLE_ID,
                boundaries=[RoleAssignmentBoundary(boundary_kind="platform")],
            )
        )
        return True
    if assignment is None:
        return False
    await db.execute(
        delete(RoleAssignment).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.role_id == PLATFORM_ADMIN_ROLE_ID,
        )
    )
    return True


async def ensure_platform_operator_role(
    db: AsyncSession,
    *,
    user_id: UUID,
    organization_id: UUID | None,
    is_superuser: bool,
    is_system: bool,
) -> bool:
    """Give non-admin provider staff the sticky support Role once.

    This preserves the access provider-organization membership historically
    supplied while authorization moves to explicit Roles. The assignment is
    intentionally not removed by a later organization move; after migration,
    Role administration—not a legacy membership side effect—is authoritative.
    """

    from sqlalchemy import select

    from src.models.orm.organizations import Organization
    from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary

    if is_superuser or is_system or organization_id is None:
        return False
    assignment = (
        await db.execute(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == PLATFORM_OPERATOR_ROLE_ID,
            )
        )
    ).scalar_one_or_none()
    if assignment is not None:
        return False
    is_provider = await db.scalar(
        select(Organization.is_provider).where(Organization.id == organization_id)
    )
    if is_provider is not True:
        return False
    db.add(
        RoleAssignment(
            user_id=user_id,
            role_id=PLATFORM_OPERATOR_ROLE_ID,
            boundaries=[RoleAssignmentBoundary(boundary_kind="managed_organizations")],
        )
    )
    return True


async def sync_organization_member_role(
    db: AsyncSession,
    *,
    user_id: UUID,
    organization_id: UUID | None,
    is_system: bool,
) -> bool:
    """Ensure every human organization member has the baseline Role there."""

    from sqlalchemy import delete, select
    from sqlalchemy.orm import selectinload

    from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary

    assignment = (
        await db.execute(
            select(RoleAssignment)
            .options(selectinload(RoleAssignment.boundaries))
            .where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == ORGANIZATION_MEMBER_ROLE_ID,
            )
        )
    ).scalar_one_or_none()
    if is_system or organization_id is None:
        if assignment is None:
            return False
        await db.execute(
            delete(RoleAssignment).where(RoleAssignment.id == assignment.id)
        )
        return True

    if assignment is None:
        db.add(
            RoleAssignment(
                user_id=user_id,
                role_id=ORGANIZATION_MEMBER_ROLE_ID,
                boundaries=[
                    RoleAssignmentBoundary(
                        boundary_kind="organization",
                        organization_id=organization_id,
                    )
                ],
            )
        )
        return True

    current = [
        boundary
        for boundary in assignment.boundaries
        if boundary.boundary_kind == "organization"
        and boundary.organization_id == organization_id
    ]
    if len(current) == 1 and len(assignment.boundaries) == 1:
        return False
    assignment.boundaries = [
        RoleAssignmentBoundary(
            boundary_kind="organization",
            organization_id=organization_id,
        )
    ]
    return True


async def validate_platform_admin_removal(
    db: AsyncSession,
    *,
    user_ids: Iterable[UUID],
    actor_user_id: UUID,
) -> None:
    """Prevent self-demotion and removal of the final active human admin."""

    from sqlalchemy import select

    from src.models.orm.role_assignments import RoleAssignment

    requested = set(user_ids)
    if not requested:
        return
    active_admin_ids = set(
        (
            await db.execute(
                select(RoleAssignment.user_id)
                .join(User, User.id == RoleAssignment.user_id)
                .where(
                    RoleAssignment.role_id == PLATFORM_ADMIN_ROLE_ID,
                    User.is_active.is_(True),
                    User.is_system.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    removals = active_admin_ids & requested
    if not removals:
        return
    if actor_user_id in removals:
        raise ValueError("You cannot remove your own Platform Admin role")
    if not active_admin_ids - removals:
        raise ValueError("At least one active Platform Admin is required")


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
    from src.models.orm.role_assignments import RoleAssignment
    from src.models.orm.users import Role

    result = await db.execute(
        select(Role.name)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(RoleAssignment.user_id == user_id)
    )
    return list(result.scalars().all())


async def get_user_capabilities(db: AsyncSession, user_id: UUID) -> list[str]:
    """Return the temporary unbounded capability union for legacy token claims.

    New human authorization must use the boundary-aware central evaluator.
    """

    from sqlalchemy import select

    from shared.authorization_scopes import validate_role_scopes
    from src.models.orm.role_assignments import RoleAssignment
    from src.models.orm.users import Role

    result = await db.execute(
        select(Role.capabilities)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(RoleAssignment.user_id == user_id)
    )
    scopes = {
        scope
        for role_capabilities in result.scalars().all()
        for scope in (role_capabilities or [])
    }
    return validate_role_scopes(scopes, custom_role=False)
