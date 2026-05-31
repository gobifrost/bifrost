"""Regression tests for ``PUT /api/config/{id}`` cache invalidation on
rename or org-move (Codex finding HIGH, 2026-05-26).

The pre-overhaul ``update_config`` route only upserted the NEW
``(scope, key)`` cache entry after a successful write. If the row's
``key`` or ``organization_id`` changed in the same update, the OLD
``(scope, key)`` entry survived for the full TTL — potentially holding
a stale secret.

These tests pin the fix:
  - rename (key change): old key deleted, new key upserted.
  - org-move (organization_id change): old org's cache entry deleted,
    new org's upserted, and CONFIG_GLOBAL_VERSION_KEY incremented on a
    global↔org transition.
  - identity-preserving update: no extra delete.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.repositories.config import ConfigRepository
from src.models.contracts.config import (
    UpdateConfigRequest,
    ConfigType,
)


def _config_row(*, id: UUID, key: str, org_id: UUID | None, value: str = "v1"):
    row = MagicMock()
    row.id = id
    row.key = key
    row.organization_id = org_id
    row.value = {"value": value}
    row.config_type = MagicMock(value="string")
    row.description = None
    row.updated_at = None
    row.updated_by = None
    return row


def _mk_repo(row):
    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=row)
    session.execute = AsyncMock(return_value=exec_result)
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    return ConfigRepository(session, org_id=None, is_superuser=True)


@pytest.mark.asyncio
async def test_update_returns_old_org_and_key_for_rename():
    """A rename surfaces the old key so the router can invalidate the
    pre-rename cache entry."""
    org = uuid4()
    config_id = uuid4()
    row = _config_row(id=config_id, key="api_token", org_id=org)
    repo = _mk_repo(row)

    request = UpdateConfigRequest(key="api_token_v2", type=ConfigType.STRING)
    update = await repo.update_config_by_id(config_id, request, updated_by="t@example.com")

    assert update is not None
    response, old_org_id, old_key = update
    assert old_key == "api_token"
    assert response.key == "api_token_v2"
    assert old_org_id == org


@pytest.mark.asyncio
async def test_update_returns_old_org_for_org_move():
    """An org-move surfaces the old org so the router can invalidate the
    pre-move cache entry."""
    src_org = uuid4()
    dst_org = uuid4()
    config_id = uuid4()
    row = _config_row(id=config_id, key="api_token", org_id=src_org)
    repo = _mk_repo(row)

    request = UpdateConfigRequest(organization_id=dst_org)
    update = await repo.update_config_by_id(config_id, request, updated_by="t@example.com")

    assert update is not None
    response, old_org_id, old_key = update
    assert old_org_id == src_org
    assert response.org_id == str(dst_org)
    assert old_key == "api_token"


@pytest.mark.asyncio
async def test_update_returns_none_when_row_missing():
    """Missing row returns None as before — no tuple/unpacking foot-gun."""
    repo = _mk_repo(row=None)
    update = await repo.update_config_by_id(
        uuid4(), UpdateConfigRequest(key="x"), updated_by="t@example.com"
    )
    assert update is None


# ---------------------------------------------------------------------------
# Router-level: verify invalidate_config is called with OLD (org, key).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_invalidates_old_cache_entry_on_rename():
    """When ``key`` changes, the router calls ``invalidate_config`` with
    the OLD key before upserting the new one. Pre-fix this was missing."""
    from src.routers.config import update_config

    org = uuid4()
    config_id = uuid4()
    row = _config_row(id=config_id, key="old_name", org_id=org)
    repo = _mk_repo(row)

    ctx = MagicMock()
    ctx.db = repo.session
    ctx.org_id = org
    ctx.user = MagicMock(email="admin@example.com", is_superuser=True)
    user = MagicMock(email="admin@example.com")
    request = UpdateConfigRequest(key="new_name", type=ConfigType.STRING)

    with (
        patch("src.routers.config.ConfigRepository", return_value=repo),
        patch("src.routers.config.invalidate_config", new=AsyncMock()) as inv,
        patch("src.routers.config.upsert_config", new=AsyncMock()) as ups,
        patch("src.routers.config.CACHE_AVAILABLE", new=True),
    ):
        await update_config(config_id, request, ctx, user)

    # OLD (org, key) invalidated
    inv.assert_awaited_once_with(str(org), "old_name")
    # NEW (org, key) upserted
    args = ups.await_args
    assert args is not None
    assert args.args[0] == str(org)
    assert args.args[1] == "new_name"


@pytest.mark.asyncio
async def test_router_does_not_invalidate_when_identity_unchanged():
    """A value-only update should NOT invalidate the existing cache entry
    — the upsert overwrites in place."""
    from src.routers.config import update_config

    org = uuid4()
    config_id = uuid4()
    row = _config_row(id=config_id, key="stable_name", org_id=org)
    repo = _mk_repo(row)

    ctx = MagicMock()
    ctx.db = repo.session
    ctx.org_id = org
    ctx.user = MagicMock(email="admin@example.com", is_superuser=True)
    user = MagicMock(email="admin@example.com")
    request = UpdateConfigRequest(value="new value")

    with (
        patch("src.routers.config.ConfigRepository", return_value=repo),
        patch("src.routers.config.invalidate_config", new=AsyncMock()) as inv,
        patch("src.routers.config.upsert_config", new=AsyncMock()),
        patch("src.routers.config.CACHE_AVAILABLE", new=True),
    ):
        await update_config(config_id, request, ctx, user)

    inv.assert_not_called()


@pytest.mark.asyncio
async def test_router_bumps_global_version_on_org_to_global_transition():
    """Moving a config from org-scoped to global must bump
    CONFIG_GLOBAL_VERSION_KEY so org-merged caches that absorbed the
    old org value re-fetch."""
    from src.routers.config import update_config

    org = uuid4()
    config_id = uuid4()
    row = _config_row(id=config_id, key="api_token", org_id=org)
    repo = _mk_repo(row)

    ctx = MagicMock()
    ctx.db = repo.session
    ctx.org_id = org
    ctx.user = MagicMock(email="admin@example.com", is_superuser=True)
    user = MagicMock(email="admin@example.com")
    request = UpdateConfigRequest(organization_id=None)

    fake_redis = AsyncMock()
    fake_redis.incr = AsyncMock()
    with (
        patch("src.routers.config.ConfigRepository", return_value=repo),
        patch("src.routers.config.invalidate_config", new=AsyncMock()),
        patch("src.routers.config.upsert_config", new=AsyncMock()),
        patch("src.routers.config.CACHE_AVAILABLE", new=True),
        patch("src.core.cache.get_shared_redis", return_value=fake_redis),
    ):
        await update_config(config_id, request, ctx, user)

    fake_redis.incr.assert_awaited()
