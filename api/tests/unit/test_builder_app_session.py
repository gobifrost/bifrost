"""Unit tests for builder launch codes, app-host sessions, and app tokens."""

import json
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from shared.authorization_scopes import SOLUTION_APP_RUNTIME_SCOPES
from src.core.security import decode_token
from src.services.builder.app_session import (
    DEFAULT_SESSION_TTL_SECONDS,
    LAUNCH_CODE_TTL_SECONDS,
    AppLaunchService,
    AppSession,
    launch_key,
    mint_app_token,
    session_key,
)


class FakeRedis:
    """In-memory stand-in implementing only the methods AppLaunchService uses.

    TTLs are modelled as a monotonic fake clock rather than real time, so
    expiry is asserted deterministically via ``advance()``.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._expires_at: dict[str, float] = {}
        self._now = 0.0

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def _reap(self, key: str) -> None:
        expires_at = self._expires_at.get(key)
        if expires_at is not None and expires_at <= self._now:
            self._values.pop(key, None)
            self._expires_at.pop(key, None)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._values[key] = value
        if ex is None:
            self._expires_at.pop(key, None)
        else:
            self._expires_at[key] = self._now + ex

    async def get(self, key: str) -> str | None:
        self._reap(key)
        return self._values.get(key)

    async def getdel(self, key: str) -> str | None:
        self._reap(key)
        self._expires_at.pop(key, None)
        return self._values.pop(key, None)

    async def delete(self, key: str) -> int:
        self._reap(key)
        self._expires_at.pop(key, None)
        return 1 if self._values.pop(key, None) is not None else 0

    async def expire(self, key: str, seconds: int) -> bool:
        self._reap(key)
        if key not in self._values:
            return False
        self._expires_at[key] = self._now + seconds
        return True

    def ttl_of(self, key: str) -> float | None:
        expires_at = self._expires_at.get(key)
        return None if expires_at is None else expires_at - self._now


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def service(fake_redis: FakeRedis) -> AppLaunchService:
    # The service only uses set/get/getdel/delete/expire; FakeRedis covers
    # exactly those, so the type mismatch with redis.Redis is intentional.
    return AppLaunchService(fake_redis)  # type: ignore[arg-type]


@pytest.fixture
def launch_args() -> dict[str, Any]:
    return {
        "user_id": uuid4(),
        "solution_id": uuid4(),
        "app_id": uuid4(),
        "organization_id": uuid4(),
        "path": "/reports/monthly",
    }


class TestLaunchCode:
    async def test_round_trip_preserves_binding(
        self, service: AppLaunchService, launch_args: dict[str, Any]
    ):
        code = await service.create_launch_code(**launch_args)
        session = await service.redeem_launch_code(code)

        assert session is not None
        assert session.user_id == launch_args["user_id"]
        assert session.solution_id == launch_args["solution_id"]
        assert session.app_id == launch_args["app_id"]
        assert session.organization_id == launch_args["organization_id"]
        assert session.path == launch_args["path"]
        assert session.session_id

    async def test_code_is_opaque_and_short_lived(
        self,
        service: AppLaunchService,
        fake_redis: FakeRedis,
        launch_args: dict[str, Any],
    ):
        code = await service.create_launch_code(**launch_args)

        # The code itself leaks no binding — it is a random pointer.
        assert str(launch_args["solution_id"]) not in code
        assert fake_redis.ttl_of(launch_key(code)) == LAUNCH_CODE_TTL_SECONDS

        stored = json.loads(await fake_redis.get(launch_key(code)) or "{}")
        assert stored["app_id"] == str(launch_args["app_id"])

    async def test_second_redemption_returns_none(
        self, service: AppLaunchService, launch_args: dict[str, Any]
    ):
        code = await service.create_launch_code(**launch_args)

        assert await service.redeem_launch_code(code) is not None
        assert await service.redeem_launch_code(code) is None

    async def test_expired_code_cannot_be_redeemed(
        self,
        service: AppLaunchService,
        fake_redis: FakeRedis,
        launch_args: dict[str, Any],
    ):
        code = await service.create_launch_code(**launch_args)
        fake_redis.advance(LAUNCH_CODE_TTL_SECONDS + 1)

        assert await service.redeem_launch_code(code) is None

    async def test_unknown_code_returns_none(self, service: AppLaunchService):
        assert await service.redeem_launch_code("not-a-real-code") is None

    async def test_default_path(self, service: AppLaunchService, launch_args: dict[str, Any]):
        launch_args.pop("path")
        code = await service.create_launch_code(**launch_args)

        session = await service.redeem_launch_code(code)
        assert session is not None
        assert session.path == "/"


class TestAppSessionLifecycle:
    async def test_session_readable_after_redemption(
        self,
        service: AppLaunchService,
        fake_redis: FakeRedis,
        launch_args: dict[str, Any],
    ):
        code = await service.create_launch_code(**launch_args)
        session = await service.redeem_launch_code(code)
        assert session is not None

        loaded = await service.get_session(session.session_id)
        assert loaded == session
        assert fake_redis.ttl_of(session_key(session.session_id)) == DEFAULT_SESSION_TTL_SECONDS

    async def test_session_expires(
        self,
        service: AppLaunchService,
        fake_redis: FakeRedis,
        launch_args: dict[str, Any],
    ):
        code = await service.create_launch_code(**launch_args)
        session = await service.redeem_launch_code(code)
        assert session is not None

        fake_redis.advance(DEFAULT_SESSION_TTL_SECONDS + 1)
        assert await service.get_session(session.session_id) is None

    async def test_renew_extends_ttl(
        self,
        service: AppLaunchService,
        fake_redis: FakeRedis,
        launch_args: dict[str, Any],
    ):
        code = await service.create_launch_code(**launch_args)
        session = await service.redeem_launch_code(code)
        assert session is not None

        fake_redis.advance(DEFAULT_SESSION_TTL_SECONDS - 10)
        assert await service.renew_session(session.session_id) is True

        fake_redis.advance(DEFAULT_SESSION_TTL_SECONDS - 10)
        assert await service.get_session(session.session_id) is not None

    async def test_renew_missing_session_returns_false(self, service: AppLaunchService):
        assert await service.renew_session("gone") is False

    async def test_revoke_ends_session(
        self, service: AppLaunchService, launch_args: dict[str, Any]
    ):
        code = await service.create_launch_code(**launch_args)
        session = await service.redeem_launch_code(code)
        assert session is not None

        await service.revoke_session(session.session_id)

        assert await service.get_session(session.session_id) is None
        assert await service.renew_session(session.session_id) is False

    async def test_custom_session_ttl(
        self, fake_redis: FakeRedis, launch_args: dict[str, Any]
    ):
        service = AppLaunchService(fake_redis, session_ttl_seconds=300)  # type: ignore[arg-type]
        code = await service.create_launch_code(**launch_args)
        session = await service.redeem_launch_code(code)
        assert session is not None

        assert fake_redis.ttl_of(session_key(session.session_id)) == 300


@pytest.fixture
def session(launch_args: dict[str, Any]) -> AppSession:
    """A session as redeem_launch_code would produce; minting needs no Redis."""
    return AppSession(
        session_id=secrets.token_urlsafe(32),
        created_at=datetime.now(timezone.utc),
        **launch_args,
    )


class TestAppToken:
    def test_claims_are_exact(self, session: AppSession):
        token = mint_app_token(session, user_email="dev@gobifrost.com")
        payload = decode_token(token, expected_type="access")

        assert payload is not None
        assert payload["actor_type"] == "solution_app"
        assert payload["actor_user_id"] == str(session.user_id)
        assert payload["sub"] == str(session.user_id)
        assert payload["solution_id"] == str(session.solution_id)
        assert payload["app_id"] == str(session.app_id)
        assert payload["organization_id"] == str(session.organization_id)
        assert payload["jti"]
        assert set(payload["scopes"]) == SOLUTION_APP_RUNTIME_SCOPES
        UUID(payload["jti"])
        assert payload["exp"]

    def test_token_is_not_privileged(self, session: AppSession):
        token = mint_app_token(session, user_email="dev@gobifrost.com")
        payload = decode_token(token, expected_type="access")

        assert payload is not None
        assert payload.get("is_superuser") is not True
        assert "is_superuser" not in payload
        assert payload.get("embed") is not True

    def test_jti_is_unique_per_mint(self, session: AppSession):
        first = decode_token(mint_app_token(session, user_email="a@b.com"))
        second = decode_token(mint_app_token(session, user_email="a@b.com"))

        assert first is not None and second is not None
        assert first["jti"] != second["jti"]

    def test_lifetime_is_short(self, session: AppSession):
        before = datetime.now(timezone.utc)
        token = mint_app_token(session, user_email="dev@gobifrost.com", expires_minutes=12)
        payload = decode_token(token)

        assert payload is not None
        expires_in = payload["exp"] - before.timestamp()
        # A stolen app token must die in minutes, not the 8h of the session.
        assert 11 * 60 < expires_in <= 12 * 60 + 5
