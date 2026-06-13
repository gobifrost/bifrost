from __future__ import annotations

import uuid

import pytest

from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.dependency_walker import SolutionDependencyWalker

pytestmark = pytest.mark.e2e


class _FakeRepo:
    def __init__(self, files: dict[str, bytes]):
        self._files = files

    async def list(self, prefix: str = "") -> list[str]:
        return [p for p in self._files if p.startswith(prefix)]

    async def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc


async def _solution(db) -> Solution:
    sol = Solution(id=uuid.uuid4(), slug=f"s-{uuid.uuid4().hex[:8]}", name="S")
    db.add(sol)
    await db.flush()
    return sol


async def _wf(db, *, path, fn="main", name) -> Workflow:
    wf = Workflow(
        id=uuid.uuid4(), name=name, function_name=fn, path=path,
        type="workflow", is_active=True, organization_id=None, solution_id=None,
    )
    db.add(wf)
    await db.flush()
    return wf


async def _table(db, name) -> Table:
    t = Table(id=uuid.uuid4(), name=name, organization_id=None, solution_id=None)
    db.add(t)
    await db.flush()
    return t


async def test_workflow_pulls_in_referenced_table(db_session) -> None:
    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/sync.py", name="sync")
    orders = await _table(db, "orders")

    repo = _FakeRepo({"workflows/sync.py": b'await tables.get("orders")'})
    preview = await SolutionDependencyWalker(db, repo=repo).preview(
        sol, workflows=[wf.id]
    )
    pulled = {(d.kind, d.ref) for d in preview.pulled_in}
    assert ("table", str(orders.id)) in pulled


async def test_selected_table_is_not_listed_as_pulled_in(db_session) -> None:
    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/sync.py", name="sync")
    orders = await _table(db, "orders")

    repo = _FakeRepo({"workflows/sync.py": b'await tables.get("orders")'})
    preview = await SolutionDependencyWalker(db, repo=repo).preview(
        sol, workflows=[wf.id], tables=[orders.id]
    )
    # Already selected → not reported as "pulled in".
    assert all(d.ref != str(orders.id) for d in preview.pulled_in)


async def test_include_imports_pulls_module_closure(db_session) -> None:
    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/sync.py", name="sync")
    repo = _FakeRepo({
        "workflows/sync.py": b"from modules.a import thing",
        "modules/a.py": b"from modules.b import other",
        "modules/b.py": b"X = 1",
        "modules/c.py": b"UNRELATED = 1",
    })

    off = await SolutionDependencyWalker(db, repo=repo).preview(
        sol, workflows=[wf.id], include_imports=False
    )
    assert not any(d.kind == "module" for d in off.pulled_in)

    on = await SolutionDependencyWalker(db, repo=repo).preview(
        sol, workflows=[wf.id], include_imports=True
    )
    mods = {d.ref for d in on.pulled_in if d.kind == "module"}
    assert mods == {"modules/a.py", "modules/b.py"}  # c is never imported


async def test_reverse_ref_warns_outside_consumer(db_session) -> None:
    db = db_session
    sol = await _solution(db)
    orders = await _table(db, "orders")
    # Workflow OUTSIDE the selection that also reads the selected table.
    await _wf(db, path="workflows/nightly.py", name="nightly-sync")

    repo = _FakeRepo({"workflows/nightly.py": b'await tables.get("orders")'})
    preview = await SolutionDependencyWalker(db, repo=repo).preview(
        sol, tables=[orders.id]
    )
    warns = [
        w for w in preview.outside_references
        if w.target_ref == str(orders.id)
        and w.referencer_name == "nightly-sync"
    ]
    assert len(warns) == 1
    assert warns[0].referencer_kind == "workflow"


async def test_form_pulls_in_launched_workflow(db_session) -> None:
    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/intake.py", name="intake")
    form = Form(
        id=uuid.uuid4(), name="Intake", organization_id=None, solution_id=None,
        created_by="test",
        workflow_path="workflows/intake.py", workflow_function_name="main",
    )
    db.add(form)
    await db.flush()

    preview = await SolutionDependencyWalker(db, repo=_FakeRepo({})).preview(
        sol, forms=[form.id]
    )
    assert ("workflow", str(wf.id)) in {(d.kind, d.ref) for d in preview.pulled_in}


async def test_app_pulls_in_referenced_workflow(db_session) -> None:
    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/api.py", name="api")
    app = Application(
        id=uuid.uuid4(), name="Portal", slug=f"p-{uuid.uuid4().hex[:8]}",
        repo_path="apps/portal", organization_id=None, solution_id=None,
    )
    db.add(app)
    await db.flush()

    repo = _FakeRepo({
        "apps/portal/src/App.tsx": b'useWorkflow("workflows/api.py::main")',
    })
    preview = await SolutionDependencyWalker(db, repo=repo).preview(
        sol, apps=[app.id]
    )
    assert ("workflow", str(wf.id)) in {(d.kind, d.ref) for d in preview.pulled_in}


async def test_forward_closure_is_transitive_app_to_workflow_to_table(db_session) -> None:
    # App -> workflow (via useWorkflowQuery) -> table (via tables.get). The
    # table must surface even though the workflow was itself only pulled in.
    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/api.py", name="get_orders")
    orders = await _table(db, "orders")
    app = Application(
        id=uuid.uuid4(), name="Portal", slug=f"p-{uuid.uuid4().hex[:8]}",
        repo_path="apps/portal", organization_id=None, solution_id=None,
    )
    db.add(app)
    await db.flush()

    repo = _FakeRepo({
        "apps/portal/src/App.tsx": b'useWorkflowQuery("get_orders")',
        "workflows/api.py": b'await tables.get("orders")',
    })
    preview = await SolutionDependencyWalker(db, repo=repo).preview(
        sol, apps=[app.id]
    )
    refs = {(d.kind, d.ref) for d in preview.pulled_in}
    assert ("workflow", str(wf.id)) in refs
    assert ("table", str(orders.id)) in refs  # transitive — the bug Codex caught


async def test_form_pulls_in_launch_workflow(db_session) -> None:
    db = db_session
    sol = await _solution(db)
    launch = await _wf(db, path="workflows/launch.py", name="launch")
    form = Form(
        id=uuid.uuid4(), name="Intake", organization_id=None, solution_id=None,
        created_by="test", launch_workflow_id=str(launch.id),
    )
    db.add(form)
    await db.flush()

    preview = await SolutionDependencyWalker(db, repo=_FakeRepo({})).preview(
        sol, forms=[form.id]
    )
    assert ("workflow", str(launch.id)) in {(d.kind, d.ref) for d in preview.pulled_in}


async def test_reverse_ref_warns_outside_agent_tool(db_session) -> None:
    from src.models.orm.agents import Agent, AgentTool

    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/tool.py", name="tool-wf")
    agent = Agent(
        id=uuid.uuid4(), name="Helper", organization_id=None, solution_id=None,
        system_prompt="help", created_by="test",
    )
    db.add(agent)
    await db.flush()
    db.add(AgentTool(agent_id=agent.id, workflow_id=wf.id))
    await db.flush()

    # Select the workflow; the outside agent using it as a tool must be warned.
    preview = await SolutionDependencyWalker(db, repo=_FakeRepo({})).preview(
        sol, workflows=[wf.id]
    )
    warns = [
        w for w in preview.outside_references
        if w.referencer_kind == "agent" and w.target_ref == str(wf.id)
    ]
    assert len(warns) == 1
    assert warns[0].referencer_name == "Helper"


async def test_reverse_ref_ignores_solution_managed_agent(db_session) -> None:
    # An agent already owned by a DIFFERENT solution is outside the loose
    # same-scope universe and must NOT be reported as an outside referencer.
    from src.models.orm.agents import Agent, AgentTool

    db = db_session
    sol = await _solution(db)
    other_sol = await _solution(db)
    wf = await _wf(db, path="workflows/tool.py", name="tool-wf")
    managed = Agent(
        id=uuid.uuid4(), name="Managed", organization_id=None,
        solution_id=other_sol.id, system_prompt="x", created_by="test",
    )
    db.add(managed)
    await db.flush()
    db.add(AgentTool(agent_id=managed.id, workflow_id=wf.id))
    await db.flush()

    preview = await SolutionDependencyWalker(db, repo=_FakeRepo({})).preview(
        sol, workflows=[wf.id]
    )
    assert not any(
        w.referencer_kind == "agent" for w in preview.outside_references
    )


async def test_form_resolves_workflow_ref_stored_as_pathfn(db_session) -> None:
    # Form.workflow_id can hold a portable path::fn ref (not just a UUID).
    db = db_session
    sol = await _solution(db)
    wf = await _wf(db, path="workflows/handler.py", fn="run", name="handler")
    form = Form(
        id=uuid.uuid4(), name="Intake", organization_id=None, solution_id=None,
        created_by="test", workflow_id="workflows/handler.py::run",
    )
    db.add(form)
    await db.flush()

    preview = await SolutionDependencyWalker(db, repo=_FakeRepo({})).preview(
        sol, forms=[form.id]
    )
    assert ("workflow", str(wf.id)) in {(d.kind, d.ref) for d in preview.pulled_in}
