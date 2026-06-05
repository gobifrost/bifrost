"""Sub-plan 6 Task 4 — Solution app deploy: v2 apps build + scoped reconcile.

Criterion 12: deploying a Solution with a v2 app stamps the Application row with
``solution_id`` + inherited scope + ``app_model=standalone_v2`` and ships its
built ``dist/`` to ``_apps/{id}/``. A redeploy without the app removes it for
THIS install only; an id collision with a ``_repo/`` or other-install app raises
``SolutionDeployConflict``. The server vite build is stubbed (prebuilt dist) so
no real Node toolchain runs in unit tests.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployConflict,
    SolutionDeployer,
)


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    import src.core.redis_client as rc
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.fixture(autouse=True)
def _stub_app_build(monkeypatch):
    """No real vite — capture the dist that would be uploaded."""
    from src.services.solutions import app_build

    uploaded: dict[str, dict] = {}

    async def _fake_build(self, app_id, src_files, dependencies, prebuilt_dist=None):
        dist = prebuilt_dist or {"index.html": b"<html></html>"}
        uploaded[str(app_id)] = dist
        return dist

    async def _fake_delete(self, app_id):
        uploaded.pop(str(app_id), None)

    monkeypatch.setattr(app_build.SolutionAppBuilder, "build", _fake_build)
    monkeypatch.setattr(
        app_build.SolutionAppBuilder, "delete_dist", _fake_delete, raising=False
    )
    return uploaded


def _app_entry(app_id: str, slug: str) -> dict:
    return {
        "id": app_id,
        "slug": slug,
        "name": slug.title(),
        "app_model": "standalone_v2",
        "dependencies": {},
        "dist_files": {"index.html": "<html></html>"},
        "access_level": "authenticated",
    }


@pytest.mark.e2e
class TestSolutionAppDeploy:
    async def _install(self, db, org_id=None) -> Solution:
        sol = Solution(
            id=uuid.uuid4(),
            slug=f"app-{uuid.uuid4().hex[:8]}",
            name="APP",
            organization_id=org_id,
        )
        db.add(sol)
        await db.flush()
        return sol

    async def test_deploy_v2_app_stamps_model_and_scope(self, db_session, _stub_app_build):
        db = db_session
        sol = await self._install(db)
        app_id = str(uuid.uuid4())

        result = await SolutionDeployer(db).deploy(
            SolutionBundle(solution=sol, apps=[_app_entry(app_id, "dash")])
        )
        await db.flush()

        app = await db.get(Application, uuid.UUID(app_id))
        assert app is not None
        assert app.solution_id == sol.id
        assert app.organization_id == sol.organization_id
        assert app.app_model == "standalone_v2"
        assert result.apps_upserted == 1
        # dist was uploaded for this app
        assert app_id in _stub_app_build

    async def test_redeploy_without_app_removes_for_this_install(
        self, db_session, _stub_app_build
    ):
        db = db_session
        sol = await self._install(db)
        app_id = str(uuid.uuid4())

        await SolutionDeployer(db).deploy(
            SolutionBundle(solution=sol, apps=[_app_entry(app_id, "dash")])
        )
        await db.flush()
        assert await db.get(Application, uuid.UUID(app_id)) is not None

        result = await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, apps=[]))
        await db.flush()

        assert await db.get(Application, uuid.UUID(app_id)) is None
        assert result.apps_deleted == 1

    async def test_repo_app_id_collision_raises_conflict(self, db_session, _stub_app_build):
        db = db_session
        sol = await self._install(db)
        # A _repo/ app (solution_id IS NULL) with this id already exists.
        app_id = uuid.uuid4()
        repo_app = Application(
            id=app_id, name="Repo", slug=f"repo-{uuid.uuid4().hex[:8]}",
            repo_path="apps/repo", organization_id=None, solution_id=None,
        )
        db.add(repo_app)
        await db.flush()

        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(
                SolutionBundle(solution=sol, apps=[_app_entry(str(app_id), "dash")])
            )
