"""
NEW-1 failing-first proof: ConfigRepository.merged_for_sdk() unconditionally
unioned the global (organization_id IS NULL) config tier. Behind the plain
CurrentUser /api/cli/config/get endpoint, an external portal user could read —
and the endpoint would decrypt — a GLOBAL secret config value.

merged_for_sdk must drop the global tier for an external caller.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.repositories.config import ConfigRepository


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    result = MagicMock()
    result.scalars.return_value = []
    s.execute.return_value = result
    return s


def _no_cache():
    """Patch redis so merged_for_sdk falls through to the DB query path."""
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return patch(
        "src.core.cache.redis_client.get_shared_redis",
        new=AsyncMock(return_value=redis),
    )


def _executed_where(session) -> str:
    out = []
    for call in session.execute.await_args_list:
        stmt = call.args[0]
        try:
            full = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            full = str(stmt)
        _, _, where = full.partition("WHERE")
        out.append(where)
    return "\n".join(out)


@pytest.mark.asyncio
class TestMergedForSdkExternal:
    async def test_external_skips_global_tier(self, session):
        repo = ConfigRepository(
            session, org_id=uuid4(), is_superuser=True, is_external=True
        )
        with _no_cache():
            await repo.merged_for_sdk()
        where = _executed_where(session)
        assert "organization_id IS NULL" not in where, (
            "external caller's config load must not union the global tier"
        )

    async def test_normal_caller_keeps_global_tier(self, session):
        repo = ConfigRepository(session, org_id=uuid4(), is_superuser=True)
        with _no_cache():
            await repo.merged_for_sdk()
        where = _executed_where(session)
        assert "organization_id IS NULL" in where

    async def test_external_read_bypasses_cache(self, session):
        """The external view must NOT read OR write the shared org cache key
        (which holds the global-merged view) — external reads skip the cache
        entirely and go straight to the org-only DB query."""
        org = uuid4()
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        redis.hset = AsyncMock()
        redis.expire = AsyncMock()

        repo = ConfigRepository(
            session, org_id=org, is_superuser=True, is_external=True
        )
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            await repo.merged_for_sdk()

        redis.hgetall.assert_not_awaited()
        redis.hset.assert_not_awaited()

    async def test_normal_read_uses_cache(self, session):
        """A non-external read still reads the cache (regression guard)."""
        org = uuid4()
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        redis.hset = AsyncMock()
        redis.expire = AsyncMock()

        repo = ConfigRepository(session, org_id=org, is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            await repo.merged_for_sdk()

        redis.hgetall.assert_awaited()
