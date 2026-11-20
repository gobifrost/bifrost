#!/usr/bin/env python3
"""One-purpose API-container bridge for disposable Cloudflare conformance.

It is executed only through ``docker exec -i`` by ``cloudflare_live_suite``.
There is no application route, no stored test state, and no capacity mutation:
the bridge can insert one canonical external worker reservation and read back
that same worker's admission state.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any
from uuid import UUID


RESOURCE_UID_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,255}$")


class BridgeError(RuntimeError):
    pass


def _request() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except ValueError as exc:
        raise BridgeError("invalid bridge input") from exc
    if not isinstance(value, dict):
        raise BridgeError("invalid bridge input")
    return value


def _identity(value: dict[str, Any]) -> tuple[str, UUID, str, dict[str, Any]]:
    scope, worker_id, resource_uid, reservation = (
        value.get("scope"), value.get("worker_id"), value.get("resource_uid"), value.get("reservation"),
    )
    if (
        not isinstance(scope, str)
        or re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", scope) is None
        or os.environ.get("BIFROST_CONFORMANCE_SCOPE") != scope
    ):
        raise BridgeError("conformance scope is not enabled for this API")
    if not isinstance(worker_id, str):
        raise BridgeError("invalid worker identity")
    try:
        worker_uuid = UUID(worker_id)
    except (TypeError, ValueError) as exc:
        raise BridgeError("invalid worker identity") from exc
    if not isinstance(resource_uid, str) or RESOURCE_UID_RE.fullmatch(resource_uid) is None:
        raise BridgeError("invalid resource identity")
    if not isinstance(reservation, dict):
        raise BridgeError("invalid capacity reservation")
    return scope, worker_uuid, resource_uid, reservation


async def _capacity_configuration(scope: str, reservation: dict[str, Any]):
    from shared.models import WorkerCapacityReservation
    from shared.version import get_version
    from src.config import get_settings
    from src.core.database import get_db_context
    from src.services.worker_pool.connection import load_capacity_provider_connection
    from src.worker.remote.protocol import WORKER_DRAIN_GRACE_SECONDS

    try:
        parsed = WorkerCapacityReservation.model_validate(reservation)
    except ValueError as exc:
        raise BridgeError("invalid capacity reservation") from exc
    if parsed.worker_version != get_version():
        raise BridgeError("capacity reservation build does not match this API")
    settings = get_settings()
    if parsed.max_lifetime_seconds > settings.pool_max_lifetime_seconds:
        raise BridgeError("capacity reservation lifetime exceeds API policy")
    if parsed.drain_grace_seconds != WORKER_DRAIN_GRACE_SECONDS:
        raise BridgeError("capacity reservation drain grace is not canonical")
    async with get_db_context() as db:
        connection = await load_capacity_provider_connection(db, settings)
    if connection is None:
        raise BridgeError("external capacity control is not configured")
    # The suite never transports a provider token.  Its isolated API must be
    # configured with the same canonical capacity token as the disposable
    # Worker so the existing external-worker HMAC validation can succeed.
    return parsed


async def seed(value: dict[str, Any]) -> None:
    scope, worker_id, resource_uid, reservation = _identity(value)
    parsed = await _capacity_configuration(scope, reservation)
    if parsed.worker_id != worker_id:
        raise BridgeError("capacity reservation worker identity does not match")

    from shared.worker_compute import WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES, WORKER_IMAGE_HANDLER_NAMES
    from src.core.database import get_db_context
    from src.models.orm.execution_workers import ExecutionWorker

    async with get_db_context() as db:
        if await db.get(ExecutionWorker, worker_id) is not None:
            # Never repair or overwrite an existing row: a fixture pointed at
            # the wrong UUID/UID must fail rather than adopt another worker.
            raise BridgeError("external worker identity already exists")
        db.add(ExecutionWorker(
            id=worker_id,
            form="external",
            state="starting",
            resource_class="standard",
            execution_lane="normal",
            capacity_scope=scope,
            capacity_resource_uid=resource_uid,
            memory_limit_bytes=parsed.memory_limit_bytes,
            version=parsed.worker_version,
            supported_handlers=list(WORKER_IMAGE_HANDLER_NAMES),
            supported_compute=list(WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES),
        ))
        await db.commit()


async def ready(value: dict[str, Any]) -> bool:
    scope, worker_id, resource_uid, reservation = _identity(value)
    parsed = await _capacity_configuration(scope, reservation)
    if parsed.worker_id != worker_id:
        raise BridgeError("capacity reservation worker identity does not match")

    from sqlalchemy import func, select

    from src.core.database import get_db_context
    from src.models.orm.execution_workers import ExecutionWorker

    async with get_db_context() as db:
        worker = await db.scalar(select(ExecutionWorker).where(
            ExecutionWorker.id == worker_id,
            ExecutionWorker.form == "external",
            ExecutionWorker.state == "ready",
            ExecutionWorker.execution_lane == "normal",
            ExecutionWorker.capacity_scope == scope,
            ExecutionWorker.capacity_resource_uid == resource_uid,
            ExecutionWorker.version == parsed.worker_version,
            ExecutionWorker.remote_launcher_id.is_not(None),
            ExecutionWorker.remote_owner_id.is_not(None),
            ExecutionWorker.remote_session_generation > 0,
            ExecutionWorker.remote_owner_lease_expires_at > func.clock_timestamp(),
            ExecutionWorker.memory_budget_bytes > 0,
            ExecutionWorker.remote_owner_lease_expires_at.is_not(None),
        ))
    return worker is not None


async def run(value: dict[str, Any]) -> dict[str, bool]:
    action = value.get("action")
    if action == "seed":
        await seed(value)
        return {"ok": True}
    if action == "ready":
        return {"ok": True, "ready": await ready(value)}
    raise BridgeError("unsupported bridge action")


def main() -> int:
    try:
        print(json.dumps(asyncio.run(run(_request()))))
        return 0
    except BridgeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
