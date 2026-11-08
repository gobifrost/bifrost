"""Immutable App recovery routes only a refresh request, never protected data."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from src.core.auth import select_websocket_auth_token
from src.services import websocket_auth_recovery as recovery
from src.services.authorization.tokens import RuntimeAuthResult, _encode_claims, _human_principal
from tests.helpers.human_tokens import human_claims


def socket(token=None, *, query=False, headers=None, cookies=None):
    socket_headers = {"host": "bifrost.test", "origin": "https://bifrost.test", **(headers or {})}
    if token and not query:
        socket_headers["cookie"] = f"access_token={token}"
    if cookies:
        socket_headers["cookie"] = cookies
    ws = WebSocket({
        "type": "websocket", "scheme": "wss", "path": "/ws/connect",
        "query_string": f"token={token}".encode() if query and token else b"",
        "headers": [(k.encode(), v.encode()) for k, v in socket_headers.items()],
        "server": ("bifrost.test", 443), "client": ("127.0.0.1", 1234),
    }, receive=AsyncMock(), send=AsyncMock())
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def authority(monkeypatch):
    claims = human_claims(organization_id=uuid4())
    db = SimpleNamespace(scalar=AsyncMock(return_value=claims.sid))

    @asynccontextmanager
    async def context():
        yield db

    authenticate = AsyncMock(return_value=RuntimeAuthResult(None, "refresh_required"))
    monkeypatch.setattr(recovery, "get_db_context", context)
    monkeypatch.setattr(recovery, "authenticate_human_token", authenticate)
    monkeypatch.setattr(recovery.manager, "broadcast", AsyncMock())
    return claims, db, authenticate


@pytest.mark.parametrize("expired", [False, True])
async def test_only_live_human_family_can_request_refresh(authority, expired):
    claims, db, authenticate = authority
    if expired:
        claims = claims.model_copy(update={"exp": 1})
        authenticate.return_value = RuntimeAuthResult(_human_principal(claims))
    assert await recovery.refreshable_websocket_session(socket(_encode_claims(claims))) == recovery.RefreshSession(
        claims.sub, claims.sid,
    )
    db.scalar.assert_awaited_once()


@pytest.mark.parametrize("reason", ["authority_state_missing", "not_live"])
async def test_missing_authority_never_recovers_even_expired(authority, reason):
    claims, db, authenticate = authority
    authenticate.return_value = RuntimeAuthResult(None, reason)
    claims = claims.model_copy(update={"exp": 1})
    assert await recovery.refreshable_websocket_session(socket(_encode_claims(claims))) is None
    db.scalar.assert_not_awaited()


async def test_valid_credential_and_ended_family_never_request_refresh(authority):
    claims, db, authenticate = authority
    ws = socket(_encode_claims(claims))
    authenticate.return_value = RuntimeAuthResult(_human_principal(claims))
    assert await recovery.refreshable_websocket_session(ws) is None
    db.scalar.assert_not_awaited()
    authenticate.return_value = RuntimeAuthResult(None, "refresh_required")
    db.scalar.return_value = None
    assert await recovery.refreshable_websocket_session(ws) is None


@pytest.mark.parametrize("token", [None, "invalid.token", ""])
async def test_invalid_credentials_do_not_query_or_publish(authority, token):
    _, db, authenticate = authority
    assert not await recovery.request_legacy_websocket_refresh(socket(token, query=True))
    authenticate.assert_not_awaited()
    db.scalar.assert_not_awaited()
    recovery.manager.broadcast.assert_not_awaited()


async def test_legacy_query_retries_via_shared_bus_without_serializing_token(authority):
    claims, _, _ = authority
    token = _encode_claims(claims)
    assert await recovery.request_legacy_websocket_refresh(socket(token, query=True))
    recovery.manager.broadcast.assert_awaited_once_with(f"user:{claims.sub}", {
        "type": recovery._AUTH_REFRESH_REQUEST, "session_id": str(claims.sid),
    })
    assert token not in repr(recovery.manager.broadcast.call_args)


@pytest.mark.parametrize("origin", ["https://other.test", "https://bifrost.test:444", "null", "", "https://bifrost.test/path", "https://user@bifrost.test", "https://bifrost.test#fragment", "https://bifrost.test:bad"])
async def test_cross_origin_or_malformed_query_does_not_request_host_refresh(authority, origin):
    claims, db, _ = authority
    # A synthetic Fetch Metadata header cannot override the real browser Origin.
    assert not await recovery.request_legacy_websocket_refresh(socket(_encode_claims(claims), query=True, headers={"origin": origin, "sec-fetch-site": "same-origin"}))
    db.scalar.assert_not_awaited()


@pytest.mark.parametrize(("origin", "host"), [
    ("https://bifrost.test", "bifrost.test:443"),
    ("http://bifrost.test:80", "bifrost.test"),
    ("http://localhost:31234", "localhost:31234"),
    ("https://bifrost.test:8443", "bifrost.test:8443"),
    ("http://[::1]:31234", "[::1]:31234"),
])
async def test_browser_without_fetch_metadata_recovers_with_public_authority(authority, origin, host):
    claims, _, _ = authority
    ws = socket(_encode_claims(claims), query=True, headers={"origin": origin, "host": host})
    assert "sec-fetch-site" not in ws.headers
    assert await recovery.request_legacy_websocket_refresh(ws)
    recovery.manager.broadcast.assert_awaited_once()


async def test_internal_control_closes_only_matching_stale_cookie_host(authority):
    claims, _, authenticate = authority
    ws = socket(_encode_claims(claims))
    event = {"type": recovery._AUTH_REFRESH_REQUEST, "session_id": str(claims.sid)}
    with pytest.raises(WebSocketDisconnect) as closed:
        await recovery.consume_auth_refresh_request(ws, f"user:{claims.sub}", event)
    assert closed.value.code == 4001
    ws.close.assert_awaited_once_with(code=4001, reason="Authentication refresh required")
    ws.close.reset_mock()
    authenticate.return_value = RuntimeAuthResult(_human_principal(claims))
    assert await recovery.consume_auth_refresh_request(ws, f"user:{claims.sub}", event)
    ws.close.assert_not_awaited()


@pytest.mark.parametrize("case", ["query", "other_session", "other_user", "impersonation"])
async def test_internal_control_is_consumed_without_affecting_other_sessions(authority, case):
    claims, _, authenticate = authority
    ws = socket(_encode_claims(claims), query=case == "query")
    if case == "impersonation":
        from shared.browser_impersonation import IMPERSONATION_COOKIE_NAME
        ws = socket(_encode_claims(claims), cookies=f"{IMPERSONATION_COOKIE_NAME}=invalid")
    event = {"type": recovery._AUTH_REFRESH_REQUEST, "session_id": str(uuid4()) if case == "other_session" else str(claims.sid)}
    channel = f"user:{uuid4() if case == 'other_user' else claims.sub}"
    assert await recovery.consume_auth_refresh_request(ws, channel, event)
    ws.close.assert_not_awaited()
    authenticate.assert_not_awaited()


async def test_ordinary_payload_is_not_an_internal_control(authority):
    claims, _, authenticate = authority
    assert not await recovery.consume_auth_refresh_request(socket(_encode_claims(claims)), f"user:{claims.sub}", {"type": "notification"})
    authenticate.assert_not_awaited()


def test_selector_keeps_explicit_and_impersonation_precedence():
    claims = human_claims(organization_id=uuid4())
    token = _encode_claims(claims)
    ws = socket(token, query=True, headers={"authorization": "Bearer explicit"})
    assert select_websocket_auth_token(ws) == "explicit"
    # Use the canonical marker name; an ordinary cookie must not replace query auth.
    from shared.browser_impersonation import IMPERSONATION_COOKIE_NAME
    ws = socket(token, query=True, cookies=f"{IMPERSONATION_COOKIE_NAME}=marker")
    assert select_websocket_auth_token(ws) == "marker"


async def test_router_never_delivers_internal_requests_as_user_data(authority):
    from src.routers import websocket

    claims, _, _ = authority
    ws = socket(_encode_claims(claims), query=True)
    ws.send_json = AsyncMock()
    user = _human_principal(claims)
    dispatch = websocket._make_delivery_dispatcher(ws, user)
    await dispatch(f"user:{claims.sub}", {"type": recovery._AUTH_REFRESH_REQUEST, "session_id": str(claims.sid)})
    ws.send_json.assert_not_awaited()
    ws.close.assert_not_awaited()


@pytest.mark.parametrize("kind", ["no_session", "runtime", "embed"])
async def test_scoped_or_sessionless_tokens_never_borrow_human_refresh(authority, kind):
    from src.core.security import create_embed_access_token
    from src.services.authorization.tokens import AccessTokenClaims, RunClaims

    claims, db, authenticate = authority
    if kind == "embed":
        token = create_embed_access_token(embed_kind="app", grant="public", resource_id=str(uuid4()), org_id=None)
    elif kind == "runtime":
        payload = claims.model_dump(exclude={"sid"})
        runtime = AccessTokenClaims(**payload, credential_kind="workflow_execution", run=RunClaims())
        token = _encode_claims(runtime)
    else:
        token = _encode_claims(claims.model_copy(update={"sid": None}))
    assert not await recovery.request_legacy_websocket_refresh(socket(token, query=True))
    authenticate.assert_not_awaited()
    db.scalar.assert_not_awaited()
    recovery.manager.broadcast.assert_not_awaited()
