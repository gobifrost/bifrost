"""MCP authorization codes are bound to the server secret.

The Redis key is derived from the code with the platform secret, and the stored
record carries a tag over its fields plus the code. ``/token`` accepts only a
record stored under the derived key with a matching tag, once.
"""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from src.services.mcp_server.auth import (
    BifrostAuthProvider,
)

from shared.mcp_oauth import _mcp_auth_code_key, _mcp_state_key, _seal_auth_code_record
from shared.app_connections import preview_mcp_connection, decide_app_connection
from src.core.principal import UserPrincipal

_VERIFIER = "verifier-for-unit-tests"
_REDIRECT_URI = "http://client.example/cb"


class _FakeRedis:
    """In-memory stand-in for the redis.asyncio client calls the provider makes."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def getdel(self, name: str) -> str | None:
        return self.values.pop(name, None)

    async def setex(self, name: str, time: int, value: str) -> bool:
        self.values[name] = value
        return True

    async def delete(self, *names: str) -> int:
        return sum(1 for name in names if self.values.pop(name, None) is not None)


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@pytest.fixture
def user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        email="person@example.com",
        name="Person",
        is_active=True,
        is_superuser=False,
        is_external=False,
        organization_id=uuid4(),
        identity_kind=None,
    )


@pytest.fixture
def redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def minted(user: SimpleNamespace, redis: _FakeRedis):
    """Patch the shared issuer and record the requested human user."""
    minted_users: list[Any] = []

    async def issue_human_session(user_id: Any) -> SimpleNamespace:
        minted_users.append(user_id)
        return SimpleNamespace(
            access_token="minted.jwt",
            refresh_token="refresh.jwt",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )

    @asynccontextmanager
    async def get_db_context():
        yield AsyncMock()

    users = MagicMock()
    users.get_by_id = AsyncMock(side_effect=lambda user_id: user if str(user.id) == str(user_id) else None)

    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=redis)),
        patch("src.core.database.get_db_context", get_db_context),
        patch("src.repositories.users.UserRepository", return_value=users),
        patch("src.services.mcp_server.auth.issue_human_session", side_effect=issue_human_session),
    ):
        yield minted_users


async def _issue_code(provider: BifrostAuthProvider, redis: _FakeRedis, user: SimpleNamespace) -> str:
    """Run the login callback for ``user`` and return the issued code."""
    redis.values[_mcp_state_key("internal")] = _seal_auth_code_record("internal", {
        "client_id": "client",
        "redirect_uri": _REDIRECT_URI,
        "state": "client-state",
        "code_challenge": _challenge(_VERIFIER),
        "scope": "mcp:access",
    })
    principal = UserPrincipal(user_id=user.id, email=user.email, organization_id=user.organization_id)
    @asynccontextmanager
    async def get_db_context():
        yield AsyncMock()
    with (
        patch("shared.app_connections.get_shared_redis", new=AsyncMock(return_value=redis)),
        patch("shared.app_connections.get_db_context", get_db_context),
        patch("shared.app_connections.get_mcp_config_cached", new=AsyncMock(return_value=SimpleNamespace(enabled=True))),
    ):
        details = await preview_mcp_connection("internal", principal)
        result = await decide_app_connection("mcp", "internal", principal, True, details.consent_token)
    redirect_url = result.redirect_url
    assert redirect_url is not None
    return parse_qs(urlsplit(redirect_url).query)["code"][0]


async def _exchange(
    provider: BifrostAuthProvider, code: str, *, client_id: str = "client",
) -> tuple[int, dict[str, Any]]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _REDIRECT_URI,
        "code_verifier": _VERIFIER,
        "client_id": client_id,
    }
    request = SimpleNamespace(form=AsyncMock(return_value=form))
    response = await provider._token(request)  # type: ignore[arg-type]
    return response.status_code, json.loads(bytes(response.body))


@pytest.mark.parametrize("claim", ["impersonated_by_user_id", "impersonation_session_id"])
async def test_impersonated_user_cannot_approve_connection(redis, user, claim):
    from fastapi import HTTPException
    principal = UserPrincipal(user_id=user.id, email=user.email, organization_id=user.organization_id)
    setattr(principal, claim, uuid4())
    with pytest.raises(HTTPException) as error:
        await preview_mcp_connection("internal", principal)
    assert error.value.status_code == 403
    assert redis.values == {}


async def test_callback_does_not_consume_state_or_create_code(redis):
    provider = BifrostAuthProvider(base_url="http://test")
    redis.values[_mcp_state_key("internal")] = "binding-must-survive"
    request = SimpleNamespace(query_params={"internal_state": "internal"}, cookies={})
    response = await provider._callback(request)
    assert response.status_code == 302
    assert response.headers["location"] == "http://test/connect-app?request=internal"
    assert redis.values == {_mcp_state_key("internal"): "binding-must-survive"}


async def test_code_round_trip_mints_tokens_for_the_signed_in_user(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[Any]
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")
    code = await _issue_code(provider, redis, user)

    key = _mcp_auth_code_key(code)
    stored_keys = list(redis.values)

    assert stored_keys == [key]
    assert code not in key

    status, body = await _exchange(provider, code)
    minted_for = [str(user_id) for user_id in minted]
    user_id = str(user.id)

    assert status == 200
    assert body["access_token"] == "minted.jwt"
    assert minted_for == [user_id]


async def test_code_is_single_use(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[Any]
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")
    code = await _issue_code(provider, redis, user)

    first_status, _ = await _exchange(provider, code)
    second_status, second = await _exchange(provider, code)
    minted_count = len(minted)

    assert first_status == 200
    assert second_status == 400
    assert second["error"] == "invalid_grant"
    assert minted_count == 1


async def test_code_cannot_be_redeemed_by_another_client(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[Any],
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")
    code = await _issue_code(provider, redis, user)

    status, body = await _exchange(provider, code, client_id="another-client")

    assert status == 400
    assert body["error"] == "invalid_grant"
    assert minted == []
    assert _mcp_auth_code_key(code) not in redis.values


async def test_record_under_a_key_not_derived_with_the_secret_is_rejected(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[Any]
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")
    code = "chosen-code"
    redis.values[f"bifrost:mcp:auth_code:{code}"] = json.dumps({
        "user_id": str(user.id),
        "email": user.email,
        "name": user.name,
        "code_challenge": _challenge(_VERIFIER),
        "redirect_uri": _REDIRECT_URI,
        "client_id": "client",
        "scope": "mcp:access",
    })

    status, body = await _exchange(provider, code)

    assert status == 400
    assert body["error"] == "invalid_grant"
    assert minted == []


async def test_record_with_altered_user_id_is_rejected(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[Any]
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")
    code = await _issue_code(provider, redis, user)
    key = _mcp_auth_code_key(code)
    stored = json.loads(redis.values[key])
    stored["record"]["user_id"] = str(uuid4())
    redis.values[key] = json.dumps(stored)

    status, body = await _exchange(provider, code)

    assert status == 400
    assert body["error"] == "invalid_grant"
    assert minted == []
    assert key not in redis.values


@pytest.mark.parametrize(
    "stored",
    [
        "not json {",
        json.dumps({"record": {"user_id": "someone"}, "tag": "\u00e9t\u00e9-not-a-hex-tag"}),
        '{"record": {}, "tag": "\\ud800"}',
        json.dumps({"record": {"user_id": "someone"}, "tag": "0" * 63}),
    ],
    ids=["invalid-json", "non-ascii-tag", "lone-surrogate-tag", "wrong-length-hex-tag"],
)
async def test_malformed_record_at_the_derived_key_is_rejected(
    stored: str, redis: _FakeRedis, minted: list[Any]
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")
    code = "issued-code"
    key = _mcp_auth_code_key(code)
    redis.values[key] = stored

    status, body = await _exchange(provider, code)

    assert status == 400
    assert body == {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"}
    assert minted == []
    assert key not in redis.values


async def test_unknown_code_is_rejected_as_before(
    redis: _FakeRedis, minted: list[Any]
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")

    status, body = await _exchange(provider, "never-issued")

    assert status == 400
    assert body == {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"}


def test_auth_code_key_depends_on_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    first = _mcp_auth_code_key("code")
    monkeypatch.setattr(get_settings(), "secret_key", "another-secret-value-of-at-least-32-chars")

    second = _mcp_auth_code_key("code")
    prefixed = second.startswith("bifrost:mcp:auth_code:")

    assert second != first
    assert prefixed
