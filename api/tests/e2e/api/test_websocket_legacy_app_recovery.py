"""Real socket/Redis/refresh recovery for an unchanged SDK2 query transport.

No deploy/build jobs: this test isolates the cross-process auth handshake. The
release image rehearsal separately proves the original compiled App artifact.
"""

import asyncio
import json
from urllib.parse import quote
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from shared.human_sessions import revoke_human_session
from src.core.security import decode_token
from tests.e2e.fixtures.setup import API_BASE_URL, _login_user

pytestmark = pytest.mark.e2e


async def _closed_without_data(ws, code):
    with pytest.raises(ConnectionClosedError) as closed:
        await asyncio.wait_for(ws.recv(), timeout=5)
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == code


async def test_legacy_app_refreshes_via_its_host_then_stops_after_session_revocation(
    e2e_ws_url, platform_admin, async_session_factory,
):
    _login_user(platform_admin)
    old_token = platform_admin.access_token
    assert old_token is not None
    ws_url = f"{e2e_ws_url}/ws/connect"
    origin = e2e_ws_url.replace("wss://", "https://").replace("ws://", "http://")
    try:
        async with (
            connect(ws_url, additional_headers={"Cookie": f"access_token={old_token}"}, origin=origin) as host,
            connect(f"{ws_url}?token={quote(old_token, safe='')}", origin=origin, additional_headers={"Sec-Fetch-Site": "same-origin"}) as legacy,
        ):
            assert json.loads(await host.recv())["type"] == "connected"
            assert json.loads(await legacy.recv())["type"] == "connected"
            async with async_session_factory() as db:
                await db.execute(text("SELECT authz_epoch_bump_once()"))
                await db.commit()
            # Only the old App sends input. The host has no pending protected
            # traffic: its4001 therefore proves the internal Redis request.
            await legacy.send(json.dumps({"type": "ping"}))
            await _closed_without_data(legacy, 1012)
            await _closed_without_data(host, 4001)

        async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
            response = await client.post("/auth/refresh", json={"refresh_token": platform_admin.refresh_token})
        assert response.status_code == 200
        tokens = response.json()
        platform_admin.set_tokens(tokens["access_token"], tokens["refresh_token"])
        fresh = tokens["access_token"]
        claims = decode_token(fresh, expected_type="access")
        assert claims is not None
        from src.services.authorization.tokens import _encode_claims, parse_human_claims

        typed = parse_human_claims(claims)
        assert typed is not None
        expired = _encode_claims(typed.model_copy(update={"exp": 1}))
        async with connect(ws_url, additional_headers={"Cookie": f"access_token={fresh}"}, origin=origin) as fresh_host:
            assert json.loads(await fresh_host.recv())["type"] == "connected"
            async with connect(f"{ws_url}?token={quote(expired, safe='')}", origin=origin, additional_headers={"Sec-Fetch-Site": "same-origin"}) as stale_retry:
                await _closed_without_data(stale_retry, 1012)
            # A stale query cannot force rotation of an already current host.
            await fresh_host.send(json.dumps({"type": "ping"}))
            assert json.loads(await fresh_host.recv())["type"] == "pong"
        async with connect(f"{ws_url}?token={quote(fresh, safe='')}", origin=origin, additional_headers={"Sec-Fetch-Site": "same-origin"}) as recovered:
            assert json.loads(await recovered.recv())["type"] == "connected"
            await recovered.send(json.dumps({"type": "ping"}))
            assert json.loads(await recovered.recv())["type"] == "pong"
            await revoke_human_session(UUID(claims["sub"]), UUID(claims["sid"]))
            await recovered.send(json.dumps({"type": "ping"}))
            await _closed_without_data(recovered, 4001)
    finally:
        _login_user(platform_admin)


@pytest.mark.parametrize("state", ["live", "revoked", "expired", "other_user", "inactive", "system", "identity"])
async def test_refresh_recovery_checks_the_current_durable_family(async_engine, monkeypatch, state):
    from contextlib import asynccontextmanager
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.orm.human_sessions import HumanAuthSession
    from src.services import websocket_auth_recovery as recovery
    from src.services.authorization.tokens import _encode_claims
    from tests.helpers.human_tokens import human_claims
    from tests.unit.authorization.committed_runs import committed_world

    @asynccontextmanager
    async def context():
        async with AsyncSession(async_engine) as db:
            yield db

    monkeypatch.setattr(recovery, "get_db_context", context)
    async with committed_world(async_engine) as world:
        org = await world.organization()
        user = await world.person(org)
        other = await world.person(org)
        now = datetime.now(timezone.utc)
        family = HumanAuthSession(
            id=uuid4(), user_id=other.id if state == "other_user" else user.id,
            current_jti_hash=uuid4().hex + uuid4().hex,
            expires_at=now + timedelta(days=-1 if state == "expired" else 1),
            revoked_at=now if state == "revoked" else None,
        )
        world.session.add(family)
        if state == "inactive":
            user.is_active = False
        if state == "system":
            user.is_system = True
        if state == "identity":
            user.identity_kind = "custom"
        await world.session.commit()
        # A signature-valid expired token can request a retry only while its
        # current family and human are still eligible; old signed flags cannot
        # override the durable rows above.
        claims = human_claims(user_id=user.id, organization_id=org.id).model_copy(update={"sid": family.id, "exp": 1})
        ws = SimpleNamespace(headers={}, query_params={}, cookies={"access_token": _encode_claims(claims)})
        result = await recovery.refreshable_websocket_session(ws)
        assert (result is not None) == (state == "live")
