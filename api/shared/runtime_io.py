"""Bounded runtime logging and service supervision through the shared API."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import RuntimeLogRequest, RuntimeLogResponse, ServiceControlRequest, ServiceControlResponse
from src.core.principal import UserPrincipal


def _runtime(user: UserPrincipal):
    authority = user.runtime_authority
    if authority is None or authority.kind not in {"workflow_execution", "service_attempt"}:
        raise HTTPException(403, "A live workflow or service credential is required")
    return authority


async def write_runtime_log(user: UserPrincipal, request: RuntimeLogRequest) -> RuntimeLogResponse:
    """The credential supplies the target; the request cannot choose another stream."""
    authority = _runtime(user)
    if authority.kind == "service_attempt":
        from bifrost._logging import publish_service_log

        entry_id = await asyncio.to_thread(
            publish_service_log, str(authority.service_id), str(authority.credential_id),
            request.level, request.message, request.timestamp,
        )
    else:
        from bifrost._logging import append_log_to_stream_async, publish_log_to_pubsub_async

        entry_id = await append_log_to_stream_async(
            authority.credential_id, request.level, request.message, request.metadata, request.timestamp,
        )
        await publish_log_to_pubsub_async(
            authority.credential_id, request.level, request.message, request.metadata, request.timestamp,
        )
    return RuntimeLogResponse(entry_id=entry_id)


async def service_control(user: UserPrincipal, request: ServiceControlRequest) -> ServiceControlResponse:
    """Read this attempt's parent-owned control state, without minting credentials."""
    authority = _runtime(user)
    if authority.kind != "service_attempt":
        raise HTTPException(403, "A live service attempt credential is required")
    from src.core.cache import get_redis
    from src.core.cache.keys import service_ready_key, service_stop_key, service_token_key

    async with get_redis() as redis:
        if request.ready:
            await redis.setex(service_ready_key(str(authority.credential_id)), 120, "1")
        stop_requested = bool(await redis.get(service_stop_key(str(authority.credential_id))))
        raw = await redis.get(service_token_key(str(authority.credential_id)))
    token = None
    expires_at = None
    if raw:
        payload = json.loads(raw)
        expires_at = datetime.fromisoformat(payload["expires_at"])
        if expires_at > request.token_expires_at:
            token = payload["token"]
        else:
            expires_at = None
    return ServiceControlResponse(
        stop_requested=stop_requested, access_token=token, token_expires_at=expires_at,
    )


async def read_runtime_logs(
    db: AsyncSession, user: UserPrincipal, execution_id: UUID, *, start: str, count: int,
):
    """Read live log rows through execution visibility and diagnostic permissions."""
    from bifrost._logging import read_logs_from_stream
    from src.models.orm.executions import Execution
    from src.services.authorization.enforce import load_caller
    from src.services.authorization.execution_access import execution_read_access

    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(404, "Execution not found")
    caller = await load_caller(db, user)
    access = execution_read_access(caller, execution)
    runtime = user.runtime_authority
    own_stream = runtime is not None and runtime.kind == "workflow_execution" and runtime.credential_id == execution_id
    if not own_stream and not access.visible:
        raise HTTPException(403, "You do not have permission to view this execution")
    rows = await read_logs_from_stream(execution_id, start=start, count=count)
    return [row for row in rows if access.diagnostics or row.level.upper() not in {"DEBUG", "TRACEBACK"}]
