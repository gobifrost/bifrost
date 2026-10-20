from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

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


async def test_table_filter_drop_keeps_subscription_for_later_visible_row(monkeypatch):
    table_id = str(uuid.uuid4())
    channel = f"table:{table_id}"
    websocket = _websocket_with_subscriptions(table_id)
    user = _user()
    dispatcher = ws_mod._make_delivery_dispatcher(websocket, user)
    manager.connections[channel] = {websocket}

    async def filter_and_deliver(ws, _user, _channel, payload):
        if payload["status"] == "open":
            await ws.send_json({"type": "document_change", "status": "open"})
        return True

    handler = AsyncMock(side_effect=filter_and_deliver)
    monkeypatch.setattr(ws_mod, "_handle_table_message", handler)

    try:
        await dispatcher(channel, {"status": "done"})

        websocket.send_json.assert_not_awaited()
        assert websocket in manager.connections[channel]

        await dispatcher(channel, {"status": "open"})

        handler.assert_has_awaits([
            call(websocket, user, channel, {"status": "done"}),
            call(websocket, user, channel, {"status": "open"}),
        ])
        websocket.send_json.assert_awaited_once_with(
            {"type": "document_change", "status": "open"}
        )
        assert websocket in manager.connections[channel]
    finally:
        manager.connections.pop(channel, None)


async def test_file_prefix_revocation_keeps_other_prefix_subscribed(monkeypatch):
    location = f"location-{uuid.uuid4()}"
    channel = f"files:{location}:shared"
    blocked_channel = f"files:{location}:blocked"
    permitted_channel = f"files:{location}:permitted"
    organization_id = uuid.uuid4()
    websocket = _websocket_with_subscriptions()
    websocket.state.file_subscriptions = {
        f"{channel}:blocked": {
            "channel_name": channel,
            "requested_channel": blocked_channel,
            "location": location,
            "scope": "shared",
            "organization_id": organization_id,
            "prefix": "blocked",
        },
        f"{channel}:permitted": {
            "channel_name": channel,
            "requested_channel": permitted_channel,
            "location": location,
            "scope": "shared",
            "organization_id": organization_id,
            "prefix": "permitted",
        },
    }
    manager.connections[channel] = {websocket}
    monkeypatch.setattr(
        ws_mod, "_file_has_applicable_policy", AsyncMock(return_value=True)
    )

    async def file_allowed(*, path, **_kwargs):
        return path != "blocked"

    monkeypatch.setattr(ws_mod, "_file_allowed", AsyncMock(side_effect=file_allowed))
    dispatcher = ws_mod._make_delivery_dispatcher(websocket, _user())

    try:
        await dispatcher(channel, {"type": "file_policy_changed"})

        assert websocket in manager.connections[channel]
        assert set(websocket.state.file_subscriptions) == {f"{channel}:permitted"}
        websocket.send_json.assert_awaited_once_with({
            "type": "subscription_revoked",
            "channel": blocked_channel,
        })

        await dispatcher(channel, {
            "type": "file_change",
            "location": location,
            "scope": "shared",
            "path": "permitted/report.txt",
            "action": "write",
        })

        websocket.send_json.assert_has_awaits([
            call({"type": "subscription_revoked", "channel": blocked_channel}),
            call({
                "type": "file_change",
                "channel": permitted_channel,
                "location": location,
                "scope": "shared",
                "path": "permitted/report.txt",
                "action": "write",
            }),
        ])
        assert websocket.send_json.await_count == 2
        assert websocket in manager.connections[channel]
    finally:
        manager.connections.pop(channel, None)


async def test_genuine_delivery_denial_removes_subscription(monkeypatch):
    execution_id = str(uuid.uuid4())
    channel = f"execution:{execution_id}"
    websocket = _websocket_with_subscriptions()
    manager.connections[channel] = {websocket}
    monkeypatch.setattr(ws_mod, "can_access_execution", AsyncMock(return_value=False))

    try:
        await ws_mod._make_delivery_dispatcher(websocket, _user())(
            channel, {"type": "execution_status"}
        )

        websocket.send_json.assert_not_awaited()
        assert channel not in manager.connections
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


async def test_revoked_impersonation_cannot_deliver_existing_subscription(monkeypatch):
    from contextlib import asynccontextmanager
    from fastapi import WebSocketDisconnect
    import pytest
    from shared.browser_impersonation import BrowserImpersonationError

    user = _user()
    user.impersonated_by_user_id = uuid.uuid4()
    user.impersonation_session_id = uuid.uuid4()
    websocket = _websocket_with_subscriptions()
    websocket.close = AsyncMock()

    @asynccontextmanager
    async def database():
        yield MagicMock()

    monkeypatch.setattr(ws_mod, "get_db_context", database)
    validate = AsyncMock(side_effect=BrowserImpersonationError("revoked"))
    monkeypatch.setattr(ws_mod, "validate_browser_impersonation", validate)
    allowed = AsyncMock(return_value=True)
    monkeypatch.setattr(ws_mod, "_delivery_allowed", allowed)

    with pytest.raises(WebSocketDisconnect):
        await ws_mod._make_delivery_dispatcher(websocket, user)(
            f"user:{user.user_id}", {"type": "notification"},
        )

    validate.assert_awaited_once()
    allowed.assert_not_awaited()
    websocket.send_json.assert_not_awaited()
    websocket.close.assert_awaited_once_with(code=4001, reason="Impersonation session ended")


def test_websocket_audit_and_access_checks_retain_operator():
    from shared import access_checks

    user = _user()
    user.impersonated_by_user_id = uuid.uuid4()
    user.impersonation_session_id = uuid.uuid4()
    actor = ws_mod._websocket_actor(MagicMock(), user)
    assert actor.impersonated_by_user_id == user.impersonated_by_user_id
    assert actor.impersonation_session_id == user.impersonation_session_id
    token = ws_mod._collect_websocket_checks(user)
    try:
        collector = access_checks.current()
        assert collector is not None
        assert collector.run_user_id == user.user_id
        assert collector.impersonated_by_user_id == user.impersonated_by_user_id
        assert collector.impersonation_session_id == user.impersonation_session_id
    finally:
        access_checks.stop_collecting(token)
