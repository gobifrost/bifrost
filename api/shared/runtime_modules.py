"""Read import-root metadata published by live execution workers.

The scheduler and API do not necessarily share workers' installed Python
environment. Workers publish a bounded inventory in their existing Redis
registration, which expires with that registration's normal TTL.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, cast

logger = logging.getLogger(__name__)

_POOL_KEY_PREFIX = "bifrost:pool:"
_MODULE_ROOTS_FIELD = "module_roots"
_AVAILABLE = "available"
_MAX_REGISTERED_WORKERS = 32
_MAX_METADATA_BYTES = 64 * 1024
MAX_RUNTIME_MODULE_ROOTS = 512
_MAX_AGGREGATE_ROOTS = 1024


@dataclass(frozen=True)
class RuntimeModuleInventory:
    """Top-level roots from registered workers and whether they are complete."""

    roots: frozenset[str]
    available: bool


def _parse_module_roots(raw: str | bytes | None) -> frozenset[str] | None:
    """Return a complete worker inventory, or ``None`` when telemetry is absent."""
    if not raw:
        return None
    if len(raw) > _MAX_METADATA_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != _AVAILABLE:
        return None
    roots = payload.get("roots")
    if (
        not isinstance(roots, list)
        or len(roots) > MAX_RUNTIME_MODULE_ROOTS
        or not all(
            isinstance(root, str) and root.isidentifier() for root in roots
        )
    ):
        return None
    return frozenset(roots)


async def read_runtime_modules() -> RuntimeModuleInventory:
    """Aggregate import roots from live worker registrations once.

    A missing, malformed, or explicitly unavailable inventory makes the result
    unavailable. Known roots remain useful context, but callers must not treat
    them as a complete description of every worker environment.
    """
    from src.core.cache import get_redis

    roots: set[str] = set()
    saw_worker = False
    complete = True
    worker_count = 0
    try:
        async with get_redis() as redis:
            cursor: Any = 0
            while True:
                cursor, keys = await cast(
                    Awaitable[tuple[Any, list[str]]],
                    redis.scan(cursor, match=f"{_POOL_KEY_PREFIX}*", count=100),
                )
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else str(key)
                    if key_str.count(":") != 2:
                        continue
                    saw_worker = True
                    worker_count += 1
                    if worker_count > _MAX_REGISTERED_WORKERS:
                        return RuntimeModuleInventory(
                            roots=frozenset(), available=False
                        )
                    raw = await cast(
                        Awaitable[str | bytes | None],
                        redis.hget(key_str, _MODULE_ROOTS_FIELD),
                    )
                    worker_roots = _parse_module_roots(raw)
                    if worker_roots is None:
                        complete = False
                        continue
                    if len(roots | worker_roots) > _MAX_AGGREGATE_ROOTS:
                        return RuntimeModuleInventory(
                            roots=frozenset(), available=False
                        )
                    roots.update(worker_roots)
                if int(cursor) == 0:
                    break
    except Exception:  # noqa: BLE001 - informational telemetry is optional
        logger.debug("runtime module inventory scan failed")
        return RuntimeModuleInventory(roots=frozenset(), available=False)

    return RuntimeModuleInventory(
        roots=frozenset(roots), available=saw_worker and complete
    )
