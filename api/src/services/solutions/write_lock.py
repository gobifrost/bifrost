"""Per-install write lock for Solution deploys (manual + git-connected).

ONE writer per install at a time (criterion 6). A deploy runs a DB phase
(reconcile + commit) AND a post-commit S3 finalize (Python source + app dists).
Both must be serialized together, or two concurrent writers interleave: A
commits DB, B commits DB, then A's finalize uploads LAST — leaving DB rows from
B but ``_solutions/``/``_apps/`` artifacts from A (Codex #12).

The lock is a Redis key holding a PER-HOLDER token, with a short TTL that a
watchdog RENEWS while held, so it never expires mid-deploy regardless of how long
clone + npm install + vite build + finalize take — but a CRASHED holder's key
still expires within one TTL so the install isn't wedged forever.

Renew and release are COMPARE-BY-TOKEN (Lua, atomic): if this holder ever lost
the lock (missed the TTL) and another writer acquired it, our watchdog must NOT
extend, and our release must NOT delete, the SUCCESSOR's lock (Codex #13). The
watchdog also keeps retrying after a transient Redis blip rather than dying on
the first failure (which would silently stop renewal and let the lock expire
under a live holder — Codex #13).

Manual deploy and git-connected sync share the SAME key namespace, so they can't
race each other either.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

# Short TTL + active renewal: a live holder keeps extending it; a crashed holder
# self-heals within one TTL. Renewal runs well inside the TTL so a single missed
# tick still leaves headroom before expiry.
_LOCK_TTL_S = 60
_RENEW_INTERVAL_S = 20

# Extend the TTL only if we still own the key (value == our token). Atomic, so a
# successor's lock is never extended by a stale holder's watchdog.
_RENEW_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
else
    return 0
end
"""
# Release only if we still own the key — never delete a successor's lock.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


def _lock_key(solution_id: UUID) -> str:
    return f"bifrost:solution:write:{solution_id}"


class SolutionWriteLockHeld(Exception):
    """Another writer already holds this install's deploy lock."""


@contextlib.asynccontextmanager
async def solution_write_lock(solution_id: UUID) -> AsyncIterator[None]:
    """Hold the per-install write lock across a deploy's DB + S3 phases.

    Raise :class:`SolutionWriteLockHeld` immediately if another writer holds it —
    the manual router surfaces a 409; git-sync treats it as "skip" (an in-flight
    sync already covers main's latest, and it queues a pending re-run).

    While held, a background task renews the TTL (compare-by-token) so a long
    deploy never loses the lock; the ``finally`` cancels the watchdog and deletes
    the key (compare-by-token).
    """
    from src.core.redis_client import get_redis_client

    redis = await get_redis_client()._get_redis()
    key = _lock_key(solution_id)
    token = uuid4().hex  # per-holder fencing token
    renew = redis.register_script(_RENEW_LUA)
    release = redis.register_script(_RELEASE_LUA)

    acquired = await redis.set(key, token, nx=True, ex=_LOCK_TTL_S)
    if not acquired:
        raise SolutionWriteLockHeld(str(solution_id))

    async def _renew() -> None:
        # Keep renewing for the lifetime of the context. A transient redis error
        # must NOT end the loop (that would silently stop renewal and let the
        # lock expire under a live holder — Codex #13); log and try again next tick.
        while True:
            await asyncio.sleep(_RENEW_INTERVAL_S)
            try:
                await renew(keys=[key], args=[token, _LOCK_TTL_S])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - renewal is best-effort; keep trying
                logger.warning(
                    "write-lock renewal failed for %s (will retry)", solution_id
                )

    watchdog = asyncio.create_task(_renew())
    try:
        yield
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        with contextlib.suppress(Exception):
            # Release only if still ours — never delete a successor's lock.
            await release(keys=[key], args=[token])
