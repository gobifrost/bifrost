"""Epoch-validated socket humans reuse the same signed role facts as HTTP."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.routers import websocket
from src.services.authorization.tokens import _human_principal
from tests.helpers.human_tokens import human_claims


async def test_human_socket_resource_checks_do_not_reload_role_authority(monkeypatch):
    principal = _human_principal(human_claims(organization_id=uuid4()))
    db = AsyncMock()
    refresh = AsyncMock(side_effect=AssertionError("human role reload"))
    monkeypatch.setattr(websocket, "refresh_caller", refresh)

    caller = await websocket._current_ws_caller(db, principal)

    assert caller is not None and caller.ctx is principal.authorization_context
    db.execute.assert_not_awaited()
    refresh.assert_not_awaited()


async def test_human_table_policy_uses_signed_roles_and_fresh_custom_claims(monkeypatch):
    principal = _human_principal(human_claims(organization_id=uuid4(), admin=True))
    setattr(principal, "claims", {"previous_table": "must not cross the policy boundary"})
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(organization_id=principal.organization_id)

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(websocket, "get_db_context", context)

    policy_user = await websocket._current_table_policy_user(principal, str(uuid4()))

    assert policy_user is not None and policy_user is not principal
    assert policy_user.role_ids == principal.role_ids
    assert policy_user.role_names == principal.role_names
    assert policy_user.roles == principal.role_names
    assert getattr(policy_user, "claims") == {}
    assert getattr(principal, "claims") == {"previous_table": "must not cross the policy boundary"}
    db.get.assert_awaited_once()
    db.execute.assert_not_awaited()
