from __future__ import annotations

import uuid
import zipfile
from io import BytesIO

import pytest
import yaml
from sqlalchemy import select

from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.solutions import Solution
from src.models.orm.tables import Document, Table
from src.services.solutions.capture import (
    SolutionCaptureConflict,
    SolutionCaptureSelectors,
    SolutionCaptureService,
)
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer
from src.services.solutions.export import build_workspace_zip


pytestmark = pytest.mark.e2e


async def _make_solution(db, slug: str = "capture", *, org_id=None) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"{slug}-{uuid.uuid4().hex[:8]}",
        name="Capture",
        organization_id=org_id,
    )
    db.add(sol)
    await db.flush()
    return sol


class _FakeRepo:
    """In-memory stand-in for RepoStorage: maps repo paths to byte content."""

    def __init__(self, files: dict[str, bytes]):
        self._files = files

    async def list(self, prefix: str = "") -> list[str]:
        return [p for p in self._files if p.startswith(prefix)]

    async def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:  # mirror S3 NoSuchKey surfacing
            raise FileNotFoundError(path) from exc


async def test_capture_table_keeps_rows_and_exports_manifest(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    table = Table(
        id=uuid.uuid4(),
        name="documents",
        organization_id=None,
        solution_id=None,
        schema={"columns": [{"name": "title", "type": "string"}]},
        access={"policies": [{"name": "admin", "actions": ["read"], "when": {"user": "is_platform_admin"}}]},
    )
    db.add(table)
    db.add(Document(table_id=table.id, id="row-1", data={"title": "Keep me"}))
    await db.flush()

    result = await SolutionCaptureService(db).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[],
            tables=[table.id],
            apps=[],
            forms=[],
            agents=[],
            claims=[],
            configs=[],
        ),
    )
    await db.flush()

    captured = await db.get(Table, table.id)
    assert captured is not None
    assert captured.solution_id == sol.id
    assert (await db.get(Document, (table.id, "row-1"))) is not None
    assert result.tables_captured == 1

    bundle = await SolutionCaptureService(db).bundle_for(sol)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        tables_yaml = yaml.safe_load(zf.read(".bifrost/tables.yaml"))
    exported = tables_yaml["tables"][str(table.id)]
    assert exported["name"] == "documents"
    assert exported["schema"]["columns"][0]["name"] == "title"


async def test_capture_config_declares_existing_value_without_copying_value(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    config = Config(
        id=uuid.uuid4(),
        key="RTM_API_KEY",
        value={"value": "secret"},
        config_type=ConfigType.SECRET,
        description="RTM API key",
        organization_id=None,
        updated_by="test",
    )
    db.add(config)
    await db.flush()

    result = await SolutionCaptureService(db).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[],
            tables=[],
            apps=[],
            forms=[],
            agents=[],
            claims=[],
            configs=["RTM_API_KEY"],
        ),
    )
    await db.flush()

    assert result.config_declarations_captured == 1
    decl = (
        await db.execute(
            select(Config).where(Config.key == "RTM_API_KEY", Config.organization_id.is_(None))
        )
    ).scalar_one()
    assert decl.value == {"value": "secret"}
    bundle = await SolutionCaptureService(db).bundle_for(sol)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        configs_yaml = yaml.safe_load(zf.read(".bifrost/configs.yaml"))
    exported = configs_yaml["configs"]["RTM_API_KEY"]
    assert exported["key"] == "RTM_API_KEY"
    assert exported["type"] == "secret"
    assert "value" not in exported


async def test_capture_rejects_wrong_scope(db_session) -> None:
    from src.models.orm.organizations import Organization

    db = db_session
    org = Organization(id=uuid.uuid4(), name=f"Other-{uuid.uuid4().hex[:8]}", created_by="test")
    db.add(org)
    sol = await _make_solution(db)
    table = Table(id=uuid.uuid4(), name="org_table", organization_id=org.id)
    db.add(table)
    await db.flush()

    with pytest.raises(SolutionCaptureConflict, match="scoped to"):
        await SolutionCaptureService(db).capture(
            sol,
            SolutionCaptureSelectors(
                workflows=[],
                tables=[table.id],
                apps=[],
                forms=[],
                agents=[],
                claims=[],
                configs=[],
            ),
        )


async def test_same_install_deploy_preserves_captured_table_identity(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    table = Table(id=uuid.uuid4(), name="documents", solution_id=sol.id)
    db.add(table)
    db.add(Document(table_id=table.id, id="row-1", data={"title": "Keep me"}))
    await db.flush()

    await SolutionDeployer(db).deploy(
        SolutionBundle(
            solution=sol,
            tables=[{
                "id": str(table.id),
                "name": "documents",
                "schema": {"columns": [{"name": "title", "type": "string"}]},
            }],
        )
    )
    await db.flush()

    rows = (
        await db.execute(select(Table.id).where(Table.solution_id == sol.id))
    ).scalars().all()
    assert rows == [table.id]
    assert (await db.get(Document, (table.id, "row-1"))) is not None


# ── Task 6a: .env / build files must never leak into a captured export ──────


async def test_capture_app_skips_secret_and_build_files(db_session) -> None:
    from src.models.orm.applications import Application

    db = db_session
    sol = await _make_solution(db)
    app = Application(
        id=uuid.uuid4(),
        name="Portal",
        slug=f"portal-{uuid.uuid4().hex[:8]}",
        repo_path="apps/portal",
        solution_id=sol.id,
        app_model="standalone_v2",
    )
    db.add(app)
    await db.flush()

    repo = _FakeRepo({
        "apps/portal/src/App.tsx": b"export default function App() {}",
        "apps/portal/.env": b"SECRET_KEY=hunter2",
        "apps/portal/.env.production": b"PROD_SECRET=nope",
        "apps/portal/node_modules/dep/index.js": b"module.exports = {}",
        "apps/portal/dist/bundle.js": b"compiled",
        "apps/portal/.DS_Store": b"\x00\x01",
    })

    await SolutionCaptureService(db, repo=repo).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[], apps=[app.id], forms=[],
            agents=[], claims=[], configs=[],
        ),
    )

    # Export writes app source files into the zip under apps/<slug>/<rel>.
    bundle = await SolutionCaptureService(db, repo=repo).bundle_for(sol)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        names = set(zf.namelist())
        apps_yaml = yaml.safe_load(zf.read(".bifrost/apps.yaml"))
    app_dir = apps_yaml["apps"][str(app.id)]["path"]
    assert f"{app_dir}/src/App.tsx" in names
    # Secrets + build junk are excluded — no entry under the app dir for them.
    for skipped in (".env", ".env.production", "node_modules/dep/index.js",
                    "dist/bundle.js", ".DS_Store"):
        assert f"{app_dir}/{skipped}" not in names, f"{skipped} leaked into export"


# ── Task 6b: app logos round-trip ───────────────────────────────────────────


async def test_capture_rejects_inline_v1_app(db_session) -> None:
    from src.models.orm.applications import Application

    db = db_session
    sol = await _make_solution(db)
    app = Application(
        id=uuid.uuid4(),
        name="Legacy",
        slug=f"legacy-{uuid.uuid4().hex[:8]}",
        repo_path="apps/legacy",
        solution_id=None,
        app_model="inline_v1",
    )
    db.add(app)
    await db.flush()

    with pytest.raises(SolutionCaptureConflict, match="standalone_v2"):
        await SolutionCaptureService(db).capture(
            sol,
            SolutionCaptureSelectors(
                workflows=[], tables=[], apps=[app.id], forms=[],
                agents=[], claims=[], configs=[],
            ),
        )
    # The reject fires BEFORE any solution_id stamp — app stays loose.
    captured = await db.get(Application, app.id)
    assert captured is not None and captured.solution_id is None


async def test_capture_app_carries_logo_for_redeploy(db_session) -> None:
    from src.models.orm.applications import Application

    db = db_session
    sol = await _make_solution(db)
    logo = b"\x89PNG\r\n\x1a\n logo bytes"
    app = Application(
        id=uuid.uuid4(),
        name="Logoed",
        slug=f"logoed-{uuid.uuid4().hex[:8]}",
        repo_path="apps/logoed",
        solution_id=sol.id,
        app_model="standalone_v2",
        logo_data=logo,
        logo_content_type="image/png",
    )
    db.add(app)
    await db.flush()

    repo = _FakeRepo({"apps/logoed/src/App.tsx": b"x"})
    await SolutionCaptureService(db, repo=repo).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[], apps=[app.id], forms=[],
            agents=[], claims=[], configs=[],
        ),
    )

    # Export writes the logo as a real file under the app dir, referenced by
    # the manifest body's ``logo:`` key (deploy reads it back). Without capture
    # carrying logo_b64/logo_content_type this round-trip would drop the icon.
    bundle = await SolutionCaptureService(db, repo=repo).bundle_for(sol)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        apps_yaml = yaml.safe_load(zf.read(".bifrost/apps.yaml"))
        entry = apps_yaml["apps"][str(app.id)]
        logo_rel = entry["logo"]
        logo_bytes = zf.read(f"{entry['path']}/{logo_rel}")
    assert logo_rel == "app-logo.png"
    assert logo_bytes == logo


# ── Task 6c: role bindings export role_names alongside UUIDs ─────────────────


async def test_capture_app_exports_role_names(db_session) -> None:
    from src.models.orm.app_roles import AppRole
    from src.models.orm.applications import Application
    from src.models.orm.users import Role

    db = db_session
    sol = await _make_solution(db)
    role = Role(id=uuid.uuid4(), name=f"editor-{uuid.uuid4().hex[:8]}", created_by="test")
    db.add(role)
    app = Application(
        id=uuid.uuid4(),
        name="Roled",
        slug=f"roled-{uuid.uuid4().hex[:8]}",
        repo_path="apps/roled",
        solution_id=sol.id,
        app_model="standalone_v2",
    )
    db.add(app)
    await db.flush()
    db.add(AppRole(app_id=app.id, role_id=role.id))
    await db.flush()

    repo = _FakeRepo({"apps/roled/src/App.tsx": b"x"})
    await SolutionCaptureService(db, repo=repo).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[], apps=[app.id], forms=[],
            agents=[], claims=[], configs=[],
        ),
    )

    bundle = await SolutionCaptureService(db, repo=repo).bundle_for(sol)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        apps_yaml = yaml.safe_load(zf.read(".bifrost/apps.yaml"))
    entry = apps_yaml["apps"][str(app.id)]
    assert entry["roles"] == [str(role.id)]
    assert entry["role_names"] == [role.name]


# ── Task 3: import-closure is opt-in, never a blind modules/ glob ───────────


async def _make_captured_workflow(db, sol, path: str):
    from src.models.orm.workflows import Workflow

    wf = Workflow(
        id=uuid.uuid4(),
        name=f"wf-{uuid.uuid4().hex[:8]}",
        function_name="main",
        path=path,
        type="workflow",
        is_active=True,
        solution_id=sol.id,
    )
    db.add(wf)
    await db.flush()
    return wf


async def test_capture_default_bundles_only_workflow_own_files(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    await _make_captured_workflow(db, sol, "workflows/main.py")

    repo = _FakeRepo({
        "workflows/main.py": b"from modules.a import thing\n",
        "modules/a.py": b"from modules.b import other\n",
        "modules/b.py": b"X = 1\n",
        "modules/c.py": b"UNRELATED = 1\n",
    })
    await SolutionCaptureService(db, repo=repo).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[], apps=[], forms=[],
            agents=[], claims=[], configs=[],
        ),
    )

    bundle = await SolutionCaptureService(db, repo=repo).bundle_for(sol)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        names = set(zf.namelist())
    # Default: only the workflow's own file — NO modules/ at all.
    assert "workflows/main.py" in names
    assert "modules/a.py" not in names
    assert "modules/b.py" not in names
    assert "modules/c.py" not in names


async def test_capture_skips_predeployed_workflow_source_not_in_repo(db_session) -> None:
    """Capturing into an install that ALREADY has a deployed workflow must not
    fail because that workflow's source lives under _solutions/{id}/, not _repo/.

    Regression: _python_files read every solution workflow's path from _repo/ and
    hard-failed (409 NoSuchKey) on a pre-deployed one (e.g. the scaffold sample),
    so any capture into a non-empty install broke. Now it skips the unreadable
    (already-deployed) source and bundles only the loose _repo/ ones being adopted.
    """
    db = db_session
    sol = await _make_solution(db)
    # Pre-deployed workflow: solution-owned, source NOT in _repo/.
    await _make_captured_workflow(db, sol, "functions/hello.py")
    # A loose _repo/ workflow we're adopting now.
    await _make_captured_workflow(db, sol, "workflows/real.py")

    repo = _FakeRepo({"workflows/real.py": b"from bifrost import workflow\n"})
    # Must NOT raise on functions/hello.py being absent from _repo/.
    await SolutionCaptureService(db, repo=repo).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[], apps=[], forms=[],
            agents=[], claims=[], configs=[],
        ),
    )
    bundle = await SolutionCaptureService(db, repo=repo).bundle_for(sol)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        names = set(zf.namelist())
    assert "workflows/real.py" in names           # the loose one bundled
    assert "functions/hello.py" not in names       # the pre-deployed one skipped


async def test_capture_include_imports_bundles_transitive_closure_only(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)
    await _make_captured_workflow(db, sol, "workflows/main.py")

    repo = _FakeRepo({
        "workflows/main.py": b"from modules.a import thing\n",
        "modules/a.py": b"from modules.b import other\n",
        "modules/b.py": b"X = 1\n",
        "modules/c.py": b"UNRELATED = 1\n",  # imported by nothing → never bundled
    })
    await SolutionCaptureService(db, repo=repo).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[], apps=[], forms=[],
            agents=[], claims=[], configs=[],
        ),
        include_imports=True,
    )

    bundle = await SolutionCaptureService(db, repo=repo).bundle_for(sol, include_imports=True)
    with zipfile.ZipFile(BytesIO(build_workspace_zip(bundle))) as zf:
        names = set(zf.namelist())
    assert "workflows/main.py" in names
    assert "modules/a.py" in names  # imported directly
    assert "modules/b.py" in names  # imported transitively
    assert "modules/c.py" not in names  # unrelated — never bundled


# ── Task 5: capture re-stamps global → org; refuses cross-org ───────────────


async def test_capture_global_entity_into_org_solution_restamps_org(db_session) -> None:
    from src.models.orm.organizations import Organization

    db = db_session
    org = Organization(id=uuid.uuid4(), name=f"OrgA-{uuid.uuid4().hex[:8]}", created_by="test")
    db.add(org)
    await db.flush()
    sol = await _make_solution(db, org_id=org.id)
    table = Table(id=uuid.uuid4(), name="orders", organization_id=None, solution_id=None)
    db.add(table)
    await db.flush()

    await SolutionCaptureService(db).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[table.id], apps=[], forms=[],
            agents=[], claims=[], configs=[],
        ),
    )
    await db.flush()

    captured = await db.get(Table, table.id)
    assert captured is not None
    assert captured.solution_id == sol.id
    # global → org-scoped solution re-stamps the entity's org down.
    assert captured.organization_id == org.id


async def test_capture_refuses_org_a_entity_into_org_b_solution(db_session) -> None:
    from src.models.orm.organizations import Organization

    db = db_session
    org_a = Organization(id=uuid.uuid4(), name=f"A-{uuid.uuid4().hex[:8]}", created_by="test")
    org_b = Organization(id=uuid.uuid4(), name=f"B-{uuid.uuid4().hex[:8]}", created_by="test")
    db.add_all([org_a, org_b])
    await db.flush()
    sol = await _make_solution(db, org_id=org_b.id)
    table = Table(id=uuid.uuid4(), name="orders", organization_id=org_a.id, solution_id=None)
    db.add(table)
    await db.flush()

    with pytest.raises(SolutionCaptureConflict, match="scoped to"):
        await SolutionCaptureService(db).capture(
            sol,
            SolutionCaptureSelectors(
                workflows=[], tables=[table.id], apps=[], forms=[],
                agents=[], claims=[], configs=[],
            ),
        )


async def test_capture_global_into_global_solution_keeps_org_null(db_session) -> None:
    db = db_session
    sol = await _make_solution(db)  # global solution (org None)
    table = Table(id=uuid.uuid4(), name="orders", organization_id=None, solution_id=None)
    db.add(table)
    await db.flush()

    await SolutionCaptureService(db).capture(
        sol,
        SolutionCaptureSelectors(
            workflows=[], tables=[table.id], apps=[], forms=[],
            agents=[], claims=[], configs=[],
        ),
    )
    await db.flush()

    captured = await db.get(Table, table.id)
    assert captured is not None
    assert captured.solution_id == sol.id
    assert captured.organization_id is None
