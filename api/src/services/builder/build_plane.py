"""Build-plane availability gate (WP3 Task 1).

Server-side builds (compiling a private Solution's generated app) run on a
separate coordinator process — the "build plane" — not in the API process.
Before a caller enqueues a build job, it must know whether any coordinator is
actually alive to claim it; otherwise the job would queue forever with no one
watching it.

The coordinator proves it's alive by heartbeating a single Redis key on a
short TTL. Availability is simply "does that key currently exist" — no
separate liveness protocol, no polling loop here; Redis's own TTL expiry does
the failure detection.
"""
from __future__ import annotations

from uuid import UUID

# Bump when the runner image toolchain changes (node/vite majors, etc.) so
# in-flight builds queued against a stale toolchain can be identified/drained.
TOOLCHAIN_VERSION = "node20-vite5-v1"

HEARTBEAT_KEY = "bifrost:builder:heartbeat"
HEARTBEAT_TTL_S = 30


def cancel_key(build_job_id: UUID | str) -> str:
    """Redis key for a build job's cancellation flag."""
    return f"bifrost:build_job:{build_job_id}:cancel"


async def record_builder_heartbeat() -> None:
    """Record that the coordinator is alive.

    Called on a timer by the coordinator process. SETs the heartbeat key to
    the current ISO timestamp with a short TTL — the timestamp value itself
    isn't consulted by :func:`build_plane_available` (key existence is the
    signal), but it's useful for operators inspecting Redis directly.
    """
    from datetime import datetime, timezone

    from src.core.redis_client import get_redis_client

    redis = await get_redis_client()._get_redis()
    await redis.set(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat(), ex=HEARTBEAT_TTL_S)


async def build_plane_available() -> bool:
    """True iff a coordinator has heartbeated within the last TTL window."""
    from src.core.redis_client import get_redis_client

    redis = await get_redis_client()._get_redis()
    return await redis.exists(HEARTBEAT_KEY) > 0


class BuildPlaneUnavailable(Exception):
    """Raised when a server build is required but no coordinator heartbeat exists."""
