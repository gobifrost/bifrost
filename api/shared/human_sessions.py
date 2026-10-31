"""Durable, serialized refresh sessions for human credentials.

Every mutation locks the owning ``users`` row before reading or changing a
session row. Refresh rotation and either form of revocation therefore
serialize per person: a successor JTI is durable before rotation returns, and
revocation sees that successor before it commits. The database context rolls
back the session write and any epoch bump if its caller is cancelled or fails.

Issuance prunes expired rows only for that user. Expired rows for people who
never authenticate again remain an operational retention question; this module
does not add a global scheduler or a second lifecycle system for them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.core.security import create_refresh_token, decode_token
from src.models.orm.human_sessions import HumanAuthSession
from src.models.orm.users import User
from src.services.authorization.tokens import (
    HumanSubject,
    TokenSubjectUnavailable,
    issue_access_token,
)


@dataclass(frozen=True)
class SessionTokens:
    """A newly minted human access token and its rotatable refresh token."""

    access_token: str
    refresh_token: str
    expires_at: datetime


class HumanSessionUnavailable(Exception):
    """The requested human refresh session cannot issue credentials."""


def _jti_hash(jti: str) -> str:
    """Return the fixed-size registry value for a refresh JTI."""
    return sha256(jti.encode("utf-8")).hexdigest()


def _refresh_claim_uuid(payload: dict[str, object], claim: str, message: str) -> UUID:
    """Read a required UUID refresh claim or raise the public session refusal."""
    value = payload.get(claim)
    if not isinstance(value, str):
        raise HumanSessionUnavailable(message)
    try:
        return UUID(value)
    except ValueError as exc:
        raise HumanSessionUnavailable(message) from exc


def _refresh_claim_jti(payload: dict[str, object]) -> str:
    """Read the JTI used to select the current refresh-token generation."""
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise HumanSessionUnavailable("Refresh token is invalid or expired.")
    return jti


def _refresh_expiry(refresh_token: str) -> datetime:
    """Read the actual configured expiry encoded in a freshly minted token."""
    payload = decode_token(refresh_token, expected_type="refresh")
    if payload is None or not isinstance(payload.get("exp"), int):
        raise RuntimeError("Freshly minted refresh token could not be decoded.")
    return datetime.fromtimestamp(payload["exp"], timezone.utc)


def _mint_refresh_token(user_id: UUID, session_id: UUID) -> tuple[str, str, datetime]:
    """Mint a refresh token bound to one durable session and its actual expiry."""
    refresh_token, jti = create_refresh_token({"sub": str(user_id), "sid": str(session_id)})
    return refresh_token, jti, _refresh_expiry(refresh_token)


def _is_human_user(user: User | None) -> bool:
    """Whether the locked row is eligible for human credentials."""
    return user is not None and user.is_active and not user.is_system and user.identity_kind is None


@asynccontextmanager
async def _human_session_transaction() -> AsyncIterator[AsyncSession]:
    """Roll back the durable session work even when a caller is cancelled."""
    async with get_db_context() as db:
        try:
            yield db
        except BaseException:
            await db.rollback()
            raise


async def _issue_access_token(user_id: UUID, session_id: UUID) -> tuple[str, datetime]:
    """Mint a candidate access token before entering a session mutation transaction."""
    try:
        issued = await issue_access_token(HumanSubject(user_id=user_id, session_id=session_id))
    except TokenSubjectUnavailable as exc:
        raise HumanSessionUnavailable("The human account is not available.") from exc
    return issued.token, issued.expires_at


async def _preflight_current_refresh_session(user_id: UUID, session_id: UUID, jti: str) -> bool:
    """Cheap non-authoritative guard before paying to mint a rotation candidate."""
    now = datetime.now(timezone.utc)
    async with get_db_context() as db:
        found = await db.scalar(
            select(HumanAuthSession.id).where(
                HumanAuthSession.id == session_id,
                HumanAuthSession.user_id == user_id,
                HumanAuthSession.current_jti_hash == _jti_hash(jti),
                HumanAuthSession.revoked_at.is_(None),
                HumanAuthSession.expires_at > now,
            )
        )
    return found is not None


async def issue_human_session(user_id: UUID) -> SessionTokens:
    """Create and commit a durable human refresh session before returning it."""
    session_id = uuid4()
    access_token, access_expires_at = await _issue_access_token(user_id, session_id)

    async with _human_session_transaction() as db:
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        if not _is_human_user(user):
            raise HumanSessionUnavailable("The human account is not available.")

        await db.execute(
            delete(HumanAuthSession).where(
                HumanAuthSession.user_id == user_id,
                HumanAuthSession.expires_at <= datetime.now(timezone.utc),
            )
        )
        refresh_token, jti, expires_at = _mint_refresh_token(user_id, session_id)
        db.add(
            HumanAuthSession(
                id=session_id,
                user_id=user_id,
                current_jti_hash=_jti_hash(jti),
                expires_at=expires_at,
            )
        )
        await db.flush()
        return SessionTokens(access_token, refresh_token, access_expires_at)


async def rotate_human_session(raw_refresh_token: str) -> SessionTokens:
    """Atomically replace a current human refresh JTI with its successor."""
    payload = decode_token(raw_refresh_token, expected_type="refresh")
    if payload is None:
        raise HumanSessionUnavailable("Refresh token is invalid or expired.")

    user_id = _refresh_claim_uuid(payload, "sub", "Refresh token is invalid or expired.")
    session_id = _refresh_claim_uuid(
        payload,
        "sid",
        "This refresh token predates durable sessions; reauthentication is required.",
    )
    jti = _refresh_claim_jti(payload)
    if not await _preflight_current_refresh_session(user_id, session_id, jti):
        raise HumanSessionUnavailable("Refresh token has been revoked or already used.")
    access_token, access_expires_at = await _issue_access_token(user_id, session_id)

    async with _human_session_transaction() as db:
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        if not _is_human_user(user):
            raise HumanSessionUnavailable("The human account is not available.")

        now = datetime.now(timezone.utc)
        session = await db.scalar(
            select(HumanAuthSession)
            .where(
                HumanAuthSession.id == session_id,
                HumanAuthSession.user_id == user_id,
                HumanAuthSession.current_jti_hash == _jti_hash(jti),
                HumanAuthSession.revoked_at.is_(None),
                HumanAuthSession.expires_at > now,
            )
            .with_for_update()
        )
        if session is None:
            raise HumanSessionUnavailable("Refresh token has been revoked or already used.")

        refresh_token, successor_jti, expires_at = _mint_refresh_token(user_id, session_id)
        session.current_jti_hash = _jti_hash(successor_jti)
        session.expires_at = expires_at
        await db.flush()
        return SessionTokens(access_token, refresh_token, access_expires_at)


async def _revoke_human_sessions_in_transaction(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID | None,
) -> int:
    """Revoke matching sessions and advance the epoch in the caller's transaction."""
    now = datetime.now(timezone.utc)
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        return 0

    statement = select(HumanAuthSession).where(HumanAuthSession.user_id == user_id)
    if session_id is not None:
        statement = statement.where(HumanAuthSession.id == session_id)
    sessions = list((await db.scalars(statement.with_for_update())).all())
    newly_revoked = [session for session in sessions if session.revoked_at is None]
    for session in newly_revoked:
        session.revoked_at = now
    await db.execute(text("SELECT authz_epoch_bump_once()"))
    return len(newly_revoked)


async def revoke_all_human_sessions_in_transaction(db: AsyncSession, user_id: UUID) -> int:
    """Lock the user and revoke all human sessions in an already-owned transaction."""
    return await _revoke_human_sessions_in_transaction(db, user_id, None)


async def revoke_human_session(user_id: UUID, session_id: UUID | None) -> int:
    """Revoke one stable session and invalidate existing human access tokens."""
    if session_id is None:
        raise HumanSessionUnavailable("A durable session is required to log out.")
    async with _human_session_transaction() as db:
        return await _revoke_human_sessions_in_transaction(db, user_id, session_id)


async def revoke_all_human_sessions(user_id: UUID) -> int:
    """Revoke every durable refresh session belonging to a human user."""
    async with _human_session_transaction() as db:
        return await revoke_all_human_sessions_in_transaction(db, user_id)


async def has_live_refresh_token_jti(db: AsyncSession, user_id: UUID, jti: str) -> bool:
    """Read the current generation in the caller's primary transaction."""
    session_id = await db.scalar(
        select(HumanAuthSession.id).where(
            HumanAuthSession.user_id == user_id,
            HumanAuthSession.current_jti_hash == _jti_hash(jti),
            HumanAuthSession.revoked_at.is_(None),
            HumanAuthSession.expires_at > datetime.now(timezone.utc),
        )
    )
    return session_id is not None
