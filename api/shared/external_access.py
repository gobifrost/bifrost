"""Provider-organization claim resolution at token mint.

The external claim is always the user account's ``is_external`` value.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_provider_org_claim(db: AsyncSession, user) -> bool:
    """Compute the ``is_provider_org`` token claim for a user.

    Returns True when the user belongs to a provider organization — the
    non-admin half of the canonical bypass rule
    (``bypass = is_platform_admin OR is_provider_org``). Provider-org members
    are platform-org staff who hop into client orgs via the portal; they get
    cross-org / global scope on files, tables, and workflow execution without
    being platform admins.

    One indexed SELECT against the user's org. System users and users with no
    org resolve to False (no org row → no provider membership). Computed at
    token mint (login / refresh), never per-request.

    Args:
        db: Async DB session.
        user: The ORM ``User`` row being minted a token.
    """
    if user.organization_id is None:
        # System accounts / org-less principals: no provider membership.
        return False

    from src.models.orm.organizations import Organization

    is_provider = await db.scalar(
        select(Organization.is_provider).where(
            Organization.id == user.organization_id
        )
    )
    return bool(is_provider)
