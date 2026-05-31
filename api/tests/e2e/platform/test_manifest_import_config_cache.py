"""Regression: manifest/git-sync import must invalidate the config cache.

``ConfigRepository.get_config`` / ``merged_for_sdk`` are read-through cached
(per-org Redis hash, see ``api/src/routers/config.py`` and
``api/src/core/cache/keys.py``). The normal write path
(``PUT /api/config/{id}``) invalidates on rename/move. But manifest import
(``import_manifest_from_repo``) writes ``Config`` rows directly via raw
upsert/delete ops, bypassing that handler.

Without the fix, a git-sync import that deletes (or renames/moves) a config
leaves the old entry live in the cache until TTL — the SDK keeps reading the
stale value. These tests pin the fix: the import drains
``ManifestResolver.configs_touched`` and calls ``invalidate_config`` for each
affected ``(org, key)`` after the transaction commits.

They write ``.bifrost/configs.yaml`` straight to S3 and call
``import_manifest_from_repo`` (no git), mirroring ``test_manifest_import.py``.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.cache.keys import config_hash_key_versioned
from src.core.cache.redis_client import get_shared_redis
from src.models.orm.config import Config
from src.repositories.config import ConfigRepository
from src.services.manifest_import import import_manifest_from_repo
from src.services.repo_storage import RepoStorage


@pytest_asyncio.fixture
async def repo_storage() -> RepoStorage:
    settings = get_settings()
    return RepoStorage(settings)


@pytest_asyncio.fixture
async def cleanup(db_session: AsyncSession, repo_storage: RepoStorage):
    yield
    # Best-effort cleanup of the S3 manifest + any configs created.
    try:
        await repo_storage.delete(".bifrost/configs.yaml")
    except Exception:
        # Teardown is best-effort — the manifest may not exist if the test
        # bailed before writing it. Nothing to recover; ignore.
        pass


def _yaml(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=True)


async def _warm_cache_for_global(db: AsyncSession) -> dict:
    """Populate the global-scope config cache via the read-through path."""
    repo = ConfigRepository(db, org_id=None, is_superuser=True)
    return await repo.merged_for_sdk()


async def _read_cache_global() -> dict:
    """Read the global config cache hash directly from Redis."""
    r = await get_shared_redis()
    hash_key = await config_hash_key_versioned(r, None)
    raw = await r.hgetall(hash_key)  # type: ignore[misc]
    out: dict = {}
    for k, v in (raw or {}).items():
        ks = k.decode() if isinstance(k, bytes) else k
        vs = v.decode() if isinstance(v, bytes) else v
        try:
            out[ks] = json.loads(vs)
        except json.JSONDecodeError:
            out[ks] = vs
    return out


@pytest.mark.e2e
@pytest.mark.asyncio
class TestManifestImportConfigCache:
    async def test_import_delete_invalidates_cached_config(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup,
    ):
        """A config present in cache but deleted by import must leave the cache.

        Without the invalidation hook the deleted key keeps being served from
        the global config hash for the full TTL.
        """
        key = f"mi_cache_del_{uuid4().hex[:8]}"

        # Seed a global, non-integration config directly in the DB.
        cfg = Config(
            id=uuid4(),
            key=key,
            organization_id=None,
            value={"value": "stale_value"},
            updated_by="test",
        )
        db_session.add(cfg)
        await db_session.commit()

        # Start from a clean global cache so the warm step re-merges from DB
        # and includes the just-seeded key (a prior test's write-through can
        # otherwise leave a stale global hash — test-order state pollution).
        from src.core.cache import invalidate_config
        await invalidate_config(None, None)

        # Warm the read-through cache; confirm the key is cached.
        merged = await _warm_cache_for_global(db_session)
        assert key in merged, "precondition: config should be in the merged read"
        cached_before = await _read_cache_global()
        assert key in cached_before, "precondition: config should be in the cache hash"

        # Import an EMPTY configs manifest with deletes enabled — this deletes
        # the seeded config (it is config_schema_id IS NULL, not in manifest).
        await repo_storage.write(
            ".bifrost/configs.yaml",
            _yaml({"configs": {}}).encode("utf-8"),
        )
        result = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=True,
            dry_run=False,
        )
        await db_session.commit()
        assert result.applied is True

        # The cache entry for the deleted config must be gone (invalidated),
        # not lingering until TTL.
        cached_after = await _read_cache_global()
        assert key not in cached_after, (
            f"Deleted config '{key}' still present in the config cache after "
            f"import — manifest import did not invalidate the cache. "
            f"Cache: {list(cached_after)}"
        )

    async def test_import_value_change_invalidates_cached_config(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup,
    ):
        """An import that changes a non-secret value must drop the stale cache."""
        key = f"mi_cache_upd_{uuid4().hex[:8]}"
        cfg_id = uuid4()

        cfg = Config(
            id=cfg_id,
            key=key,
            organization_id=None,
            value={"value": "old_value"},
            updated_by="test",
        )
        db_session.add(cfg)
        await db_session.commit()

        # Clean global cache first (avoid stale cross-test state), then warm.
        from src.core.cache import invalidate_config
        await invalidate_config(None, None)
        await _warm_cache_for_global(db_session)
        cached_before = await _read_cache_global()
        assert cached_before.get(key, {}).get("value") == "old_value"

        # Import the same config id with a new value.
        await repo_storage.write(
            ".bifrost/configs.yaml",
            _yaml({
                "configs": {
                    str(cfg_id): {
                        "id": str(cfg_id),
                        "key": key,
                        "config_type": "string",
                        "organization_id": None,
                        "value": "new_value",
                    }
                }
            }).encode("utf-8"),
        )
        result = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=False,
        )
        await db_session.commit()
        assert result.applied is True

        # Sanity: the import actually updated the DB value (stored value can be
        # the raw scalar or wrapped in {"value": ...} depending on serialize).
        db_row = (
            await db_session.execute(
                select(Config.value).where(Config.id == cfg_id)
            )
        ).scalar_one()
        db_val = db_row.get("value") if isinstance(db_row, dict) else db_row
        assert db_val == "new_value", (
            f"precondition: import should have written new_value to DB; "
            f"got {db_row!r} (entity_changes={result.entity_changes})"
        )

        # The import invalidated the cache, so a fresh read re-merges from the
        # (now-updated) DB and returns the new value rather than the stale one.
        #
        # expire_all() drops this test session's identity-map snapshot so the
        # merged_for_sdk SELECT actually round-trips to the DB. In production
        # the SDK read happens on a fresh session/request, so this is a
        # test-harness concern only — not something the cache fix must handle.
        db_session.expire_all()
        repo = ConfigRepository(db_session, org_id=None, is_superuser=True)
        merged = await repo.merged_for_sdk()
        assert merged.get(key, {}).get("value") == "new_value", (
            f"Config '{key}' served stale value after import. Got: {merged.get(key)}"
        )

    async def test_clean_db_config_rows(self, db_session: AsyncSession):
        """Housekeeping: remove configs created by these tests."""
        await db_session.execute(
            delete(Config).where(Config.key.like("mi_cache_%"))
        )
        await db_session.commit()
