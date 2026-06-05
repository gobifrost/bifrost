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


@pytest.mark.e2e
class TestMultiInstallAppIdentity:
    """Two installs of the same app-bearing solution must NOT collide on the
    app slug/repo_path global unique index (criterion 9, Codex G3)."""

    async def _install(self, db, slug):
        # Two distinct installs (distinct solution_id) — org=None keeps the test
        # free of Organization FK setup; the per-install uniqueness is keyed on
        # solution_id, which differs between the two.
        sol = Solution(id=uuid.uuid4(), slug=slug, name=slug.upper(), organization_id=None)
        db.add(sol)
        await db.flush()
        return sol

    async def test_same_app_slug_two_installs(self, db_session, _stub_app_build):
        db = db_session
        # Two independent installs (different solution_id + org).
        sol_a = await self._install(db, f"mi-a-{uuid.uuid4().hex[:8]}")
        sol_b = await self._install(db, f"mi-b-{uuid.uuid4().hex[:8]}")
        shared_slug = f"dash-{uuid.uuid4().hex[:8]}"
        app_a, app_b = str(uuid.uuid4()), str(uuid.uuid4())

        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol_a, apps=[{**_app_entry(app_a, shared_slug), "repo_path": f"apps/{shared_slug}"}],
        ))
        await db.flush()
        # Second install, SAME slug + repo_path — must not raise unique violation.
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol_b, apps=[{**_app_entry(app_b, shared_slug), "repo_path": f"apps/{shared_slug}"}],
        ))
        await db.flush()

        a = await db.get(Application, uuid.UUID(app_a))
        b = await db.get(Application, uuid.UUID(app_b))
        assert a.solution_id == sol_a.id and b.solution_id == sol_b.id
        assert a.slug == b.slug == shared_slug  # same slug, different installs


@pytest.mark.e2e
class TestAppPublishAndBuildModel:
    """A deployed app is live (published) so /apps/{slug} serves it (P1-c).
    inline_v1 apps are NOT vite-built (P1-g)."""

    async def _install(self, db):
        sol = Solution(id=uuid.uuid4(), slug=f"pb-{uuid.uuid4().hex[:8]}", name="PB", organization_id=None)
        db.add(sol)
        await db.flush()
        return sol

    async def test_deployed_v2_app_is_published(self, db_session, _stub_app_build):
        db = db_session
        sol = await self._install(db)
        app_id = str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, apps=[_app_entry(app_id, "dash")],
        ))
        await db.flush()
        app = await db.get(Application, uuid.UUID(app_id))
        assert app.is_published is True, "deployed app must be live (published)"
        assert app.published_at is not None

    async def test_inline_v1_app_is_not_vite_built(self, db_session, monkeypatch):
        db = db_session
        sol = await self._install(db)
        app_id = str(uuid.uuid4())
        # If build() is called for an inline_v1 app, fail loudly.
        from src.services.solutions import app_build

        async def _boom(self, *a, **k):
            raise AssertionError("inline_v1 app must not be vite-built")
        monkeypatch.setattr(app_build.SolutionAppBuilder, "build", _boom)

        result = await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            apps=[{
                "id": app_id, "slug": "legacy", "name": "Legacy",
                "app_model": "inline_v1", "dependencies": {},
                "src_files": {"pages/index.tsx": "export default 1"},
            }],
        ))
        await db.flush()
        app = await db.get(Application, uuid.UUID(app_id))
        assert app.app_model == "inline_v1"
        assert result.apps_upserted == 1
        # And it's still published (renders via the inline path).
        assert app.is_published is True


@pytest.mark.e2e
class TestDeployTransactionalS3:
    """A deploy that fails DB validation must NOT have mutated S3 (Codex P1-e):
    DB work runs before any S3 write, so an ownership conflict rolls back with
    zero S3 side effects."""

    async def test_failed_deploy_writes_no_s3(self, db_session, monkeypatch):
        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"tx-{uuid.uuid4().hex[:8]}", name="TX", organization_id=None)
        db.add(sol)
        # A _repo/ workflow whose id the bundle will (illegally) reuse → conflict.
        conflict_wf = uuid.uuid4()
        from src.models.orm.workflows import Workflow
        db.add(Workflow(
            id=conflict_wf, name="repo_wf", function_name="run", path="workflows/r.py",
            type="workflow", organization_id=None, solution_id=None, is_active=True,
        ))
        await db.flush()

        # Spy on S3-writing methods — none should be called when the deploy fails.
        from src.services.solutions import app_build
        calls = {"build": 0}

        async def _count_build(self, *a, **k):
            calls["build"] += 1
            return {"index.html": b""}
        monkeypatch.setattr(app_build.SolutionAppBuilder, "build", _count_build)

        wrote_python = {"n": 0}
        orig = SolutionDeployer._write_python

        async def _count_python(self, sid, files):
            wrote_python["n"] += 1
            return await orig(self, sid, files)
        monkeypatch.setattr(SolutionDeployer, "_write_python", _count_python)

        app_id = str(uuid.uuid4())
        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol,
                python_files={"workflows/x.py": "x=1"},
                # The conflicting workflow makes _upsert_workflows raise BEFORE
                # the S3 phase runs.
                workflows=[{"id": str(conflict_wf), "name": "hijack",
                            "function_name": "run", "path": "workflows/r.py"}],
                apps=[_app_entry(app_id, "dash")],
            ))

        # No S3 writes happened (conflict raised during the DB phase).
        assert calls["build"] == 0, "app dist was built despite a failed deploy"
        assert wrote_python["n"] == 0, "python source was written despite a failed deploy"
