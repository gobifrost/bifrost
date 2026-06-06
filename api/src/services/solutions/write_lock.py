"""Per-install write lock for Solution deploys (manual + git-connected).

ONE writer per install at a time (criterion 6). A deploy runs a DB phase
(reconcile + commit) AND a post-commit S3 finalize (Python source + app dists).
Both must be serialized together, or two concurrent writers interleave: A
commits DB, B commits DB, then A's finalize uploads LAST — leaving DB rows from
B but ``_solutions/``/``_apps/`` artifacts from A (Codex #12).

The lock is a Redis ``SET NX`` key with a short TTL that a watchdog RENEWS while
held, so it never expires mid-deploy regardless of how long clone + npm install +
vite build + finalize take (Codex #12 finding B) — but a CRASHED holder's key
still expires within one TTL so the install isn't wedged forever.

Manual deploy and git-connected sync share the SAME key namespace, so they can't
race each other either.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from uuid import UUID

logger = logging.getLogger(__name__)

# Short TTL + active renewal: a live holder keeps extending it; a crashed holder
# self-heals within one TTL. Renewal runs at TTL/2 so a single missed tick still
# leaves headroom before expiry.
_LOCK_TTL_S = 60
_RENEW_INTERVAL_S = 30


def _lock_key(solution_id: UUID) -> str:
    return f"bifrost:solution:write:{solution_id}"


class SolutionWriteLockHeld(Exception):
    """Another writer already holds this install's deploy lock."""


@contextlib.asynccontextmanager
async def solution_write_lock(
    solution_id: UUID, *, wait: bool = False
) -> AsyncIterator[None]:
    """Hold the per-install write lock across a deploy's DB + S3 phases.

    ``wait=False`` (manual deploy): raise :class:`SolutionWriteLockHeld`
    immediately if another writer holds it — the caller surfaces a 409.
    ``wait=False`` is also used by git-sync, which treats "held" as "skip" (an
    in-flight sync already covers main's latest).

    While held, a background task renews the TTL so a long deploy never loses the
    lock; the ``finally`` cancels the watchdog and deletes the key.
    """
    from src.core.redis_client import get_redis_client

    redis = await get_redis_client()._get_redis()
    key = _lock_key(solution_id)

    acquired = await redis.set(key, "1", nx=True, ex=_LOCK_TTL_S)
    if not acquired:
        if not wait:
            raise SolutionWriteLockHeld(str(solution_id))
        # wait=True: poll until free (bounded by the holder's TTL self-heal).
        while not acquired:
            await asyncio.sleep(1.0)
            acquired = await redis.set(key, "1", nx=True, ex=_LOCK_TTL_S)

    async def _renew() -> None:
        try:
            while True:
                await asyncio.sleep(_RENEW_INTERVAL_S)
                await redis.expire(key, _LOCK_TTL_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - renewal is best-effort; TTL self-heals
            # A transient redis blip just means the TTL counts down; if the work
            # outlives it a racing writer could acquire — logged, not fatal.
            logger.warning("write-lock renewal failed for %s", solution_id)

    watchdog = asyncio.create_task(_renew())
    try:
        yield
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        with contextlib.suppress(Exception):
            await redis.delete(key)
