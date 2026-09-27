"""
Live service memory served from pool registration hashes.

The claim loop publishes each owned attempt's footprint into its worker's
``bifrost:pool:{worker_id}`` hash under the ``services`` field
(``{attempt_id: {memory_mb, updated_at}}``). This module reads those hashes
back across workers — same precedent as worker packages
(``routers/packages.py``) — so the services API serves memory without
reaching into worker processes.

Entries carry their own timestamp; readers accept only entries younger
than ``MEMORY_ENTRY_MAX_AGE_SECONDS`` so a dead worker's last publish
stops displaying once its registration expires.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, cast

logger = logging.getLogger(__name__)

#: Max age of a published memory entry before the API treats it as stale.
MEMORY_ENTRY_MAX_AGE_SECONDS = 90


def parse_service_memory_entries(
    raw: str | bytes | None, *, now: datetime | None = None
) -> dict[str, float]:
    """Parse one ``services`` hash field into attempt_id -> memory_mb.

    Pure function (no Redis) so the staleness bound is unit-testable.
    Entries missing a numeric ``memory_mb`` or a parseable ``updated_at``
    within the age bound are dropped.
    """
    if not raw:
        return {}
    try:
        entries = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(entries, dict):
        return {}
    now = now or datetime.now(timezone.utc)
    fresh: dict[str, float] = {}
    for attempt_id, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        memory_mb = entry.get("memory_mb")
        if isinstance(memory_mb, bool) or not isinstance(
            memory_mb, (int, float)
        ):
            continue
        try:
            updated_at = datetime.fromisoformat(str(entry.get("updated_at")))
        except (ValueError, TypeError):
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        # Absolute age: worker clocks can skew either way; entries more
        # than the bound old OR in the future are both untrustworthy.
        if abs((now - updated_at).total_seconds()) > MEMORY_ENTRY_MAX_AGE_SECONDS:
            continue
        fresh[str(attempt_id)] = float(memory_mb)
    return fresh


async def read_service_memory() -> dict[str, float]:
    """Attempt_id -> memory_mb across all workers (one scan, fresh only).

    Scans ``bifrost:pool:*`` registration keys (exactly two colons, so
    heartbeat/command keys are skipped) and merges each ``services`` field.
    Redis failures yield an empty map — memory is informational and must
    never break the services endpoints.
    """
    from src.core.cache import get_redis

    merged: dict[str, float] = {}
    try:
        async with get_redis() as r:
            cursor: Any = 0
            while True:
                cursor, keys = await cast(
                    Awaitable[tuple[Any, list[str]]],
                    r.scan(cursor, match="bifrost:pool:*", count=100),
                )
                for key in keys:
                    key_str = (
                        key.decode()
                        if isinstance(key, (bytes, bytearray))
                        else str(key)
                    )
                    if key_str.count(":") != 2:
                        continue
                    raw = await cast(
                        Awaitable[str | bytes | None],
                        r.hget(key_str, "services"),
                    )
                    merged.update(parse_service_memory_entries(raw))
                if int(cursor) == 0:
                    break
    except Exception as e:
        logger.debug("service memory scan failed: %s", e)
    return merged
