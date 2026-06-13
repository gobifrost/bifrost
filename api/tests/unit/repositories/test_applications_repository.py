"""ApplicationRepository.create_application must serialize against solution
deploys of the same slug.

deploy.py takes `pg_advisory_xact_lock(hashtext('bifrost:appslug:' || slug))`
precisely to make the SELECT-then-INSERT atomic across concurrent same-slug
writers. create_application does the same SELECT-then-INSERT into a DISJOINT
partial unique index — without taking the same lock, a racing pair lands two
same-slug rows and every subsequent /apps/{slug} open 500s with
MultipleResultsFound. Mirrors test_deploy_takes_advisory_lock_on_slug.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.contracts.applications import ApplicationCreate
from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository

pytestmark = pytest.mark.e2e


async def test_create_application_takes_slug_advisory_lock_first(db_session, monkeypatch):
    """The FIRST statement create_application executes is the per-slug advisory
    lock — BEFORE the duplicate-check SELECT, so a racing deploy/create pair
    blocks instead of both passing the check."""
    db = db_session
    slug = f"dash-{uuid.uuid4().hex[:8]}"
    # Pre-existing app with this slug → create_application raises ValueError
    # AFTER the lock + duplicate-check SELECT (keeps the test off the
    # file-scaffolding path; the lock-ordering claim is identical).
    db.add(Application(
        id=uuid.uuid4(), name="Existing", slug=slug, repo_path=f"apps/{slug}",
        organization_id=None, solution_id=None,
    ))
    await db.flush()

    statements: list[tuple[str, str | None]] = []
    orig_execute = db.execute

    async def _spy_execute(stmt, params=None, *a, **k):
        statements.append((str(stmt), (params or {}).get("s") if isinstance(params, dict) else None))
        return await orig_execute(stmt, params, *a, **k)

    monkeypatch.setattr(db, "execute", _spy_execute)

    repo = ApplicationRepository(db, org_id=None, user_id=None, is_superuser=True)
    with pytest.raises(ValueError, match="already exists"):
        # inline_v1: this test is about the slug advisory lock, not the v2 bark
        # (a bare create defaults to v2 now and would refuse before the dup check).
        await repo.create_application(
            ApplicationCreate(name="Dup", slug=slug, app_model="inline_v1"),
            created_by="dev@x",
        )

    assert statements, "create_application executed no statements"
    first_sql, first_param = statements[0]
    assert "pg_advisory_xact_lock" in first_sql, (
        f"first statement was not the advisory lock: {first_sql}"
    )
    assert "bifrost:appslug:" in first_sql
    assert first_param == slug
    # The duplicate-check SELECT happens AFTER the lock.
    assert any(
        "pg_advisory_xact_lock" not in sql and "applications" in sql.lower()
        for sql, _ in statements[1:]
    ), "duplicate-check SELECT not found after the lock"


async def test_create_application_barks_on_v2_default(db_session):
    """A bare `apps create` defaults to standalone_v2, but a loose (non-solution)
    v2 app can never render (only a Solution deploy builds its dist). So
    create_application REFUSES v2 with a message pointing at the Solution flow.
    Solution apps are created by deploy, not this path."""
    repo = ApplicationRepository(db_session, org_id=None, user_id=None, is_superuser=True)
    with pytest.raises(ValueError, match="Solution"):
        await repo.create_application(
            ApplicationCreate(name="V2 loose", slug=f"v2-{uuid.uuid4().hex[:8]}"),
            created_by="dev@x",
        )


async def test_create_application_default_is_v2():
    """The contract default flipped to standalone_v2 (v2 is the future; v1 is the
    explicit legacy opt-in)."""
    assert ApplicationCreate(name="x", slug="y").app_model == "standalone_v2"


async def test_create_application_inline_v1_still_allowed(db_session):
    """inline_v1 is the standalone/legacy model and is still creatable here."""
    repo = ApplicationRepository(db_session, org_id=None, user_id=None, is_superuser=True)
    slug = f"v1-{uuid.uuid4().hex[:8]}"
    app = await repo.create_application(
        ApplicationCreate(name="V1", slug=slug, app_model="inline_v1"),
        created_by="dev@x",
    )
    assert app.app_model == "inline_v1"
