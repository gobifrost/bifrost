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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from src.services.mcp_server.auth import (
    BifrostAuthProvider,
    _mcp_auth_code_key,
    _mcp_state_key,
)

_VERIFIER = "verifier-for-unit-tests"
_REDIRECT_URI = "http://client.example/cb"


class _FakeRedis:
    """In-memory stand-in for the redis.asyncio client calls the provider makes."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

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
        is_superuser=False,
        organization_id=uuid4(),
        identity_kind=None,
    )


@pytest.fixture
def redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def minted(user: SimpleNamespace, redis: _FakeRedis):
    """Patch the token endpoint's dependencies; record minted access-token data."""
    minted_tokens: list[dict[str, Any]] = []

    def create_access_token(data: dict, expires_delta: Any = None) -> str:
        minted_tokens.append(data)
        return "minted.jwt"

    @asynccontextmanager
    async def get_db_context():
        yield AsyncMock()

    users = MagicMock()
    users.get_by_id = AsyncMock(side_effect=lambda user_id: user if str(user.id) == str(user_id) else None)

    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=redis)),
        patch("src.core.security.create_access_token", side_effect=create_access_token),
        patch("src.core.security.create_refresh_token", return_value=("refresh.jwt", "jti")),
        patch("src.core.database.get_db_context", get_db_context),
        patch("src.repositories.users.UserRepository", return_value=users),
        patch("src.services.mcp_server.auth.resolve_external_claim", new=AsyncMock(return_value=False)),
        patch("src.services.mcp_server.auth.resolve_provider_org_claim", new=AsyncMock(return_value=False)),
    ):
        yield minted_tokens


async def _issue_code(provider: BifrostAuthProvider, redis: _FakeRedis, user: SimpleNamespace) -> str:
    """Run the login callback for ``user`` and return the issued code."""
    redis.values[_mcp_state_key("internal")] = json.dumps({
        "client_id": "client",
        "redirect_uri": _REDIRECT_URI,
        "state": "client-state",
        "code_challenge": _challenge(_VERIFIER),
        "scope": "mcp:access",
    })
    request = SimpleNamespace(
        query_params={"internal_state": "internal"},
        cookies={"access_token": "session.jwt"},
        headers={"accept": "application/json"},
    )
    payload = {"sub": str(user.id), "email": user.email, "name": user.name, "org_id": str(user.organization_id)}
    with (
        patch("src.core.security.decode_token", return_value=payload),
        patch.object(provider, "_check_mcp_access", new=AsyncMock(return_value=True)),
    ):
        response = await provider._callback(request)  # type: ignore[arg-type]
    redirect_url = json.loads(bytes(response.body))["redirect_url"]
    return parse_qs(urlsplit(redirect_url).query)["code"][0]


async def _exchange(provider: BifrostAuthProvider, code: str) -> tuple[int, dict[str, Any]]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _REDIRECT_URI,
        "code_verifier": _VERIFIER,
        "client_id": "client",
    }
    request = SimpleNamespace(form=AsyncMock(return_value=form))
    response = await provider._token(request)  # type: ignore[arg-type]
    return response.status_code, json.loads(bytes(response.body))


async def test_code_round_trip_mints_tokens_for_the_signed_in_user(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[dict[str, Any]]
) -> None:
    provider = BifrostAuthProvider(base_url="http://test")
    code = await _issue_code(provider, redis, user)

    key = _mcp_auth_code_key(code)
    stored_keys = list(redis.values)

    assert stored_keys == [key]
    assert code not in key

    status, body = await _exchange(provider, code)
    minted_for = [token["sub"] for token in minted]
    user_id = str(user.id)

    assert status == 200
    assert body["access_token"] == "minted.jwt"
    assert minted_for == [user_id]


async def test_code_is_single_use(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[dict[str, Any]]
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


async def test_record_under_a_key_not_derived_with_the_secret_is_rejected(
    user: SimpleNamespace, redis: _FakeRedis, minted: list[dict[str, Any]]
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
    user: SimpleNamespace, redis: _FakeRedis, minted: list[dict[str, Any]]
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
    ],
    ids=["invalid-json", "non-ascii-tag"],
)
async def test_malformed_record_at_the_derived_key_is_rejected(
    stored: str, redis: _FakeRedis, minted: list[dict[str, Any]]
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
    redis: _FakeRedis, minted: list[dict[str, Any]]
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
