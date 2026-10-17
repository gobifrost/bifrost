from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.core.auth import UserPrincipal
from src.core.pubsub import manager
from src.models.contracts.policies import TablePolicies
from src.routers import websocket as ws_mod


def _websocket_with_subscriptions(*table_ids: str) -> MagicMock:
    websocket = MagicMock()
    websocket.state = SimpleNamespace(
        table_subscriptions={table_id: {"filter": None} for table_id in table_ids},
    )
    websocket.send_json = AsyncMock()
    return websocket


def _user() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid.uuid4(),
        email="user@example.com",
        organization_id=uuid.uuid4(),
        is_superuser=False,
    )


async def test_table_invalidated_for_active_subscription_forwards_canonical_payload(monkeypatch):
    table_id = str(uuid.uuid4())
    websocket = _websocket_with_subscriptions(table_id)
    user = _user()
    monkeypatch.setattr(
        ws_mod, "_current_table_policy_user", AsyncMock(return_value=user)
    )
    monkeypatch.setattr(
        ws_mod, "_load_policies_for_table", AsyncMock(return_value=TablePolicies.model_validate({
            "policies": [{"name": "open-read", "actions": ["read"]}],
        }))
    )

    await ws_mod._handle_table_message(
        websocket,
        user,
        f"table:{table_id}",
        {"type": "table_invalidated", "table_id": "ignored", "extra": "ignored"},
    )

    websocket.send_json.assert_awaited_once_with({
        "type": "table_invalidated",
        "table_id": table_id,
    })
    ws_mod._load_policies_for_table.assert_awaited_once_with(table_id, user)


async def test_table_invalidated_without_active_subscription_does_not_send(monkeypatch):
    table_id = str(uuid.uuid4())
    websocket = MagicMock()
    websocket.state = SimpleNamespace(table_subscriptions={})
    websocket.send_json = AsyncMock()
    load_policies = AsyncMock()
    monkeypatch.setattr(ws_mod, "_load_policies_for_table", load_policies)

    await ws_mod._handle_table_message(
        websocket,
        _user(),
        f"table:{table_id}",
        {"type": "table_invalidated", "table_id": table_id},
    )

    websocket.send_json.assert_not_awaited()
    load_policies.assert_not_awaited()


async def test_basic_execution_reader_drops_diagnostics_without_losing_subscription(
    monkeypatch,
):
    execution_id = str(uuid.uuid4())
    channel = f"execution:{execution_id}"
    websocket = MagicMock()
    websocket.state = SimpleNamespace(table_subscriptions={})
    websocket.send_json = AsyncMock()
    user = _user()
    dispatcher = ws_mod._make_delivery_dispatcher(websocket, user)
    manager.connections[channel] = {websocket}
    monkeypatch.setattr(ws_mod, "can_access_execution", AsyncMock(return_value=True))
    diagnostics = AsyncMock(return_value=False)
    monkeypatch.setattr(ws_mod, "can_read_execution_diagnostics", diagnostics)

    try:
        await dispatcher(channel, {"type": "execution_log", "level": "DEBUG"})

        websocket.send_json.assert_not_awaited()
        assert websocket in manager.connections[channel]
        diagnostics.assert_awaited_once_with(user, execution_id)

        # The visible subscription remains active for non-diagnostic execution
        # updates after a sensitive row was omitted.
        await dispatcher(channel, {"type": "execution_log", "level": "INFO"})

        websocket.send_json.assert_awaited_once_with(
            {"type": "execution_log", "level": "INFO"}
        )
        diagnostics.assert_awaited_once()
    finally:
        manager.connections.pop(channel, None)


async def test_execution_diagnostics_reader_receives_debug_row(monkeypatch):
    execution_id = str(uuid.uuid4())
    channel = f"execution:{execution_id}"
    websocket = _websocket_with_subscriptions()
    manager.connections[channel] = {websocket}
    monkeypatch.setattr(ws_mod, "can_access_execution", AsyncMock(return_value=True))
    monkeypatch.setattr(
        ws_mod, "can_read_execution_diagnostics", AsyncMock(return_value=True)
    )

    try:
        await ws_mod._make_delivery_dispatcher(websocket, _user())(
            channel, {"type": "execution_log", "level": "TRACEBACK"}
        )

        websocket.send_json.assert_awaited_once_with(
            {"type": "execution_log", "level": "TRACEBACK"}
        )
    finally:
        manager.connections.pop(channel, None)


async def test_embed_session_never_receives_execution_diagnostics():
    user = _user()
    user.embed = True

    assert not await ws_mod.can_read_execution_diagnostics(user, str(uuid.uuid4()))
