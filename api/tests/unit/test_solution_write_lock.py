"""Per-install Solution write lock (Codex #12): one writer per install across
the DB + S3-finalize phases, with TTL renewal so a long deploy can't lose it."""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.services.solutions.write_lock import (
    SolutionWriteLockHeld,
    _LOCK_TTL_S,
    _lock_key,
    solution_write_lock,
)


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.mark.e2e
class TestSolutionWriteLock:
    async def test_second_writer_is_refused_while_held(self) -> None:
        """A concurrent manual deploy (wait=False) is refused while another
        writer holds the lock — the caller surfaces a 409."""
        sid = uuid4()
        async with solution_write_lock(sid):
            with pytest.raises(SolutionWriteLockHeld):
                async with solution_write_lock(sid):
                    pass

    async def test_lock_is_released_on_exit(self) -> None:
        """After the context exits the lock is free for the next writer."""
        sid = uuid4()
        async with solution_write_lock(sid):
            pass
        # Re-acquire immediately — must succeed (no leftover key).
        async with solution_write_lock(sid):
            pass

    async def test_different_installs_dont_block_each_other(self) -> None:
        async with solution_write_lock(uuid4()):
            async with solution_write_lock(uuid4()):
                pass  # distinct keys → both held concurrently, no raise

    async def test_release_is_compare_by_token(self) -> None:
        """Codex #13: a stale holder must not release a SUCCESSOR's lock. Acquire
        as A, simulate A losing it + B acquiring (overwrite the key with B's
        token), then exit A's context — A's release must NOT delete B's key."""
        from src.core.redis_client import get_redis_client

        sid = uuid4()
        redis = await get_redis_client()._get_redis()
        key = _lock_key(sid)
        async with solution_write_lock(sid):
            # Simulate: A's TTL lapsed and B acquired with its OWN token.
            await redis.set(key, "B-successor-token", ex=_LOCK_TTL_S)
        # A's finally ran release(compare-by-token) — B's key must survive.
        assert await redis.get(key) == "B-successor-token"
        await redis.delete(key)  # cleanup

    async def test_holds_a_live_ttl_that_renews(self) -> None:
        """While held, the key carries a positive TTL (it's the renewable lock,
        not a permanent key) so a crashed holder still self-heals."""
        from src.core.redis_client import get_redis_client

        sid = uuid4()
        async with solution_write_lock(sid):
            redis = await get_redis_client()._get_redis()
            ttl = await redis.ttl(_lock_key(sid))
            assert 0 < ttl <= _LOCK_TTL_S
