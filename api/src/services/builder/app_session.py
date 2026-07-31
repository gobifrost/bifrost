"""Launch codes and renewable app-host sessions for Solution apps.

Implements the launch flow from the private Solution builder design:

1. The control-plane UI, authenticated as a normal user, asks for a one-time
   launch code bound to (user, Solution, app, org, path). The code lives in
   Redis for ~60 seconds.
2. The app host redeems the code exactly once, which creates a long-lived
   app-host session (default 8h) held only in a host-only HttpOnly cookie on
   the app origin.
3. The app host mints short-lived (10-15 min) ``solution_app`` access tokens
   from that session, renewing them for as long as the session is alive.

Tokens never travel in query strings or Referer headers: the launch code is
single-use and carries no authority of its own, and the access token is minted
server-side from the session cookie.
"""

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as redis

from shared.authorization_scopes import SOLUTION_APP_RUNTIME_SCOPES
from src.core.security import ACTOR_TYPE_SOLUTION_APP, create_access_token

LAUNCH_CODE_TTL_SECONDS = 60
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60

_LAUNCH_KEY_PREFIX = "bifrost:builder:launch:"
_SESSION_KEY_PREFIX = "bifrost:builder:appsession:"

# Marker claim that routes a token to solution-app validation instead of
# normal-user validation. Downstream auth builds a UserPrincipal from any
# access token and defaults is_superuser/embed to False when absent, so a
# solution_app token is inert-but-indistinguishable without this claim. Every
# user-auth path default-denies any token carrying actor_type, so this must
# always be present. Defined in src.core.security (the shared decode layer) so
# auth can reject on it without importing this module.
ACTOR_TYPE = ACTOR_TYPE_SOLUTION_APP


def launch_key(code: str) -> str:
    """Redis key holding a pending one-time launch code."""
    return f"{_LAUNCH_KEY_PREFIX}{code}"


def session_key(session_id: str) -> str:
    """Redis key holding an app-host session."""
    return f"{_SESSION_KEY_PREFIX}{session_id}"


@dataclass(frozen=True)
class AppSession:
    """A redeemed app-host session backing token renewal for one launch."""

    session_id: str
    user_id: UUID
    solution_id: UUID
    app_id: UUID
    organization_id: UUID
    created_at: datetime
    path: str

    def to_payload(self) -> dict[str, Any]:
        """Serialize for Redis storage (session_id lives in the key)."""
        return {
            "user_id": str(self.user_id),
            "solution_id": str(self.solution_id),
            "app_id": str(self.app_id),
            "organization_id": str(self.organization_id),
            "created_at": self.created_at.isoformat(),
            "path": self.path,
        }

    @classmethod
    def from_payload(cls, session_id: str, payload: dict[str, Any]) -> "AppSession":
        return cls(
            session_id=session_id,
            user_id=UUID(payload["user_id"]),
            solution_id=UUID(payload["solution_id"]),
            app_id=UUID(payload["app_id"]),
            organization_id=UUID(payload["organization_id"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            path=payload["path"],
        )


def mint_app_token(
    session: AppSession,
    *,
    user_email: str,
    expires_minutes: int = 12,
) -> str:
    """Mint a short-lived ``solution_app`` access token for an app-host session.

    The token carries ``actor_type="solution_app"`` as its routing marker: the
    shared auth path builds a principal from any access token and treats a
    missing ``is_superuser`` as False, so nothing else distinguishes this from
    a low-privilege user token. Middleware and route dependencies match on
    ``actor_type`` to apply Solution-runtime enforcement.

    ``is_superuser`` is never set. ``org_id`` and ``email`` are present because
    the shared principal builder rejects a non-superuser token that lacks
    them.

    Args:
        session: The redeemed app-host session the token is minted from.
        user_email: Email of the launching user, for the required email claim.
        expires_minutes: Access-token lifetime; short by design so a stolen
            token expires quickly while the session keeps the app usable.

    Returns:
        Encoded JWT string.
    """
    claims = {
        "sub": str(session.user_id),
        "email": user_email,
        "actor_type": ACTOR_TYPE,
        "actor_user_id": str(session.user_id),
        "solution_id": str(session.solution_id),
        "app_id": str(session.app_id),
        "organization_id": str(session.organization_id),
        "org_id": str(session.organization_id),
        "jti": str(uuid4()),
        "scopes": sorted(SOLUTION_APP_RUNTIME_SCOPES),
    }
    return create_access_token(claims, expires_delta=timedelta(minutes=expires_minutes))


class AppLaunchService:
    """Issues launch codes and manages the app-host sessions they redeem into."""

    def __init__(
        self,
        redis_client: redis.Redis,
        *,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._session_ttl = session_ttl_seconds

    async def create_launch_code(
        self,
        *,
        user_id: UUID,
        solution_id: UUID,
        app_id: UUID,
        organization_id: UUID,
        path: str = "/",
    ) -> str:
        """Create a one-time launch code bound to a user/Solution/app/path.

        The code carries no authority itself — it is an opaque pointer to a
        Redis entry that expires in 60 seconds and is destroyed on redemption.
        """
        code = secrets.token_urlsafe(32)
        payload = {
            "user_id": str(user_id),
            "solution_id": str(solution_id),
            "app_id": str(app_id),
            "organization_id": str(organization_id),
            "path": path,
        }
        await self._redis.set(
            launch_key(code),
            json.dumps(payload),
            ex=LAUNCH_CODE_TTL_SECONDS,
        )
        return code

    async def redeem_launch_code(self, code: str) -> AppSession | None:
        """Redeem a launch code exactly once into an app-host session.

        Returns None if the code is unknown, already redeemed, or expired.
        """
        raw = await self._redis.getdel(launch_key(code))
        if raw is None:
            return None

        payload = json.loads(raw)
        session = AppSession(
            session_id=secrets.token_urlsafe(32),
            user_id=UUID(payload["user_id"]),
            solution_id=UUID(payload["solution_id"]),
            app_id=UUID(payload["app_id"]),
            organization_id=UUID(payload["organization_id"]),
            created_at=datetime.now(timezone.utc),
            path=payload["path"],
        )
        await self._redis.set(
            session_key(session.session_id),
            json.dumps(session.to_payload()),
            ex=self._session_ttl,
        )
        return session

    async def get_session(self, session_id: str) -> AppSession | None:
        """Load a live app-host session, or None if expired or revoked."""
        raw = await self._redis.get(session_key(session_id))
        if raw is None:
            return None
        return AppSession.from_payload(session_id, json.loads(raw))

    async def revoke_session(self, session_id: str) -> None:
        """Destroy an app-host session, ending token renewal for that launch."""
        await self._redis.delete(session_key(session_id))

    async def renew_session(self, session_id: str) -> bool:
        """Extend a live session's TTL. Returns False if it is already gone."""
        return bool(await self._redis.expire(session_key(session_id), self._session_ttl))
