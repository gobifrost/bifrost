"""App consent binds the request to its client, callback, and approving human."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.app_connections import (
    OAuthConnectionError, begin_mcp_connection, decide_app_connection,
    preview_mcp_connection, register_mcp_client,
)
from shared.mcp_oauth import _mcp_state_key, redirect_matches, validate_redirect_uri
from src.core.principal import UserPrincipal


class Redis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def connection():
    redis = Redis()
    user = UserPrincipal(user_id=uuid4(), email="user@example.com", organization_id=uuid4(), auth_session_id=uuid4())
    config = SimpleNamespace(enabled=True)
    clients = {}
    db = AsyncMock()
    db.add = lambda row: clients.update({row.client_id: row})
    db.get.side_effect = lambda model, key: clients.get(key)
    async def import_row(statement):
        from src.models.orm.oauth_clients import OAuthClientRegistration
        params = statement.compile().params
        clients.setdefault(params["client_id"], OAuthClientRegistration(**params))
    db.execute.side_effect = import_row
    @asynccontextmanager
    async def db_context():
        yield db
    with (
        patch("shared.app_connections.get_shared_redis", new=AsyncMock(return_value=redis)),
        patch("shared.app_connections.get_db_context", db_context),
        patch("shared.app_connections.get_mcp_config_cached", new=AsyncMock(return_value=config)),
    ):
        yield redis, user, config


async def pending(name="Example App", uri="https://app.example/cb?existing=value"):
    registration = await register_mcp_client({"client_name": name, "redirect_uris": [uri]})
    return await begin_mcp_connection(registration["client_id"], uri, "state & value", "challenge", "mcp:access")


async def test_opening_request_never_issues_code_and_explicit_approval_is_once(connection):
    redis, user, _ = connection
    state = await pending()
    details = await preview_mcp_connection(state, user)
    assert not any(":auth_code:" in key for key in redis.values)
    assert _mcp_state_key(state) in redis.values
    result = await decide_app_connection("mcp", state, user, True, details.consent_token)
    query = parse_qs(urlsplit(result.redirect_url).query)
    assert query["existing"] == ["value"]
    assert query["state"] == ["state & value"]
    assert query["code"]
    assert _mcp_state_key(state) not in redis.values
    with pytest.raises(HTTPException):
        await decide_app_connection("mcp", state, user, True, details.consent_token)


async def test_deny_issues_no_code_and_cannot_be_reversed(connection):
    redis, user, _ = connection
    state = await pending()
    details = await preview_mcp_connection(state, user)
    result = await decide_app_connection("mcp", state, user, False, details.consent_token)
    assert parse_qs(urlsplit(result.redirect_url).query)["error"] == ["access_denied"]
    assert not any(":auth_code:" in key for key in redis.values)
    with pytest.raises(HTTPException):
        await decide_app_connection("mcp", state, user, True, details.consent_token)


@pytest.mark.parametrize("changed", ["user_id", "auth_session_id"])
async def test_consent_cannot_be_transferred_to_another_user_or_session(connection, changed):
    redis, user, _ = connection
    state = await pending()
    details = await preview_mcp_connection(state, user)
    setattr(user, changed, uuid4())
    with pytest.raises(HTTPException) as error:
        await decide_app_connection("mcp", state, user, True, details.consent_token)
    assert error.value.status_code == 403
    assert _mcp_state_key(state) in redis.values


async def test_disabled_mcp_cannot_consume_previously_reviewed_request(connection):
    redis, user, config = connection
    state = await pending()
    details = await preview_mcp_connection(state, user)
    config.enabled = False
    with pytest.raises(HTTPException) as error:
        await decide_app_connection("mcp", state, user, True, details.consent_token)
    assert error.value.status_code == 403
    assert _mcp_state_key(state) in redis.values


@pytest.mark.parametrize("name", [None, "", "  ", "Example App"])
async def test_unnamed_clients_connect_with_clear_display_name(connection, name):
    _, user, _ = connection
    state = await pending(name)
    assert (await preview_mcp_connection(state, user)).client_name == (name.strip() if name and name.strip() else "Unnamed App")


async def test_missing_name_preserves_registration_default(connection):
    client = await register_mcp_client({"redirect_uris": ["claude://oauth/callback"]})
    assert client["client_name"] == "MCP Client"


async def test_unknown_or_expired_client_is_rejected_before_state_creation(connection):
    redis, _, _ = connection
    with pytest.raises(OAuthConnectionError) as error:
        await begin_mcp_connection("expired-client", "https://app.example/cb", "state", "challenge", "mcp:access")
    assert error.value.payload["error"] == "invalid_client"
    assert redis.values == {}


async def test_wrong_callback_is_rejected_without_creating_state(connection):
    redis, _, _ = connection
    client = await register_mcp_client({"redirect_uris": ["https://app.example/cb"]})
    with pytest.raises(OAuthConnectionError):
        await begin_mcp_connection(client["client_id"], "https://attacker.example/cb", "state", "challenge", "mcp:access")
    assert not any(":state:" in key for key in redis.values)


async def test_tampered_pending_state_cannot_gain_approval(connection):
    redis, user, _ = connection
    state = await pending()
    raw = json.loads(redis.values[_mcp_state_key(state)])
    raw["record"]["redirect_uri"] = "https://attacker.example/cb"
    redis.values[_mcp_state_key(state)] = json.dumps(raw)
    with pytest.raises(HTTPException):
        await preview_mcp_connection(state, user)


@pytest.mark.parametrize("requested,registered,expected", [
    ("https://app.example/cb", "https://app.example/cb", True),
    ("https://app.example/other", "https://app.example/cb", False),
    ("https://app.example:444/cb", "https://app.example:443/cb", False),
    ("http://127.0.0.1:4000/cb?q=1", "http://127.0.0.1:8000/cb?q=1", True),
    ("http://[::1]:4000/cb", "http://[::1]:8000/cb", True),
    ("http://localhost:4000/cb", "http://localhost:8000/cb", True),
    ("http://127.0.0.1:4000/other", "http://127.0.0.1:8000/cb", False),
    ("http://127.0.0.1:4000/cb", "http://localhost:8000/cb", False),
    ("claude://oauth/cb", "claude://oauth/cb", True),
    ("claude://oauth/other", "claude://oauth/cb", False),
])
def test_callback_matching_preserves_only_standard_loopback_port_variance(requested, registered, expected):
    assert redirect_matches(requested, registered) is expected


@pytest.mark.parametrize("uri", ["javascript:alert(1)", "https://user:pass@app.example/cb", "https://app.example/cb#fragment", "https://app.example/cb#", "http://public.example/cb", "https://app.example/\ncb", "https://app.example:99999/cb"])
def test_unsafe_callbacks_cannot_be_registered(uri):
    with pytest.raises(ValueError):
        validate_redirect_uri(uri)


async def test_existing_redis_registration_is_imported_once_without_changing_its_id(connection):
    redis, user, _ = connection
    key = "bifrost:mcp:client:existing-id"
    redis.values[key] = json.dumps({"client_name": "Existing Client", "redirect_uris": ["claude://oauth/callback"]})
    state = await begin_mcp_connection("existing-id", "claude://oauth/callback", "state", "challenge", "mcp:access")
    assert (await preview_mcp_connection(state, user)).client_name == "Existing Client"
    assert key not in redis.values
    # Subsequent authorizations survive removal of the old Redis metadata.
    await begin_mcp_connection("existing-id", "claude://oauth/callback", "state-2", "challenge", "mcp:access")


@pytest.mark.parametrize("cookie", [None, "invalid", "wrong-user", "wrong-session", "revoked"])
async def test_access_token_alone_or_wrong_browser_cannot_authorize_a_new_session(connection, cookie):
    from shared.app_connections import require_browser_app_approver
    _, user, _ = connection
    claims = {"sub": str(user.user_id), "sid": str(user.auth_session_id), "jti": "current"}
    if cookie == "wrong-user":
        claims["sub"] = str(uuid4())
    if cookie == "wrong-session":
        claims["sid"] = str(uuid4())
    request = SimpleNamespace(cookies={"refresh_token": cookie} if cookie else {})
    with (
        patch("shared.app_connections.decode_token", return_value=None if cookie == "invalid" else claims),
        patch("shared.app_connections.has_live_refresh_token_jti", new=AsyncMock(return_value=cookie != "revoked")),
    ):
        with pytest.raises(HTTPException) as error:
            await require_browser_app_approver(request, user)
        assert error.value.status_code == 403


async def test_matching_live_browser_session_can_review_and_decide(connection):
    from shared.app_connections import require_browser_app_approver
    _, user, _ = connection
    request = SimpleNamespace(cookies={"refresh_token": "browser-refresh"})
    claims = {"sub": str(user.user_id), "sid": str(user.auth_session_id), "jti": "current"}
    with (
        patch("shared.app_connections.decode_token", return_value=claims),
        patch("shared.app_connections.has_live_refresh_token_jti", new=AsyncMock(return_value=True)),
    ):
        await require_browser_app_approver(request, user)
