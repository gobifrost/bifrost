"""Test helper: mark an engine execution as live in Redis.

Production dispatch (process_pool.py::_dispatch_to_child) always writes the
active-execution lease right after forking a child and before that child
ever receives its engine token - get_current_user_optional's engine-token
liveness check (src/core/auth.py) relies on this lease. Real-fork socket
tests that mint an engine token directly (bypassing the process pool) must
write the same lease so they exercise a still-running child the way
production does, instead of hitting a false 401.
"""

from __future__ import annotations

import json

from src.core.cache.keys import TTL_ACTIVE_EXECUTION, active_execution_key
from src.core.redis_client import get_redis_client


async def mark_engine_execution_running(execution_id: str) -> None:
    """Write the active-execution lease an engine token's execution_id needs."""
    r = await get_redis_client()._get_redis()  # noqa: SLF001 - test-only, matches process_pool.py
    await r.setex(
        active_execution_key(execution_id),
        TTL_ACTIVE_EXECUTION,
        json.dumps({"execution_id": execution_id}),
    )


async def clear_engine_execution_lease(execution_id: str) -> None:
    r = await get_redis_client()._get_redis()  # noqa: SLF001
    await r.delete(active_execution_key(execution_id))
