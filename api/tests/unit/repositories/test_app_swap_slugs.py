"""ApplicationRepository.swap_slugs exchanges two apps' slugs atomically.

The v1→v2 migration cutover gives the freshly-scaffolded v2 app the live v1
slug (so ``/apps/{slug}`` bookmarks survive) and parks v1 under the temp slug.
The swap must:
- never leave the live slug unowned (one transaction, placeholder bridge),
- never trip the slug unique index mid-swap,
- hold the slug advisory lock for BOTH slugs (so no same-slug deploy interleaves).
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository

pytestmark = pytest.mark.e2e


async def _mk_app(db, slug: str) -> Application:
    app = Application(
        id=uuid.uuid4(), name=slug, slug=slug, repo_path=f"apps/{slug}",
        organization_id=None, solution_id=None,
    )
    db.add(app)
    await db.flush()
    return app


async def test_swap_slugs_exchanges_two_apps(db_session):
    db = db_session
    a = await _mk_app(db, f"orders-{uuid.uuid4().hex[:8]}")
    b = await _mk_app(db, f"orders-v2-{uuid.uuid4().hex[:8]}")
    slug_a, slug_b = a.slug, b.slug

    repo = ApplicationRepository(db, org_id=None, user_id=None, is_superuser=True)
    ra, rb = await repo.swap_slugs(a.id, b.id)

    assert ra.id == a.id and ra.slug == slug_b
    assert rb.id == b.id and rb.slug == slug_a
    # And it sticks across a reload (the placeholder never survived).
    await db.refresh(a)
    await db.refresh(b)
    assert a.slug == slug_b
    assert b.slug == slug_a
    assert not a.slug.startswith("__swap-")


async def test_swap_slugs_takes_advisory_lock_for_both_slugs(db_session, monkeypatch):
    db = db_session
    a = await _mk_app(db, f"alpha-{uuid.uuid4().hex[:8]}")
    b = await _mk_app(db, f"beta-{uuid.uuid4().hex[:8]}")
    slug_a, slug_b = a.slug, b.slug

    locked: list[str] = []
    orig_execute = db.execute

    async def _spy_execute(stmt, params=None, *args, **kwargs):
        if "pg_advisory_xact_lock" in str(stmt) and isinstance(params, dict):
            s = params.get("s")
            if isinstance(s, str):
                locked.append(s)
        return await orig_execute(stmt, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", _spy_execute)
    repo = ApplicationRepository(db, org_id=None, user_id=None, is_superuser=True)
    await repo.swap_slugs(a.id, b.id)

    # App-IDENTITY locks come FIRST (serialize swaps sharing an app), then the
    # slug-string locks (serialize against same-slug deploy/create).
    id_locks = [s for s in locked if "-" in s and s not in (slug_a, slug_b)]
    slug_locks = [s for s in locked if s in (slug_a, slug_b)]
    assert set(id_locks) == {str(a.id), str(b.id)}, locked
    assert set(slug_locks) == {slug_a, slug_b}, locked
    # All id locks precede all slug locks (read slugs under the id locks).
    assert locked.index(slug_locks[0]) > max(locked.index(x) for x in id_locks)
    # Each lock group acquired in sorted order — deterministic → no deadlock.
    assert id_locks == sorted((str(a.id), str(b.id)))
    assert slug_locks == sorted((slug_a, slug_b))


async def test_swap_slugs_rejects_self_swap(db_session):
    db = db_session
    a = await _mk_app(db, f"solo-{uuid.uuid4().hex[:8]}")
    repo = ApplicationRepository(db, org_id=None, user_id=None, is_superuser=True)
    with pytest.raises(ValueError, match="itself"):
        await repo.swap_slugs(a.id, a.id)


async def test_swap_slugs_missing_app_raises(db_session):
    db = db_session
    a = await _mk_app(db, f"present-{uuid.uuid4().hex[:8]}")
    ghost = uuid.uuid4()
    repo = ApplicationRepository(db, org_id=None, user_id=None, is_superuser=True)
    with pytest.raises(ValueError, match="not found"):
        await repo.swap_slugs(a.id, ghost)
