"""User invite service: create, regenerate, revoke, consume invite tokens."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import get_password_hash
from src.models.contracts.user_invites import InviteStatus
from src.models.orm import User, UserInvite

# Invites expire after 7 days by default.
INVITE_TTL = timedelta(days=7)


def _hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token, used as the lookup key."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class InviteConsumeError(Exception):
    """Raised when an invite cannot be consumed."""


class UserInviteService:
    """Manages single-use, time-bound, hashed invite tokens for user registration."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_or_replace(
        self, *, user_id: UUID, created_by: UUID | None
    ) -> tuple[str, UserInvite]:
        """Generate a fresh invite, replacing any existing one for this user.

        Returns ``(raw_token, invite_row)``. The raw token is shown to the
        inviter once; only the SHA-256 hash is persisted.
        """
        existing = await self._get_for_user(user_id)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

        raw = secrets.token_urlsafe(32)
        invite = UserInvite(
            user_id=user_id,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(timezone.utc) + INVITE_TTL,
            created_by=created_by,
        )
        self.session.add(invite)
        await self.session.flush()
        return raw, invite

    async def revoke(self, *, user_id: UUID) -> None:
        existing = await self._get_for_user(user_id)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

    async def consume(self, *, token: str, password: str | None = None) -> User:
        token_hash = _hash_token(token)
        invite = (
            await self.session.execute(
                select(UserInvite).where(UserInvite.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        if invite is None:
            raise InviteConsumeError("Invite not found")
        if invite.consumed_at is not None:
            raise InviteConsumeError("Invite already consumed")
        if invite.revoked_at is not None:
            raise InviteConsumeError("Invite revoked")
        if invite.expires_at < datetime.now(timezone.utc):
            raise InviteConsumeError("Invite expired")

        user = (
            await self.session.execute(select(User).where(User.id == invite.user_id))
        ).scalar_one()

        if password:
            user.hashed_password = get_password_hash(password)
        user.is_registered = True
        invite.consumed_at = datetime.now(timezone.utc)

        await self.session.flush()
        return user

    async def status_for(self, user: User) -> str:
        if user.is_registered:
            return InviteStatus.ACTIVE
        invite = await self._get_for_user(user.id)
        if invite is None:
            return InviteStatus.NEVER_INVITED
        if invite.revoked_at is not None:
            return InviteStatus.NEVER_INVITED
        if invite.expires_at < datetime.now(timezone.utc):
            return InviteStatus.EXPIRED
        return InviteStatus.PENDING

    async def _get_for_user(self, user_id: UUID) -> UserInvite | None:
        return (
            await self.session.execute(
                select(UserInvite).where(UserInvite.user_id == user_id)
            )
        ).scalar_one_or_none()
