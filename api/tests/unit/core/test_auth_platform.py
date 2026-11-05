"""Human HTTP and WebSocket auth requires exact, epoch-current claims.

Legacy user-supplied admin/org flags are no longer a session format. Database
mint/parity and eligibility are covered by authorization/test_human_tokens.py.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from src.core.auth import get_current_user_optional, get_current_user_ws
from src.core.security import create_access_token
from src.services.authorization.tokens import _encode_claims
from tests.helpers.human_tokens import human_claims


@pytest.mark.parametrize('admin,organization', [(True, None), (True, 'org'), (False, 'org')])
async def test_http_human_identity_and_context_come_from_current_claims(admin, organization):
    org = uuid4() if organization else None
    claims = human_claims(admin=admin, organization_id=org, provider=True)
    request = MagicMock(spec=Request, cookies={})
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = int(claims.av)
    db.execute.return_value = result
    credentials = HTTPAuthorizationCredentials(scheme='Bearer', credentials=_encode_claims(claims))
    principal = await get_current_user_optional(request, credentials, db)
    assert principal is not None
    assert principal.user_id == claims.sub and principal.organization_id == org
    assert principal.is_platform_admin is admin
    assert principal.is_provider_org
    assert principal.authorization_context is not None
    db.execute.assert_awaited_once()


async def test_legacy_human_flags_cannot_authenticate_http_or_websocket():
    token = create_access_token({'sub': str(uuid4()), 'email': 'engine@example.test', 'is_superuser': True})
    credentials = HTTPAuthorizationCredentials(scheme='Bearer', credentials=token)
    assert await get_current_user_optional(MagicMock(spec=Request, cookies={}), credentials, AsyncMock()) is None
    socket = SimpleNamespace(headers={'authorization': f'Bearer {token}'}, query_params={}, cookies={})
    assert await get_current_user_ws(socket) is None


async def test_websocket_uses_epoch_validation_and_preserves_explicit_bearer_precedence():
    claims = human_claims(organization_id=uuid4())
    token = _encode_claims(claims)
    socket = SimpleNamespace(headers={'authorization': f'Bearer {token}'}, query_params={}, cookies={'access_token': 'invalid'})
    from src.services.authorization.tokens import RuntimeAuthResult
    expected = SimpleNamespace(user_id=claims.sub)
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def db_context():
        yield AsyncMock()
    with patch('src.core.database.get_db_context', db_context), patch(
        'src.core.auth.authenticate_human_token', AsyncMock(return_value=RuntimeAuthResult(expected))
    ) as authenticate:
        assert await get_current_user_ws(socket) is expected
        authenticate.assert_awaited_once()


async def test_eligibility_change_during_issuance_returns_auth_refusal_without_a_replay_challenge():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from shared.human_sessions import HumanSessionUnavailable
    from src.core.app_wiring import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    @app.post('/login')
    async def login():
        raise HumanSessionUnavailable('Account was disabled after the password check')

    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        response = await client.post('/login')
    assert response.status_code == 401
    assert response.headers['WWW-Authenticate'] == 'Bearer'
