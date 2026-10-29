"""Short-lived, browser-wide impersonation credentials."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from src.config import get_settings
from shared.external_access import resolve_provider_org_claim
from src.core.cache import get_shared_redis
from src.core.cache.keys import browser_impersonation_session_key, refresh_token_jti_key
from src.core.principal import UserPrincipal
from src.core.security import create_access_token
from src.models.orm.users import User
from src.services.authorization.impersonation import RunAsError, authorize_run_as
from src.services.user_provisioning import get_user_roles

IMPERSONATION_TTL_SECONDS = 30 * 60
IMPERSONATION_COOKIE_NAME = "impersonation_token"
# The JWT itself remains short lived. Keeping the marker after its expiry makes
# every ordinary browser request fail closed until the operator explicitly exits.
IMPERSONATION_MARKER_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


class BrowserImpersonationError(PermissionError):
    """A browser impersonation credential cannot be used."""


@dataclass(frozen=True)
class BrowserImpersonationCredential:
    """The signed browser marker and durable audit correlation id."""

    access_token: str
    session_id: UUID


def is_browser_impersonation(principal: UserPrincipal) -> bool:
    """Whether a principal carries a complete browser-impersonation provenance."""
    return (
        principal.impersonated_by_user_id is not None
        and principal.impersonation_session_id is not None
    )


async def create_browser_impersonation(
    db: AsyncSession,
    initiator: UserPrincipal,
    target_user_id: UUID,
    initiator_refresh_jti: str,
) -> BrowserImpersonationCredential:
    """Authorize and mint a browser-wide impersonation marker for ``target_user_id``."""
    if (
        initiator.embed
        or initiator.runtime_authority is not None
        or initiator.impersonated_by_user_id is not None
        or initiator.impersonation_session_id is not None
    ):
        raise BrowserImpersonationError("Browser impersonation requires an original human session")

    target = await authorize_run_as(db, initiator, target_user_id)
    if target is None:
        raise BrowserImpersonationError("You cannot impersonate yourself")

    user = await db.get(User, target.user_id)
    if user is None:
        # ``authorize_run_as`` has already hidden unknown targets from callers.
        raise BrowserImpersonationError("The impersonation target is unavailable")

    role_names = await get_user_roles(db, user.id)
    session_id = uuid4()
    claims = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name or user.email.split("@", 1)[0],
        "is_superuser": user.is_superuser,
        "is_external": bool(user.is_external),
        "is_provider_org": await resolve_provider_org_claim(db, user),
        "org_id": str(user.organization_id) if user.organization_id else None,
        "roles": ["authenticated", *role_names],
        "impersonated_by_user_id": str(initiator.user_id),
        "impersonation_session_id": str(session_id),
    }
    token = create_access_token(data=claims, expires_delta=timedelta(seconds=IMPERSONATION_TTL_SECONDS))
    record = {
        "target_user_id": str(user.id),
        "initiator_user_id": str(initiator.user_id),
        "initiator_refresh_jti": initiator_refresh_jti,
        "session_id": str(session_id),
        "expires_at": int((datetime.now(timezone.utc) + timedelta(seconds=IMPERSONATION_TTL_SECONDS)).timestamp()),
    }
    redis = await get_shared_redis()
    await redis.setex(
        browser_impersonation_session_key(str(session_id)),
        IMPERSONATION_TTL_SECONDS,
        json.dumps(record),
    )
    return BrowserImpersonationCredential(access_token=token, session_id=session_id)


def decode_browser_impersonation_marker(token: str) -> dict[str, Any] | None:
    """Verify a marker's signature while allowing expiry-only Exit cleanup.

    Authentication never uses this helper. It exists solely to associate an
    expired marker with its original operator before deleting its Redis record.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


async def validate_browser_impersonation(db: AsyncSession, principal: UserPrincipal) -> None:
    """Recheck a signed impersonation token against its live session and authority."""
    if not is_browser_impersonation(principal):
        raise BrowserImpersonationError("Browser impersonation provenance is incomplete")

    assert principal.impersonated_by_user_id is not None
    assert principal.impersonation_session_id is not None
    redis = await get_shared_redis()
    raw = await redis.get(browser_impersonation_session_key(str(principal.impersonation_session_id)))
    if raw is None:
        raise BrowserImpersonationError("Browser impersonation session is no longer active")
    try:
        record = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BrowserImpersonationError("Browser impersonation session is invalid") from exc
    if (
        record.get("session_id") != str(principal.impersonation_session_id)
        or record.get("target_user_id") != str(principal.user_id)
        or record.get("initiator_user_id") != str(principal.impersonated_by_user_id)
    ):
        raise BrowserImpersonationError("Browser impersonation session does not match this token")

    initiator_refresh_jti = record.get("initiator_refresh_jti")
    if not isinstance(initiator_refresh_jti, str) or not initiator_refresh_jti:
        raise BrowserImpersonationError("Browser impersonation session is invalid")
    if not await redis.exists(
        refresh_token_jti_key(str(principal.impersonated_by_user_id), initiator_refresh_jti)
    ):
        raise BrowserImpersonationError("Browser impersonation operator session is no longer active")

    initiator = await db.get(User, principal.impersonated_by_user_id)
    if initiator is None:
        raise BrowserImpersonationError("Browser impersonation initiator is unavailable")
    initiator_principal = UserPrincipal(
        user_id=initiator.id,
        email=initiator.email,
        organization_id=initiator.organization_id,
        name=initiator.name or "",
        is_active=initiator.is_active,
        is_superuser=initiator.is_superuser,
        is_verified=initiator.is_verified,
        is_external=initiator.is_external,
    )
    try:
        with access_checks.suspend_collecting():
            await authorize_run_as(db, initiator_principal, principal.user_id)
    except RunAsError as exc:
        raise BrowserImpersonationError(exc.detail) from exc


async def revoke_browser_impersonation(principal: UserPrincipal) -> None:
    """End a browser impersonation session immediately without touching login cookies."""
    if not is_browser_impersonation(principal):
        raise BrowserImpersonationError("This is not a browser impersonation session")
    assert principal.impersonation_session_id is not None
    redis = await get_shared_redis()
    await redis.delete(browser_impersonation_session_key(str(principal.impersonation_session_id)))


async def revoke_browser_impersonation_marker(
    marker: str,
    original_operator_id: UUID,
) -> UUID | None:
    """Revoke only the marker session owned by ``original_operator_id``.

    A missing/expired/revoked session is an idempotent cleanup. The signed
    marker still has to name the original operator before an existing record
    can be deleted.
    """
    payload = decode_browser_impersonation_marker(marker)
    if payload is None:
        return None
    try:
        session_id = UUID(str(payload["impersonation_session_id"]))
        initiator_id = UUID(str(payload["impersonated_by_user_id"]))
        target_user_id = UUID(str(payload["sub"]))
    except (KeyError, TypeError, ValueError):
        return None
    if initiator_id != original_operator_id:
        return None

    redis = await get_shared_redis()
    raw = await redis.get(browser_impersonation_session_key(str(session_id)))
    if raw is None:
        return target_user_id
    try:
        record = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        record.get("session_id") != str(session_id)
        or record.get("initiator_user_id") != str(original_operator_id)
        or record.get("target_user_id") != str(target_user_id)
    ):
        return None
    await redis.delete(browser_impersonation_session_key(str(session_id)))
    return target_user_id
