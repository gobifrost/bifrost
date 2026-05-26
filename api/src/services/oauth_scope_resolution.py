"""Scoped OAuth provider and token lookup helpers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.oauth import OAuthProvider, OAuthToken


async def get_oauth_provider_for_scope(
    db: AsyncSession, connection_name: str, org_uuid: UUID | None
) -> OAuthProvider | None:
    """Load an org-specific OAuth provider, falling back to the global provider."""
    if org_uuid is not None:
        result = await db.execute(
            select(OAuthProvider).where(
                OAuthProvider.provider_name == connection_name,
                OAuthProvider.organization_id == org_uuid,
            )
        )
        provider = result.scalars().first()
        if provider:
            return provider

    result = await db.execute(
        select(OAuthProvider).where(
            OAuthProvider.provider_name == connection_name,
            OAuthProvider.organization_id.is_(None),
        )
    )
    return result.scalars().first()


async def get_oauth_token_for_scope(
    db: AsyncSession, provider_id: UUID, org_uuid: UUID | None
) -> OAuthToken | None:
    """Load an org-level OAuth token, falling back to the global provider token."""
    if org_uuid is not None:
        result = await db.execute(
            select(OAuthToken)
            .where(
                OAuthToken.provider_id == provider_id,
                OAuthToken.organization_id == org_uuid,
                OAuthToken.user_id.is_(None),
            )
            .order_by(OAuthToken.created_at.desc(), OAuthToken.id.desc())
        )
        token = result.scalars().first()
        if token:
            return token

    result = await db.execute(
        select(OAuthToken)
        .where(
            OAuthToken.provider_id == provider_id,
            OAuthToken.organization_id.is_(None),
            OAuthToken.user_id.is_(None),
        )
        .order_by(OAuthToken.created_at.desc(), OAuthToken.id.desc())
    )
    return result.scalars().first()
