"""Unit tests for ApplicationRepository.replace_application (repoint).

These tests use the real ``db_session`` fixture backed by PostgreSQL to
exercise the uniqueness/nesting/source-exists validation logic against
actual ORM behavior. They're placed under ``tests/unit/`` because they
target a single repository method in isolation, but they still require
the Docker test stack to be up (``./test.sh``).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.applications import Application
from src.models.orm.file_index import FileIndex
from src.routers.applications import ApplicationRepository


@pytest_asyncio.fixture
async def repo(db_session: AsyncSession) -> ApplicationRepository:
    # org_id=None → global scope. bypass_resource_roles=True bypasses per-entity role
    # checks so the tests focus on replace_application's own validation logic.
    return ApplicationRepository(
        session=db_session,
        org_id=None,
        bypass_resource_roles=True,
    )


async def _make_app(db: AsyncSession, *, slug: str, repo_path: str) -> Application:
    app = Application(
        id=uuid4(),
        name=slug,
        slug=slug,
        repo_path=repo_path,
        access_level="authenticated",
    )
    db.add(app)
    await db.flush()
    return app


async def _seed_file(db: AsyncSession, path: str) -> None:
    db.add(FileIndex(path=path, content="// stub", content_hash="x"))
    await db.flush()


@pytest.mark.asyncio
async def test_replace_repoints_when_all_checks_pass(repo, db_session):
    app = await _make_app(db_session, slug="foo", repo_path="apps/foo")
    await _seed_file(db_session, "apps/foo-v2/index.tsx")

    result = await repo.replace_application(app.id, "apps/foo-v2", force=False)

    assert result is not None
    assert result.repo_path == "apps/foo-v2"


@pytest.mark.asyncio
async def test_replace_noop_when_path_unchanged(repo, db_session):
    app = await _make_app(db_session, slug="foo", repo_path="apps/foo")
    # No file seeded — no-op should not trigger source-exists check.

    result = await repo.replace_application(app.id, "apps/foo", force=False)

    assert result is not None
    assert result.repo_path == "apps/foo"


@pytest.mark.asyncio
async def test_replace_rejects_duplicate_repo_path(repo, db_session):
    app_a = await _make_app(db_session, slug="a", repo_path="apps/a")
    await _make_app(db_session, slug="b", repo_path="apps/taken")
    await _seed_file(db_session, "apps/taken/index.tsx")

    with pytest.raises(ValueError, match="already claimed"):
        await repo.replace_application(app_a.id, "apps/taken", force=False)


@pytest.mark.asyncio
async def test_replace_rejects_nested_under_existing(repo, db_session):
    app_a = await _make_app(db_session, slug="a", repo_path="apps/a")
    await _make_app(db_session, slug="outer", repo_path="apps/outer")
    await _seed_file(db_session, "apps/outer/sub/index.tsx")

    with pytest.raises(ValueError, match="nested"):
        await repo.replace_application(app_a.id, "apps/outer/sub", force=False)


@pytest.mark.asyncio
async def test_replace_rejects_existing_nested_under_target(repo, db_session):
    app_a = await _make_app(db_session, slug="a", repo_path="apps/a")
    await _make_app(db_session, slug="inner", repo_path="apps/outer/inner")
    await _seed_file(db_session, "apps/outer/index.tsx")

    with pytest.raises(ValueError, match="nested"):
        await repo.replace_application(app_a.id, "apps/outer", force=False)


@pytest.mark.asyncio
async def test_replace_rejects_empty_prefix(repo, db_session):
    app = await _make_app(db_session, slug="foo", repo_path="apps/foo")

    with pytest.raises(ValueError, match="no files"):
        await repo.replace_application(app.id, "apps/does-not-exist", force=False)


@pytest.mark.asyncio
async def test_force_bypasses_uniqueness_then_db_integrity_catches_it(repo, db_session):
    app_a = await _make_app(db_session, slug="a", repo_path="apps/a")
    await _make_app(db_session, slug="b", repo_path="apps/taken")
    await _seed_file(db_session, "apps/taken/index.tsx")

    # force bypass skips application-layer check. The DB unique constraint is
    # the final guard — verify it fires on commit.
    with pytest.raises(Exception):  # IntegrityError on commit
        await repo.replace_application(app_a.id, "apps/taken", force=True)
        await db_session.commit()


@pytest.mark.asyncio
async def test_force_bypasses_nesting(repo, db_session):
    app_a = await _make_app(db_session, slug="a", repo_path="apps/a")
    await _make_app(db_session, slug="outer", repo_path="apps/outer")
    await _seed_file(db_session, "apps/outer/index.tsx")

    result = await repo.replace_application(app_a.id, "apps/outer/sub", force=True)
    assert result.repo_path == "apps/outer/sub"


@pytest.mark.asyncio
async def test_force_bypasses_source_exists(repo, db_session):
    app = await _make_app(db_session, slug="foo", repo_path="apps/foo")

    result = await repo.replace_application(app.id, "apps/empty", force=True)
    assert result.repo_path == "apps/empty"
