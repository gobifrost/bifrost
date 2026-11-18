"""Bearer credentials for identity provider connections.

Only the SHA-256 digest of a token is stored; the raw token is returned once,
when it is generated. Rotation issues a new token and cuts the previous
token's remaining life to a short overlap, so the identity provider can be
updated without a provisioning gap.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.identity_providers import IdentityProvider, IdentityProviderToken
from src.models.orm.organizations import Organization

TOKEN_PREFIX = "bfidp_"
ROTATION_OVERLAP = timedelta(hours=1)
DEFAULT_LIFETIME_DAYS = 365
ALLOWED_LIFETIME_DAYS = (90, 180, 365, 730)
# Bookkeeping writes (last_used_at / last_request_at) at most this often per
# token, so a busy provisioning cycle doesn't rewrite the row on every call.
_USAGE_WRITE_INTERVAL = timedelta(minutes=1)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate() -> tuple[str, str]:
    raw = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    return raw, hash_token(raw)


async def current_token(
    session: AsyncSession, identity_provider_id: UUID
) -> IdentityProviderToken | None:
    """The newest unrevoked token, expired or not (for display)."""
    return await session.scalar(
        select(IdentityProviderToken)
        .where(
            IdentityProviderToken.identity_provider_id == identity_provider_id,
            IdentityProviderToken.revoked_at.is_(None),
        )
        .order_by(IdentityProviderToken.created_at.desc())
        .limit(1)
    )


async def lock_tokens(session: AsyncSession, identity_provider_id: UUID) -> None:
    """Serialize token changes for one connection on its row. Otherwise two
    rotations at once each shorten only the tokens they can see, and both
    new tokens keep their full lifetime."""
    await session.execute(
        select(IdentityProvider.id)
        .where(IdentityProvider.id == identity_provider_id)
        .with_for_update()
    )


async def issue_token(
    session: AsyncSession,
    connection: IdentityProvider,
    *,
    lifetime_days: int,
    created_by: str,
) -> tuple[str, IdentityProviderToken]:
    """Create a new token; earlier live tokens keep working for the overlap."""
    if lifetime_days not in ALLOWED_LIFETIME_DAYS:
        raise ValueError(f"lifetime_days must be one of {ALLOWED_LIFETIME_DAYS}")
    await lock_tokens(session, connection.id)
    now = datetime.now(timezone.utc)
    overlap_end = now + ROTATION_OVERLAP
    await session.execute(
        update(IdentityProviderToken)
        .where(
            IdentityProviderToken.identity_provider_id == connection.id,
            IdentityProviderToken.revoked_at.is_(None),
            IdentityProviderToken.expires_at > overlap_end,
        )
        .values(expires_at=overlap_end)
    )
    raw, digest = _generate()
    token = IdentityProviderToken(
        identity_provider_id=connection.id,
        token_hash=digest,
        token_hint=raw[-4:],
        expires_at=now + timedelta(days=lifetime_days),
        created_by=created_by,
    )
    session.add(token)
    await session.flush()
    return raw, token


async def authenticate(
    session: AsyncSession, identity_provider_id: UUID, raw: str
) -> IdentityProvider | None:
    """The enabled connection ``raw`` is a live token for, or None."""
    if not raw.startswith(TOKEN_PREFIX):
        return None
    digest = hash_token(raw)
    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(IdentityProviderToken, IdentityProvider)
            .join(
                IdentityProvider,
                IdentityProvider.id == IdentityProviderToken.identity_provider_id,
            )
            # A disabled organization's connections stop provisioning; they
            # resume if it is reactivated.
            .join(Organization, Organization.id == IdentityProvider.organization_id)
            .where(
                IdentityProviderToken.token_hash == digest,
                IdentityProviderToken.identity_provider_id == identity_provider_id,
                IdentityProviderToken.revoked_at.is_(None),
                IdentityProviderToken.expires_at > now,
                IdentityProvider.is_enabled.is_(True),
                Organization.is_active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    token, connection = row
    # The lookup is by digest; compare again in constant time so a future
    # change to the query can't turn it into a prefix or partial match.
    if not hmac.compare_digest(token.token_hash, digest):
        return None
    if token.last_used_at is None or now - token.last_used_at > _USAGE_WRITE_INTERVAL:
        token.last_used_at = now
        connection.last_request_at = now
        await session.flush()
    return connection
