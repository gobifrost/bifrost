"""Actor-scoped WebSocket surface for generated private Solution apps.

This router is mounted only by ``src.app_host``.  A Solution-app token can
subscribe to three data-plane channel families, each re-authorized against the
token's exact live Solution binding:

* ``table:{name-or-id}`` for a table owned by the Solution;
* ``files:{location}:{prefix}`` for a declared Solution file location;
* ``execution:{id}`` for an execution started by this exact actor session.

Every other channel is denied.  In particular, none of the control-plane,
chat, notification, or broad user channels supported by the normal API
WebSocket router are reachable from generated code.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select

from shared.policies.evaluate import evaluate
from src.core.app_actor import (
    SolutionAppPrincipal,
    authenticate_solution_app_token,
)
from src.core.database import get_db_context
from src.core.pubsub import manager
from src.models.contracts.policies import Expr
from src.models.orm.executions import Execution
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.routers.solution_app_runtime import get_solution_app_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["solution-app-websocket"])


async def _reject(websocket: WebSocket, code: int = 4001) -> None:
    await websocket.accept()
    await websocket.close(code=code)


async def _resolve_table(
    principal: SolutionAppPrincipal,
    name_or_id: str,
) -> UUID | None:
    async with get_db_context() as db:
        try:
            table_id = UUID(name_or_id)
            predicate = Table.id == table_id
        except ValueError:
            predicate = Table.name == name_or_id
        return (
            await db.execute(
                select(Table.id).where(
                    predicate,
                    Table.solution_id == principal.solution_id,
                    Table.organization_id == principal.organization_id,
                )
            )
        ).scalar_one_or_none()


async def _execution_allowed(
    principal: SolutionAppPrincipal,
    execution_id: str,
) -> bool:
    try:
        parsed_id = UUID(execution_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        return (
            await db.execute(
                select(Execution.id)
                .join(Workflow, Workflow.id == Execution.workflow_id)
                .where(
                    Execution.id == parsed_id,
                    Execution.executed_by == principal.actor_user_id,
                    Execution.organization_id == principal.organization_id,
                    Execution.execution_context["actor_jti"].astext == principal.jti,
                    Workflow.solution_id == principal.solution_id,
                    Workflow.organization_id == principal.organization_id,
                )
            )
        ).scalar_one_or_none() is not None


async def _file_location_allowed(
    principal: SolutionAppPrincipal,
    location: str,
) -> bool:
    async with get_db_context() as db:
        return (
            await db.execute(
                select(SolutionFileLocation.id).where(
                    SolutionFileLocation.solution_id == principal.solution_id,
                    SolutionFileLocation.location == location,
                )
            )
        ).scalar_one_or_none() is not None


def _path_matches(prefix: str, path: str) -> bool:
    normalized_prefix = prefix.strip("/")
    normalized_path = path.strip("/")
    return (
        not normalized_prefix
        or normalized_path == normalized_prefix
        or normalized_path.startswith(f"{normalized_prefix}/")
    )


def _parse_table_channel(
    channel: str,
    raw: dict[str, Any] | str,
) -> tuple[str, Expr | None] | None:
    name_or_id = channel.partition(":")[2]
    if not name_or_id:
        return None
    if not isinstance(raw, dict) or raw.get("filter") is None:
        return name_or_id, None
    try:
        return name_or_id, Expr.model_validate(raw["filter"])
    except ValidationError:
        return None


def _parse_file_channel(channel: str) -> tuple[str, str] | None:
    parts = channel.split(":", 2)
    if len(parts) != 3 or not parts[1]:
        return None
    return parts[1], parts[2].strip("/")


async def _subscribe(
    websocket: WebSocket,
    principal: SolutionAppPrincipal,
    raw: dict[str, Any] | str,
) -> None:
    channel = raw if isinstance(raw, str) else raw.get("name")
    if not isinstance(channel, str):
        await websocket.send_json({"type": "error", "message": "Invalid channel"})
        return

    canonical: str | None = None
    requested = channel
    if channel.startswith("table:"):
        parsed = _parse_table_channel(channel, raw)
        if parsed is not None:
            name_or_id, user_filter = parsed
            table_id = await _resolve_table(principal, name_or_id)
            if table_id is not None:
                canonical = f"table:{table_id}"
                websocket.state.table_subscriptions[str(table_id)] = {
                    "filter": user_filter,
                }
    elif channel.startswith("files:"):
        parsed_file = _parse_file_channel(channel)
        if parsed_file is not None:
            location, prefix = parsed_file
            # Solution file storage is isolated under the install UUID.  A
            # caller-supplied scope is deliberately ignored, exactly like the
            # HTTP file API's active Solution context.
            if await _file_location_allowed(principal, location):
                canonical = f"files:{location}:{principal.solution_id}"
                websocket.state.file_subscriptions[requested] = {
                    "channel": canonical,
                    "prefix": prefix,
                }
    elif channel.startswith("execution:"):
        execution_id = channel.partition(":")[2]
        if execution_id and await _execution_allowed(principal, execution_id):
            canonical = channel

    if canonical is None:
        await websocket.send_json(
            {"type": "error", "channel": requested, "message": "Access denied"}
        )
        return

    manager.connections.setdefault(canonical, set()).add(websocket)
    websocket.state.registered_channels[requested] = canonical
    await websocket.send_json({"type": "subscribed", "channel": canonical})


async def _unsubscribe(websocket: WebSocket, requested: str) -> None:
    canonical = websocket.state.registered_channels.pop(requested, None)
    if canonical is None and requested in websocket.state.registered_channels.values():
        canonical = requested
        for alias, registered in list(websocket.state.registered_channels.items()):
            if registered == canonical:
                websocket.state.registered_channels.pop(alias, None)
    if canonical is not None and canonical in manager.connections:
        manager.connections[canonical].discard(websocket)
    if canonical and canonical.startswith("table:"):
        websocket.state.table_subscriptions.pop(canonical.partition(":")[2], None)
    websocket.state.file_subscriptions.pop(requested, None)
    await websocket.send_json(
        {"type": "unsubscribed", "channel": canonical or requested}
    )


def _attach_dispatchers(
    websocket: WebSocket,
    actor_user: Any,
) -> None:
    async def table_dispatcher(
        channel: str,
        payload: dict[str, Any],
    ) -> None:
        table_id = channel.partition(":")[2]
        sub = websocket.state.table_subscriptions.get(table_id)
        if sub is None or payload.get("type") != "document_change":
            return
        user_filter = sub.get("filter")
        old_row = payload.get("old_row")
        new_row = payload.get("new_row")
        old_visible = old_row is not None and (
            user_filter is None or evaluate(user_filter, old_row, actor_user)
        )
        new_visible = new_row is not None and (
            user_filter is None or evaluate(user_filter, new_row, actor_user)
        )
        if not old_visible and not new_visible:
            return
        if old_visible and not new_visible:
            await websocket.send_json(
                {
                    "type": "document_change",
                    "action": "delete",
                    "table_id": table_id,
                    "row_id": (old_row or {}).get("id"),
                }
            )
            return
        await websocket.send_json(
            {
                "type": "document_change",
                "action": "update" if old_visible else "insert",
                "table_id": table_id,
                "row": new_row,
            }
        )

    async def file_dispatcher(
        channel: str,
        payload: dict[str, Any],
    ) -> None:
        if payload.get("type") != "file_change":
            return
        path = str(payload.get("path") or "").strip("/")
        for requested, sub in websocket.state.file_subscriptions.items():
            if sub["channel"] != channel or not _path_matches(sub["prefix"], path):
                continue
            await websocket.send_json(
                {
                    "type": "file_change",
                    "channel": requested,
                    "location": payload.get("location"),
                    "scope": payload.get("scope"),
                    "path": path,
                    "action": payload.get("action"),
                }
            )

    setattr(websocket, "_table_dispatcher", table_dispatcher)
    setattr(websocket, "_file_dispatcher", file_dispatcher)


@router.websocket("/connect")
async def connect_solution_app(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    if token is None:
        await _reject(websocket)
        return
    try:
        async with get_db_context() as db:
            principal = await authenticate_solution_app_token(
                token,
                db,
                request_path="/ws/connect",
            )
            actor_user = await get_solution_app_user(principal, db)
    except HTTPException:
        await _reject(websocket)
        return

    websocket.state.registered_channels = {}
    websocket.state.table_subscriptions = {}
    websocket.state.file_subscriptions = {}
    _attach_dispatchers(websocket, actor_user)

    try:
        await manager.connect(websocket, [])
        await websocket.send_json({"type": "connected", "channels": []})
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif message.get("type") == "subscribe":
                channels = message.get("channels")
                if not isinstance(channels, list):
                    await websocket.send_json(
                        {"type": "error", "message": "channels must be a list"}
                    )
                    continue
                for raw in channels:
                    await _subscribe(websocket, principal, raw)
            elif message.get("type") == "unsubscribe":
                channel = message.get("channel")
                if isinstance(channel, str):
                    await _unsubscribe(websocket, channel)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
