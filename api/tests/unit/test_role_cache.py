"""
Unit tests for the per-user role cache.

The role cache stores `(role_ids, role_names)` per user under
`bifrost:role_cache:user:{user_id}`. It backs the table-policy evaluator's
`has_role` lookups and replaces the per-request `JOIN UserRole ON Role`
queries that used to fire from `get_execution_context` and the WS
`_populate_user_roles` hook.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.role_cache import (
    _ROLE_CACHE_KEY_PREFIX,
    _ROLE_CACHE_TTL,
    get_user_roles,
    invalidate_role,
    invalidate_user,
)


class _Row:
    """Minimal stand-in for SQLAlchemy `Row` with `.id` / `.name` attrs."""

    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name


def _make_db_returning(rows: list[tuple]) -> AsyncMock:
    """Return a fake AsyncSession whose `.execute()` yields rows.

    `rows` is the list of tuples that `result.all()` should return — each
    tuple is `(role_id, role_name)`.
    """
    db = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[_Row(r[0], r[1]) for r in rows])
    db.execute = AsyncMock(return_value=result)
    return db


class TestGetUserRoles:
    """Read path: cache-first, DB on miss, populates after miss."""

    @pytest.mark.asyncio
    async def test_get_user_roles_miss_hydrates_from_db(self):
        """Cache empty -> DB query fires -> cache populated with TTL."""
        user_id = uuid4()
        role_id = uuid4()
        db = _make_db_returning([(role_id, "admin")])

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # cache miss
        mock_redis.set = AsyncMock()

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            role_ids, role_names = await get_user_roles(user_id, db)

        # DB was queried
        db.execute.assert_called_once()
        # Cache was populated with our payload + TTL
        mock_redis.set.assert_called_once()
        args, kwargs = mock_redis.set.call_args
        assert args[0] == f"{_ROLE_CACHE_KEY_PREFIX}{user_id}"
        payload = json.loads(args[1])
        assert payload == {
            "role_ids": [str(role_id)],
            "role_names": ["admin"],
            "v": 1,
        }
        assert kwargs.get("ex") == _ROLE_CACHE_TTL

        assert role_ids == [role_id]
        assert role_names == ["admin"]

    @pytest.mark.asyncio
    async def test_get_user_roles_hit_returns_cache_no_db(self):
        """Pre-seeded cache returns immediately; no DB query."""
        user_id = uuid4()
        role_id = uuid4()

        db = AsyncMock()
        db.execute = AsyncMock()  # would raise an attribute error in test if invoked

        cached = json.dumps(
            {"role_ids": [str(role_id)], "role_names": ["editor"], "v": 1}
        )
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=cached)
        mock_redis.set = AsyncMock()

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            role_ids, role_names = await get_user_roles(user_id, db)

        db.execute.assert_not_called()
        mock_redis.set.assert_not_called()
        assert role_ids == [role_id]
        assert role_names == ["editor"]

    @pytest.mark.asyncio
    async def test_cache_handles_user_with_no_roles(self):
        """Empty role list is a valid cache value, not a sentinel for miss."""
        user_id = uuid4()

        # Seed cache with empty role list
        cached = json.dumps({"role_ids": [], "role_names": [], "v": 1})
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=cached)
        mock_redis.set = AsyncMock()

        db = AsyncMock()
        db.execute = AsyncMock()

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            role_ids, role_names = await get_user_roles(user_id, db)

        # No DB hit, no re-population
        db.execute.assert_not_called()
        mock_redis.set.assert_not_called()
        assert role_ids == []
        assert role_names == []

    @pytest.mark.asyncio
    async def test_get_user_roles_miss_with_no_roles_caches_empty(self):
        """User with no roles: DB returns empty, cache stores empty payload."""
        user_id = uuid4()
        db = _make_db_returning([])

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            role_ids, role_names = await get_user_roles(user_id, db)

        mock_redis.set.assert_called_once()
        args, _ = mock_redis.set.call_args
        payload = json.loads(args[1])
        assert payload == {"role_ids": [], "role_names": [], "v": 1}
        assert role_ids == []
        assert role_names == []


class TestInvalidateUser:
    """Write path (single user): invalidate clears one entry."""

    @pytest.mark.asyncio
    async def test_invalidate_user_role_cache_clears_entry(self):
        """Invalidate -> next read sees miss and re-hydrates from DB."""
        user_id = uuid4()
        role_id = uuid4()

        # Stage 1: invalidate issues a redis DELETE for this user's key
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            await invalidate_user(user_id)

        mock_redis.delete.assert_called_once_with(f"{_ROLE_CACHE_KEY_PREFIX}{user_id}")

        # Stage 2: after invalidation, read sees miss and queries DB
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        db = _make_db_returning([(role_id, "admin")])

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            role_ids, role_names = await get_user_roles(user_id, db)

        db.execute.assert_called_once()
        assert role_ids == [role_id]
        assert role_names == ["admin"]

    @pytest.mark.asyncio
    async def test_invalidate_user_handles_redis_error(self):
        """Redis failure during invalidation is logged, not raised."""
        user_id = uuid4()

        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("redis down"))

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            # Must not raise
            await invalidate_user(user_id)


class TestInvalidateRole:
    """Write path (role-wide): invalidate every user holding that role."""

    @pytest.mark.asyncio
    async def test_role_id_invalidation_clears_all_users_with_role(self):
        """Multiple users have the role -> all entries cleared."""
        role_id = uuid4()
        other_role_id = uuid4()
        user_a = uuid4()
        user_b = uuid4()
        user_c = uuid4()  # has a different role; should NOT be cleared

        # Cached entries keyed by user
        store = {
            f"{_ROLE_CACHE_KEY_PREFIX}{user_a}": json.dumps(
                {"role_ids": [str(role_id)], "role_names": ["admin"], "v": 1}
            ),
            f"{_ROLE_CACHE_KEY_PREFIX}{user_b}": json.dumps(
                {
                    "role_ids": [str(role_id), str(other_role_id)],
                    "role_names": ["admin", "viewer"],
                    "v": 1,
                }
            ),
            f"{_ROLE_CACHE_KEY_PREFIX}{user_c}": json.dumps(
                {"role_ids": [str(other_role_id)], "role_names": ["viewer"], "v": 1}
            ),
        }
        deleted: list[str] = []

        async def fake_get(key: str):
            return store.get(key)

        async def fake_delete(key: str):
            deleted.append(key)
            store.pop(key, None)

        async def fake_scan_iter(match: str):
            for k in list(store.keys()):
                yield k

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=fake_get)
        mock_redis.delete = AsyncMock(side_effect=fake_delete)
        mock_redis.scan_iter = fake_scan_iter

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            await invalidate_role(role_id)

        # Only the two users with role_id were cleared
        assert sorted(deleted) == sorted(
            [
                f"{_ROLE_CACHE_KEY_PREFIX}{user_a}",
                f"{_ROLE_CACHE_KEY_PREFIX}{user_b}",
            ]
        )
        # user_c still cached
        assert f"{_ROLE_CACHE_KEY_PREFIX}{user_c}" in store

    @pytest.mark.asyncio
    async def test_invalidate_role_handles_redis_error(self):
        """Redis failure during role-wide invalidation is logged, not raised."""
        role_id = uuid4()

        async def fake_scan_iter(match: str):
            raise Exception("redis down")
            yield  # pragma: no cover

        mock_redis = AsyncMock()
        mock_redis.scan_iter = fake_scan_iter

        with patch("shared.role_cache.get_shared_redis", return_value=mock_redis):
            # Must not raise
            await invalidate_role(role_id)
