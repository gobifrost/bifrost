"""
NEW-1 failing-first proof: ConfigRepository.merged_for_sdk() unconditionally
unioned the global (organization_id IS NULL) config tier. Behind the plain
CurrentUser /api/cli/config/get endpoint, an external portal user could read —
and the endpoint would decrypt — a GLOBAL secret config value.

merged_for_sdk must drop the global tier for an external caller.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.repositories.config import ConfigRepository


class _Pipeline:
    """Small Redis transaction recorder for complete-view cache tests."""

    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def delete(self, *keys):
        self.operations.append(("delete", keys))
        return self

    def hset(self, key, mapping):
        self.operations.append(("hset", (key, mapping)))
        return self

    def expire(self, key, ttl):
        self.operations.append(("expire", (key, ttl)))
        return self

    async def execute(self):
        for operation, args in self.operations:
            if operation == "delete":
                self.redis.deleted.extend(args)
                for key in args:
                    self.redis.hashes.pop(key, None)
            elif operation == "hset":
                key, mapping = args
                self.redis.hsets.append((key, mapping))
                self.redis.hashes[key] = mapping
            else:
                self.redis.expires.append(args)
        return []


class _CompleteViewRedis:
    """Redis double for atomic complete-hash reads and writes."""

    def __init__(self):
        self.hashes = {}
        self.deleted = []
        self.hsets = []
        self.expires = []
        self.hgetall = AsyncMock(side_effect=lambda key: self.hashes.get(key, {}))
        self.hmget = AsyncMock(
            side_effect=lambda key, fields: [
                self.hashes.get(key, {}).get(field) for field in fields
            ]
        )

    async def get(self, key):
        return None

    def pipeline(self, *, transaction):
        assert transaction is True
        return _Pipeline(self)


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
    redis.get = AsyncMock(return_value=None)
    redis.hgetall = AsyncMock(return_value={})
    pipe = MagicMock()
    pipe.delete = MagicMock()
    pipe.hset = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    redis.pipeline = MagicMock(return_value=pipe)
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
        """A non-external list checks the completion field before SQL."""
        org = uuid4()
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.hgetall = AsyncMock(return_value={})
        pipe = MagicMock()
        pipe.delete = MagicMock()
        pipe.hset = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(return_value=[])
        redis.pipeline = MagicMock(return_value=pipe)

        repo = ConfigRepository(session, org_id=org, is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            await repo.merged_for_sdk()

        redis.hgetall.assert_awaited()

    async def test_empty_view_is_cached_and_reused_without_a_database_scan(self, session):
        """An empty complete view is cacheable without reserving a config key.

        The old implementation skipped the write when ``config_dict`` was
        empty, so each ``config.list`` for a newly configured organization
        repeated both SQL scans. A complete metadata field makes an otherwise
        empty hash distinguishable from a missing cache entry.
        """
        redis = _CompleteViewRedis()
        repo = ConfigRepository(session, org_id=uuid4(), is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            assert await repo.merged_for_sdk() == {}
            first_db_reads = session.execute.await_count
            assert first_db_reads == 2

            assert await repo.merged_for_sdk() == {}

        assert session.execute.await_count == first_db_reads
        assert len(redis.hsets) == 1
        _, mapping = redis.hsets[0]
        assert mapping == {"complete": "1"}
        assert redis.expires[0][1] > 0

    async def test_missing_key_cold_fill_is_reused_without_rescanning_sql(self, session):
        """The first missing SDK read fills the shared empty complete view."""
        redis = _CompleteViewRedis()
        repo = ConfigRepository(session, org_id=uuid4(), is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            assert await repo.get_for_sdk("missing") is None
            first_db_reads = session.execute.await_count
            assert first_db_reads == 2

            assert await repo.get_for_sdk("missing") is None

        assert session.execute.await_count == first_db_reads
        assert redis.hgetall.await_count == 1
        assert redis.hmget.await_count == 2

    async def test_malformed_complete_cache_reloads_from_database(self, session):
        """A corrupt v2 field is a cache miss, never a silently missing key."""
        redis = _CompleteViewRedis()
        org = uuid4()
        hash_key = f"bifrost:org:{org}:config:g0:o0:v2"
        redis.hashes[hash_key] = {
            "complete": "1",
            json.dumps("wanted"): "not-json",
        }
        repo = ConfigRepository(session, org_id=org, is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            assert await repo.merged_for_sdk() == {}

        assert session.execute.await_count == 2

    async def test_cold_fill_database_error_propagates_without_retry(self, session):
        """A database failure is not a Redis failure and must not be retried."""
        redis = _CompleteViewRedis()
        session.execute.side_effect = RuntimeError("database unavailable")
        repo = ConfigRepository(session, org_id=uuid4(), is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            with pytest.raises(RuntimeError, match="database unavailable"):
                await repo.get_for_sdk("missing")

        assert session.execute.await_count == 1

    async def test_single_key_read_uses_hmget_not_full_hash_scan(self, session):
        """SDK ``config.get`` reads only its requested Redis hash field."""
        redis = _CompleteViewRedis()
        org = uuid4()
        hash_key = f"bifrost:org:{org}:config:g0:o0:v2"
        redis.hashes[hash_key] = {
            "complete": "1",
            json.dumps("wanted"): json.dumps({"value": "targeted", "type": "string"}),
        }
        repo = ConfigRepository(session, org_id=org, is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            assert await repo.get_for_sdk("wanted") == {
                "value": "targeted",
                "type": "string",
            }

        redis.hmget.assert_awaited_once_with(
            hash_key, ["complete", json.dumps("wanted")]
        )
        redis.hgetall.assert_not_awaited()
        session.execute.assert_not_awaited()

    async def test_single_key_ignores_a_value_without_complete_metadata(self, session):
        """A partial hash cannot make a keyed SDK read return stale data."""
        global_config = SimpleNamespace(
            key="wanted",
            value={"value": "database"},
            config_type=SimpleNamespace(value="string"),
        )
        global_result = MagicMock()
        global_result.scalars.return_value = [global_config]
        org_result = MagicMock()
        org_result.scalars.return_value = []
        session.execute.side_effect = [global_result, org_result]
        redis = _CompleteViewRedis()
        org = uuid4()
        hash_key = f"bifrost:org:{org}:config:g0:o0:v2"
        redis.hashes[hash_key] = {
            json.dumps("wanted"): json.dumps({"value": "stale", "type": "string"}),
        }
        repo = ConfigRepository(session, org_id=org, is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            assert await repo.get_for_sdk("wanted") == {
                "value": "database",
                "type": "string",
            }

        assert session.execute.await_count == 2

    async def test_single_key_read_keeps_org_override_over_global(self, session):
        """The narrow DB fallback preserves the normal org-then-global cascade."""
        global_config = SimpleNamespace(
            key="shared", value={"value": "global"}, config_type=SimpleNamespace(value="string")
        )
        org_config = SimpleNamespace(
            key="shared", value={"value": "org"}, config_type=SimpleNamespace(value="string")
        )
        global_result = MagicMock()
        global_result.scalars.return_value = [global_config]
        org_result = MagicMock()
        org_result.scalars.return_value = [org_config]
        session.execute.side_effect = [global_result, org_result]
        redis = _CompleteViewRedis()
        repo = ConfigRepository(session, org_id=uuid4(), is_superuser=True)
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            assert await repo.get_for_sdk("shared") == {
                "value": "org",
                "type": "string",
            }

        # The cold keyed read fills the complete view once; later reads use HMGET.
        redis.hgetall.assert_awaited_once()
        assert session.execute.await_count == 2

    async def test_external_single_key_read_bypasses_shared_cache(self, session):
        """External restrictions still read the org tier only and never cache it."""
        redis = AsyncMock()
        org_config = SimpleNamespace(
            key="private", value={"value": "org-only"}, config_type=SimpleNamespace(value="string")
        )
        session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=org_config)
        )
        repo = ConfigRepository(
            session, org_id=uuid4(), is_superuser=True, is_external=True
        )
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ):
            assert await repo.get_for_sdk("private") == {
                "value": "org-only",
                "type": "string",
            }

        redis.hmget.assert_not_awaited()
        redis.hgetall.assert_not_awaited()
