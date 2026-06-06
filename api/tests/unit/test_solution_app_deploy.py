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
    solution_entity_id,
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
    """No real vite — compile to a stub dist (sync) + capture uploads.

    The deployer compiles dists pre-commit (compile_dist, sync) and uploads them
    post-commit (upload_dist), so both seams are stubbed."""
    from src.services.solutions import app_build

    uploaded: dict[str, dict] = {}

    def _fake_compile(self, app_id, src_files, dependencies, prebuilt_dist=None):
        return prebuilt_dist or {"index.html": b"<html></html>"}

    async def _fake_upload(self, app_id, dist):
        uploaded[str(app_id)] = dist

    async def _fake_delete(self, app_id):
        uploaded.pop(str(app_id), None)

    monkeypatch.setattr(app_build.SolutionAppBuilder, "compile_dist", _fake_compile)
    monkeypatch.setattr(app_build.SolutionAppBuilder, "upload_dist", _fake_upload, raising=False)
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
        # S3 phase is deferred until after commit (P1-c); run it explicitly.
        await result.finalize_s3()

        expected_id = solution_entity_id(sol.id, uuid.UUID(app_id))
        app = await db.get(Application, expected_id)
        assert app is not None
        assert app.solution_id == sol.id
        assert app.organization_id == sol.organization_id
        assert app.app_model == "standalone_v2"
        assert result.apps_upserted == 1
        # dist was uploaded for this app (under the per-install remapped id)
        assert str(expected_id) in _stub_app_build

    async def test_inline_v1_solution_app_is_rejected(self, db_session, _stub_app_build):
        """Codex #11: a Solution app must be standalone_v2. An inline_v1 app
        (the legacy default when app_model is omitted) has no working deploy path
        — its source is never persisted — so deploy must REJECT it loudly rather
        than create a published-but-sourceless app. No Application row is written."""
        db = db_session
        sol = await self._install(db)
        app_id = str(uuid.uuid4())
        inline = {
            "id": app_id,
            "slug": "legacy",
            "name": "Legacy",
            "app_model": "inline_v1",
            "src_files": {"App.tsx": "export default () => null;"},
        }

        with pytest.raises(SolutionDeployConflict, match="standalone_v2"):
            await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, apps=[inline]))

        # Nothing was persisted for the rejected app.
        expected_id = solution_entity_id(sol.id, uuid.UUID(app_id))
        assert await db.get(Application, expected_id) is None

    async def test_inline_v1_default_when_app_model_omitted_is_rejected(
        self, db_session, _stub_app_build
    ):
        """A manifest entry with NO app_model defaults to inline_v1 — also rejected
        (a bare/legacy manifest can't silently produce a broken app)."""
        db = db_session
        sol = await self._install(db)
        app_id = str(uuid.uuid4())
        bare = {"id": app_id, "slug": "bare", "name": "Bare"}  # no app_model

        with pytest.raises(SolutionDeployConflict, match="standalone_v2"):
            await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, apps=[bare]))

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
        expected_id = solution_entity_id(sol.id, uuid.UUID(app_id))
        assert await db.get(Application, expected_id) is not None

        result = await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, apps=[]))
        await db.flush()

        assert await db.get(Application, expected_id) is None
        assert result.apps_deleted == 1

    async def test_repo_app_id_collision_raises_conflict(self, db_session, _stub_app_build):
        db = db_session
        sol = await self._install(db)
        # A _repo/ app (solution_id IS NULL) already owns the REMAPPED id this
        # bundle's manifest id deploys into. The bundle may not hijack a
        # _repo/-owned id — the conflict fires at the per-install remapped id.
        manifest_id = uuid.uuid4()
        repo_app = Application(
            id=solution_entity_id(sol.id, manifest_id), name="Repo",
            slug=f"repo-{uuid.uuid4().hex[:8]}",
            repo_path="apps/repo", organization_id=None, solution_id=None,
        )
        db.add(repo_app)
        await db.flush()

        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(
                SolutionBundle(solution=sol, apps=[_app_entry(str(manifest_id), "dash")])
            )


@pytest.mark.e2e
class TestMultiInstallAppIdentity:
    """Two installs of the same app-bearing solution to DIFFERENT orgs must NOT
    collide on the app slug/repo_path global unique index (criterion 9, Codex
    G3). Each install is org-scoped, so the same slug under different orgs is
    legitimate — the per-install index allows it AND the route resolver
    disambiguates by org (see TestAppSlugRouteCollision for the same-org case)."""

    async def _install(self, db, slug, org_id):
        # Distinct installs keyed on solution_id, each scoped to its own org —
        # the real multi-install shape (two clients), not two global installs.
        sol = Solution(id=uuid.uuid4(), slug=slug, name=slug.upper(), organization_id=org_id)
        db.add(sol)
        await db.flush()
        return sol

    async def test_same_app_slug_two_installs(self, db_session, _stub_app_build):
        from src.models.orm.organizations import Organization

        db = db_session
        org_a = Organization(id=uuid.uuid4(), name=f"A-{uuid.uuid4().hex[:6]}", created_by="dev@x")
        org_b = Organization(id=uuid.uuid4(), name=f"B-{uuid.uuid4().hex[:6]}", created_by="dev@x")
        db.add_all([org_a, org_b])
        await db.flush()
        # Two independent installs (different solution_id, different org).
        sol_a = await self._install(db, f"mi-a-{uuid.uuid4().hex[:8]}", org_a.id)
        sol_b = await self._install(db, f"mi-b-{uuid.uuid4().hex[:8]}", org_b.id)
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

        a = await db.get(Application, solution_entity_id(sol_a.id, uuid.UUID(app_a)))
        b = await db.get(Application, solution_entity_id(sol_b.id, uuid.UUID(app_b)))
        assert a.solution_id == sol_a.id and b.solution_id == sol_b.id
        assert a.slug == b.slug == shared_slug  # same slug, different installs


@pytest.mark.e2e
class TestAppSlugRouteCollision:
    """The per-install unique index keeps (solution_id, slug) unique but does
    NOT stop a solution app from colliding with another VISIBLE app on the same
    /apps/{slug} route within an org. Two such rows make the slug resolver
    (scalar_one_or_none) raise MultipleResultsFound — the deployed app becomes
    unopenable. Deploy must refuse the collision up front (Codex P2-f).
    """

    async def _install(self, db, org_id=None):
        sol = Solution(
            id=uuid.uuid4(), slug=f"sc-{uuid.uuid4().hex[:8]}", name="SC",
            organization_id=org_id,
        )
        db.add(sol)
        await db.flush()
        return sol

    async def test_solution_app_slug_collides_with_repo_app(self, db_session, _stub_app_build):
        db = db_session
        sol = await self._install(db)  # global install (org=None)
        slug = f"dash-{uuid.uuid4().hex[:8]}"
        # A visible _repo/ app already owns this slug at the same (global) scope.
        db.add(Application(
            id=uuid.uuid4(), name="Repo", slug=slug, repo_path=f"apps/{slug}",
            organization_id=None, solution_id=None,
        ))
        await db.flush()

        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol, apps=[_app_entry(str(uuid.uuid4()), slug)],
            ))

    async def test_two_global_solution_apps_same_slug_refused(self, db_session, _stub_app_build):
        db = db_session
        sol_a = await self._install(db)
        sol_b = await self._install(db)
        slug = f"dash-{uuid.uuid4().hex[:8]}"

        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol_a, apps=[_app_entry(str(uuid.uuid4()), slug)],
        ))
        await db.flush()
        # Different install, but both global (org=None) → same /apps/{slug} route.
        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol_b, apps=[_app_entry(str(uuid.uuid4()), slug)],
            ))

    async def test_same_slug_different_orgs_allowed(self, db_session, _stub_app_build):
        """Cross-org installs sharing a slug is legitimate (criterion 9): the
        resolver disambiguates by org, so deploy must NOT refuse this."""
        from src.models.orm.organizations import Organization

        db = db_session
        org_a = Organization(id=uuid.uuid4(), name=f"A-{uuid.uuid4().hex[:6]}", created_by="dev@x")
        org_b = Organization(id=uuid.uuid4(), name=f"B-{uuid.uuid4().hex[:6]}", created_by="dev@x")
        db.add_all([org_a, org_b])
        await db.flush()
        sol_a = await self._install(db, org_id=org_a.id)
        sol_b = await self._install(db, org_id=org_b.id)
        slug = f"dash-{uuid.uuid4().hex[:8]}"

        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol_a, apps=[_app_entry(str(uuid.uuid4()), slug)],
        ))
        await db.flush()
        # Different org → no route collision → allowed.
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol_b, apps=[_app_entry(str(uuid.uuid4()), slug)],
        ))
        await db.flush()

    async def test_deploy_takes_advisory_lock_on_slug(self, db_session, _stub_app_build, monkeypatch):
        """The check-then-insert is serialized across concurrent deploys by a
        per-slug transaction advisory lock (Codex R5). Assert the lock SQL is
        issued for the app's slug before the row is upserted."""
        db = db_session
        sol = await self._install(db)
        slug = f"dash-{uuid.uuid4().hex[:8]}"

        lock_calls: list[str | None] = []
        orig_execute = db.execute

        async def _spy_execute(stmt, params=None, *a, **k):
            s = str(stmt)
            if "pg_advisory_xact_lock" in s:
                lock_calls.append((params or {}).get("s"))
            return await orig_execute(stmt, params, *a, **k)

        monkeypatch.setattr(db, "execute", _spy_execute)
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, apps=[_app_entry(str(uuid.uuid4()), slug)],
        ))
        await db.flush()
        assert slug in lock_calls, "no advisory lock taken on the app slug"

    async def test_org_install_refused_against_visible_global_app(self, db_session, _stub_app_build):
        """An ORG install must not stomp a GLOBAL app's slug — that org sees
        both (global apps are visible to every org). Codex R4."""
        from src.models.orm.organizations import Organization

        db = db_session
        org = Organization(id=uuid.uuid4(), name=f"O-{uuid.uuid4().hex[:6]}", created_by="dev@x")
        db.add(org)
        await db.flush()
        slug = f"dash-{uuid.uuid4().hex[:8]}"
        # Pre-existing GLOBAL app with this slug.
        db.add(Application(
            id=uuid.uuid4(), name="G", slug=slug, repo_path=f"apps/{slug}",
            organization_id=None, solution_id=None,
        ))
        await db.flush()
        sol = await self._install(db, org_id=org.id)  # org-scoped install
        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol, apps=[_app_entry(str(uuid.uuid4()), slug)],
            ))

    async def test_binary_assets_reach_the_builder_decoded(self, db_session, monkeypatch):
        """bin_files (base64) in the bundle are decoded to bytes and merged into
        the builder's src_files, so a v2 app's PNG/fonts actually build (P2-j/R4)."""
        import base64

        from src.services.solutions import app_build

        captured: dict = {}

        def _capture_compile(self, app_id, src_files, dependencies, prebuilt_dist=None):
            captured["src_files"] = src_files
            return {"index.html": b"<html></html>"}

        async def _noop_upload(self, app_id, dist):
            return None

        async def _noop_delete(self, app_id):
            return None

        monkeypatch.setattr(app_build.SolutionAppBuilder, "compile_dist", _capture_compile)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "upload_dist", _noop_upload, raising=False)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "delete_dist", _noop_delete, raising=False)

        db = db_session
        sol = await self._install(db)
        png = b"\x89PNG\x00data"
        result = await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            apps=[{
                "id": str(uuid.uuid4()), "slug": f"dash-{uuid.uuid4().hex[:6]}",
                "name": "Dash", "app_model": "standalone_v2", "dependencies": {},
                "src_files": {"src/main.tsx": "import './logo.png'"},
                "bin_files": {"logo.png": base64.b64encode(png).decode()},
                # no dist_files → goes through the real build path (stubbed)
            }],
        ))
        await db.flush()
        await result.finalize_s3()
        # The decoded PNG bytes reached the builder (at compile time, pre-commit).
        assert captured["src_files"]["logo.png"] == png
        assert captured["src_files"]["src/main.tsx"] == b"import './logo.png'"

    async def test_global_install_refused_against_visible_org_app(self, db_session, _stub_app_build):
        """A GLOBAL install must not take a slug already used by an ORG app —
        that org would then see two apps at /apps/{slug}. Codex R4."""
        from src.models.orm.organizations import Organization

        db = db_session
        org = Organization(id=uuid.uuid4(), name=f"O-{uuid.uuid4().hex[:6]}", created_by="dev@x")
        db.add(org)
        await db.flush()
        slug = f"dash-{uuid.uuid4().hex[:8]}"
        # Pre-existing ORG-scoped app with this slug.
        db.add(Application(
            id=uuid.uuid4(), name="OrgApp", slug=slug, repo_path=f"apps/{slug}",
            organization_id=org.id, solution_id=None,
        ))
        await db.flush()
        sol = await self._install(db, org_id=None)  # global install
        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol, apps=[_app_entry(str(uuid.uuid4()), slug)],
            ))


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
        app = await db.get(Application, solution_entity_id(sol.id, uuid.UUID(app_id)))
        assert app.is_published is True, "deployed app must be live (published)"
        assert app.published_at is not None

    async def test_inline_v1_app_is_rejected_not_built(self, db_session, monkeypatch):
        """An inline_v1 Solution app is REJECTED at deploy (Codex #11): it has no
        working deploy path here (source dropped, no _apps build), so rather than
        persist a published-but-broken app, deploy raises before any row write —
        and certainly never attempts a build."""
        db = db_session
        sol = await self._install(db)
        app_id = str(uuid.uuid4())
        # If build() is even attempted for an inline_v1 app, fail loudly.
        from src.services.solutions import app_build

        def _boom(self, *a, **k):
            raise AssertionError("inline_v1 app must not be vite-built")
        monkeypatch.setattr(app_build.SolutionAppBuilder, "compile_dist", _boom)

        with pytest.raises(SolutionDeployConflict, match="standalone_v2"):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol,
                apps=[{
                    "id": app_id, "slug": "legacy", "name": "Legacy",
                    "app_model": "inline_v1", "dependencies": {},
                    "src_files": {"pages/index.tsx": "export default 1"},
                }],
            ))
        # No row was written for the rejected app.
        assert await db.get(Application, solution_entity_id(sol.id, uuid.UUID(app_id))) is None


@pytest.mark.e2e
class TestDeployTransactionalS3:
    """A deploy that fails DB validation must NOT have mutated S3 (Codex P1-e):
    DB work runs before any S3 write, so an ownership conflict rolls back with
    zero S3 side effects."""

    async def test_failed_deploy_writes_no_s3(self, db_session, monkeypatch):
        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"tx-{uuid.uuid4().hex[:8]}", name="TX", organization_id=None)
        db.add(sol)
        # A _repo/ workflow whose REMAPPED id the bundle will (illegally) reuse →
        # conflict. The manifest id is remapped before the ownership guard, so the
        # pre-seeded _repo/ row must carry the per-install remapped id to collide.
        conflict_manifest_id = uuid.uuid4()
        conflict_wf = solution_entity_id(sol.id, conflict_manifest_id)
        from src.models.orm.workflows import Workflow
        db.add(Workflow(
            id=conflict_wf, name="repo_wf", function_name="run", path="workflows/r.py",
            type="workflow", organization_id=None, solution_id=None, is_active=True,
        ))
        await db.flush()

        # Spy on the build seams — none should run when the deploy fails in the
        # DB phase (the conflict raises BEFORE compile, which is before upload).
        from src.services.solutions import app_build
        calls = {"compile": 0, "upload": 0}

        def _count_compile(self, *a, **k):
            calls["compile"] += 1
            return {"index.html": b""}

        async def _count_upload(self, *a, **k):
            calls["upload"] += 1
        monkeypatch.setattr(app_build.SolutionAppBuilder, "compile_dist", _count_compile)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "upload_dist", _count_upload, raising=False)

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
                # the S3 phase runs (its manifest id remaps onto conflict_wf).
                workflows=[{"id": str(conflict_manifest_id), "name": "hijack",
                            "function_name": "run", "path": "workflows/r.py"}],
                apps=[_app_entry(app_id, "dash")],
            ))

        # No build/upload/python-write happened (conflict raised in the DB phase,
        # before compile and before the post-commit finalize).
        assert calls["compile"] == 0, "app dist was compiled despite a failed deploy"
        assert calls["upload"] == 0, "app dist was uploaded despite a failed deploy"
        assert wrote_python["n"] == 0, "python source was written despite a failed deploy"

    async def test_build_failure_rolls_back_and_uploads_nothing(self, db_session, monkeypatch):
        """A vite/npm BUILD error must raise from deploy() (pre-commit), so the
        caller never commits and uploads nothing — DB never ends up ahead of S3
        (Codex R4 atomicity). The compile runs before commit; if it throws, the
        deploy is abandoned with no upload and no DeployResult to finalize."""
        from src.services.solutions import app_build

        uploaded = {"n": 0}

        def _compile_boom(self, *a, **k):
            raise RuntimeError("vite build failed: missing import './nope.png'")

        async def _count_upload(self, *a, **k):
            uploaded["n"] += 1

        monkeypatch.setattr(app_build.SolutionAppBuilder, "compile_dist", _compile_boom)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "upload_dist", _count_upload, raising=False)

        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"bf-{uuid.uuid4().hex[:8]}", name="BF", organization_id=None)
        db.add(sol)
        await db.flush()
        app_id = str(uuid.uuid4())

        with pytest.raises(RuntimeError, match="vite build failed"):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol,
                apps=[{
                    "id": app_id, "slug": f"dash-{uuid.uuid4().hex[:6]}", "name": "D",
                    "app_model": "standalone_v2", "dependencies": {},
                    "src_files": {"src/main.tsx": "import './nope.png'"},
                }],
            ))

        # The build error happened BEFORE any upload, and the caller would not
        # commit — so nothing was uploaded.
        assert uploaded["n"] == 0, "dist uploaded despite a failed build"

    async def test_finalize_retries_a_transient_upload_failure(self, db_session, monkeypatch):
        """A transient storage blip during post-commit finalize is absorbed by
        retrying the idempotent step — the deploy completes, no error (Codex R5)."""
        import src.services.solutions.deploy as deploy_mod
        from src.services.solutions import app_build

        monkeypatch.setattr(deploy_mod, "_FINALIZE_BACKOFF_S", 0)  # fast test

        attempts = {"n": 0}

        def _compile(self, *a, **k):
            return {"index.html": b"<html></html>"}

        async def _flaky_upload(self, *a, **k):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient S3 reset")
            # second attempt succeeds

        async def _noop_delete(self, *a, **k):
            return None

        monkeypatch.setattr(app_build.SolutionAppBuilder, "compile_dist", _compile)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "upload_dist", _flaky_upload, raising=False)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "delete_dist", _noop_delete, raising=False)

        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"fz-{uuid.uuid4().hex[:8]}", name="FZ", organization_id=None)
        db.add(sol)
        await db.flush()
        result = await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, apps=[_app_entry(str(uuid.uuid4()), f"d-{uuid.uuid4().hex[:6]}")],
        ))
        await db.flush()
        await result.finalize_s3()  # must NOT raise — the retry succeeds
        assert attempts["n"] == 2, "upload was not retried after a transient failure"

    async def test_finalize_raises_when_storage_stays_down(self, db_session, monkeypatch):
        """If a finalize step fails every retry (a real outage), finalize raises
        SolutionFinalizeIncomplete so the caller can surface a retry (Codex R5)."""
        import src.services.solutions.deploy as deploy_mod
        from src.services.solutions import app_build
        from src.services.solutions.deploy import SolutionFinalizeIncomplete

        monkeypatch.setattr(deploy_mod, "_FINALIZE_BACKOFF_S", 0)

        def _compile(self, *a, **k):
            return {"index.html": b"<html></html>"}

        async def _dead_upload(self, *a, **k):
            raise RuntimeError("S3 unavailable")

        async def _noop_delete(self, *a, **k):
            return None

        monkeypatch.setattr(app_build.SolutionAppBuilder, "compile_dist", _compile)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "upload_dist", _dead_upload, raising=False)
        monkeypatch.setattr(app_build.SolutionAppBuilder, "delete_dist", _noop_delete, raising=False)

        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"fz-{uuid.uuid4().hex[:8]}", name="FZ", organization_id=None)
        db.add(sol)
        await db.flush()
        result = await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, apps=[_app_entry(str(uuid.uuid4()), f"d-{uuid.uuid4().hex[:6]}")],
        ))
        await db.flush()
        with pytest.raises(SolutionFinalizeIncomplete):
            await result.finalize_s3()
