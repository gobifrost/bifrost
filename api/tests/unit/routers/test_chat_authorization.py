"""Authorization boundaries for the personal Chat surface."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.routers import chat, websocket


async def test_chat_route_requires_chat_execute_at_original_home_organization(monkeypatch):
    home_organization_id = uuid4()
    caller = SimpleNamespace(
        ctx=SimpleNamespace(
            user_id=uuid4(),
            home_organization_id=home_organization_id,
        )
    )
    target = object()
    request = SimpleNamespace(
        method="GET",
        scope={"route": SimpleNamespace(path="/api/chat/conversations")},
    )
    authorize_operation = AsyncMock()
    monkeypatch.setattr(chat, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(chat, "authorize_operation", authorize_operation)
    monkeypatch.setattr(chat, "org_target", MagicMock(return_value=target))

    db = AsyncMock()
    user = SimpleNamespace()
    await chat._authorize_chat_route(request, db, user)

    chat.authorize_operation.assert_awaited_once_with(
        db,
        user,
        "GET /api/chat/conversations",
        target,
    )
    chat.org_target.assert_called_once_with(home_organization_id)


async def test_chat_caller_keeps_original_user_as_conversation_owner(monkeypatch):
    owner_id = uuid4()
    caller = SimpleNamespace(ctx=SimpleNamespace(user_id=owner_id))
    monkeypatch.setattr(chat, "load_caller", AsyncMock(return_value=caller))

    actual_caller, actual_owner_id = await chat._chat_caller(AsyncMock(), SimpleNamespace())

    assert actual_caller is caller
    assert actual_owner_id == owner_id


async def test_chat_model_profiles_uses_chat_permission_gate(monkeypatch):
    class PermissionDeniedForTest(Exception):
        pass

    denied = PermissionDeniedForTest()
    caller = AsyncMock(side_effect=denied)
    monkeypatch.setattr(chat, "_chat_caller", caller)

    with pytest.raises(PermissionDeniedForTest) as error:
        await chat.get_model_profiles(AsyncMock(), SimpleNamespace())

    assert error.value is denied
    caller.assert_awaited_once()


async def test_chat_socket_subscription_requires_chat_permission_and_conversation_ownership(
    monkeypatch,
):
    home_organization_id = uuid4()
    owner_id = uuid4()
    conversation_id = uuid4()
    caller = SimpleNamespace(
        ctx=SimpleNamespace(
            user_id=owner_id,
            home_organization_id=home_organization_id,
        )
    )
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(is_active=True, user_id=owner_id)

    @asynccontextmanager
    async def database():
        yield db

    allows_permission = MagicMock(return_value=False)
    monkeypatch.setattr(websocket, "get_db_context", database)
    monkeypatch.setattr(websocket, "_current_ws_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(websocket, "allows_permission", allows_permission)

    assert not await websocket.can_access_conversation_current(
        SimpleNamespace(), str(conversation_id)
    )
    allows_permission.assert_called_once_with(
        caller, "chat.execute", home_organization_id
    )

    allows_permission.reset_mock()
    allows_permission.return_value = True
    assert await websocket.can_access_conversation_current(
        SimpleNamespace(), str(conversation_id)
    )
    allows_permission.assert_called_once_with(
        caller, "chat.execute", home_organization_id
    )

    allows_permission.reset_mock()
    db.get.return_value = SimpleNamespace(is_active=True, user_id=uuid4())
    assert not await websocket.can_access_conversation_current(
        SimpleNamespace(), str(conversation_id)
    )
    allows_permission.assert_not_called()
