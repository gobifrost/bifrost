"""Identities: user accounts that run work no person started.

Every organization has a default identity (`org_default`); one global identity
(`global_default`) has no organization; admins may add more (`custom`). They
are ordinary users — roles, reach and attribution work as for anyone — but
never sign in. `users.identity_kind` marks them.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import USER_ROLE_ID
from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.organizations import Organization
from src.models.orm.users import User

IDENTITY_EMAIL_DOMAIN = "identities.bifrost.internal"


class IdentityKind(StrEnum):
    ORG_DEFAULT = "org_default"
    GLOBAL_DEFAULT = "global_default"
    CUSTOM = "custom"


def is_identity(user: User) -> bool:
    return user.identity_kind is not None


def is_identity_email(email: str) -> bool:
    return email.lower().endswith("@" + IDENTITY_EMAIL_DOMAIN)


def refuse_identity_sign_in(user: User) -> None:
    """Every path that signs a user in, or gives them a credential, calls this."""
    if is_identity(user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def ensure_default_identity(session: AsyncSession, organization: Organization) -> User:
    """The organization's default identity, created with it when missing."""
    from shared.sdk_users import set_user_base_role

    existing = await session.scalar(
        select(User).where(
            User.organization_id == organization.id,
            User.identity_kind == IdentityKind.ORG_DEFAULT,
        )
    )
    if existing is not None:
        return existing
    identity = User(
        email=f"identity-{organization.id}@{IDENTITY_EMAIL_DOMAIN}",
        name=f"{organization.name} identity",
        is_active=True,
        is_verified=True,
        is_registered=True,
        is_system=False,
        is_external=False,
        organization_id=organization.id,
        identity_kind=IdentityKind.ORG_DEFAULT,
    )
    await set_user_base_role(session, identity, USER_ROLE_ID)
    session.add(identity)
    await session.flush()
    return identity


async def validate_run_identity(
    session: AsyncSession, *, workflow_organization_id: UUID | None, identity_id: UUID
) -> None:
    """Raise ValueError when a workflow of this organization can't run as `identity_id`."""
    identity = await session.get(User, identity_id)
    if identity is None or not is_identity(identity):
        raise ValueError(f"{identity_id} is not an identity")
    if not run_identity_allowed(workflow_organization_id=workflow_organization_id, identity=identity):
        raise ValueError(f"{identity.name} can't run workflows of this organization")


def run_identity_allowed(*, workflow_organization_id: UUID | None, identity: User) -> bool:
    """Whether a workflow may run unattended as `identity`.

    An identity of the workflow's own organization, or — since Global and
    the provider organization share — for a global or provider-organization
    workflow, an identity of either.
    """
    if not is_identity(identity):
        return False
    if identity.organization_id == workflow_organization_id:
        return True
    shared = {None, PROVIDER_ORG_ID}
    return workflow_organization_id in shared and identity.organization_id in shared
