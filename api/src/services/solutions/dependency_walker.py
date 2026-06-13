"""Dependency walker for the Solution capture/export preview (§3.2 / §3.3).

Given a seed selection of loose entities, computes:

- **Forward closure** (``pulled_in``): the entities the selection drags in that
  AREN'T already selected — a captured workflow's ``modules/`` imports (when
  ``include_imports`` is on), the tables/configs it reads, the workflow a
  captured form launches, the workflows an app references, an agent's tools.
- **Reverse references** (``outside_references``): entities OUTSIDE the
  selection that reference something INSIDE it (e.g. "table ``orders`` is also
  read by workflow ``nightly-sync`` which is NOT being captured").

The forward closure here is informational for the PREVIEW only — it does not
mutate the capture selectors. The human deselects in the UI; the preview is the
guard. (Capture's own Python bundling closure is separate, in ``capture.py``
``_python_files`` gated by ``include_imports``.)

References are found by static scanners (``ref_scanner``): Python module imports
(AST) and string-literal entity refs (``tables.get``/``config.get``/
``useWorkflow``/``useTable``). Computed refs are invisible — hence
``scan_is_static`` and the deselectable human-checked preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solutions import (
    DependencyRef,
    OutsideReference,
    SolutionDependencyPreview,
)
from src.models.orm.agents import Agent, AgentTool
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.forms import Form
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.repo_storage import RepoStorage
from src.services.solutions.ref_scanner import (
    scan_config_refs,
    scan_imported_modules,
    scan_table_refs,
    scan_workflow_refs,
)


@dataclass
class _Seed:
    """Resolved seed selection as id/key sets."""

    workflows: set[UUID] = field(default_factory=set)
    tables: set[UUID] = field(default_factory=set)
    apps: set[UUID] = field(default_factory=set)
    forms: set[UUID] = field(default_factory=set)
    agents: set[UUID] = field(default_factory=set)
    claims: set[UUID] = field(default_factory=set)
    configs: set[str] = field(default_factory=set)


class SolutionDependencyWalker:
    """Compute the capture/export dependency preview for a seed selection."""

    def __init__(self, db: AsyncSession, repo: RepoStorage | None = None):
        self.db = db
        self.repo = repo or RepoStorage()

    async def preview(
        self,
        solution: Solution,
        seed: _Seed | None = None,
        *,
        workflows: list[UUID] | None = None,
        tables: list[UUID] | None = None,
        apps: list[UUID] | None = None,
        forms: list[UUID] | None = None,
        agents: list[UUID] | None = None,
        claims: list[UUID] | None = None,
        configs: list[str] | None = None,
        include_imports: bool = False,
    ) -> SolutionDependencyPreview:
        seed = seed or _Seed(
            workflows=set(workflows or []),
            tables=set(tables or []),
            apps=set(apps or []),
            forms=set(forms or []),
            agents=set(agents or []),
            claims=set(claims or []),
            configs=set(configs or []),
        )

        # Load the same-scope loose universe once (everything capture could see).
        org_id = solution.organization_id
        wf_by_id, wf_by_pathfn = await self._load_workflows(org_id)
        tbl_by_id, tbl_by_name = await self._load_tables(org_id)
        cfg_by_key = await self._load_configs(org_id)
        forms_all = await self._load_forms(org_id)
        apps_all = await self._load_apps(org_id)

        pulled: dict[tuple[str, str], DependencyRef] = {}

        def _add_pulled(kind: str, ref: str, name: str, in_sel: bool) -> None:
            pulled[(kind, ref)] = DependencyRef(
                kind=kind, ref=ref, name=name, in_selection=in_sel  # type: ignore[arg-type]
            )

        # ── Forward closure (transitive) ───────────────────────────────────
        # Build a workflow worklist: seeded workflows PLUS workflows reached
        # from selected apps (useWorkflow* refs), forms (launch/handler) and
        # agents (tool junction). Every workflow on the list is then scanned for
        # the tables/configs/modules IT reads — transitively (Codex: forward
        # closure must follow pulled-in workflows, not stop at them).
        wf_worklist: set[UUID] = set(seed.workflows)
        scanned_wf: set[UUID] = set()
        seen_modules: dict[str, str] = {}

        def _ref_workflow(wf: Workflow) -> None:
            _add_pulled("workflow", str(wf.id), wf.name, wf.id in seed.workflows)
            wf_worklist.add(wf.id)

        # Apps: useWorkflow*/useTable refs in TSX source.
        for app_id in seed.apps:
            app = apps_all.get(app_id)
            if app is None:
                continue
            for _rel, src in await self._read_app_sources(app):
                for ref in scan_workflow_refs(src):
                    wf = self._resolve_workflow_ref(ref, wf_by_id, wf_by_pathfn)
                    if wf is not None:
                        _ref_workflow(wf)
                for tname in scan_table_refs(src):
                    tbl = tbl_by_name.get(tname)
                    if tbl is not None:
                        _add_pulled(
                            "table", str(tbl.id), tbl.name, tbl.id in seed.tables
                        )

        # Forms: the workflow(s) they launch (workflow_id + launch_workflow_id).
        for form_id in seed.forms:
            form = forms_all.get(form_id)
            if form is None:
                continue
            for wf in self._form_workflows(form, wf_by_id, wf_by_pathfn):
                _ref_workflow(wf)

        # Agents: their tool workflows (AgentTool junction).
        if seed.agents:
            for tool_wf_id in await self._agent_tool_workflows(seed.agents):
                wf = wf_by_id.get(tool_wf_id)
                if wf is not None:
                    _ref_workflow(wf)

        # Drain the worklist: scan each workflow's source for tables/configs/
        # modules. New workflows can't appear here (workflows don't reference
        # other workflows in source), so this terminates after one sweep — but
        # the loop form keeps it correct if that ever changes.
        while wf_worklist - scanned_wf:
            wf_id = (wf_worklist - scanned_wf).pop()
            scanned_wf.add(wf_id)
            wf = wf_by_id.get(wf_id)
            if wf is None or not wf.path:
                continue
            src = await self._read(wf.path)
            if src is None:
                continue
            if include_imports:
                await self._collect_module_closure(src, seen_modules)
            for tname in scan_table_refs(src):
                tbl = tbl_by_name.get(tname)
                if tbl is not None:
                    _add_pulled("table", str(tbl.id), tbl.name, tbl.id in seed.tables)
            for key in scan_config_refs(src):
                if key in cfg_by_key:
                    _add_pulled("config", key, key, key in seed.configs)

        for rel in seen_modules:
            _add_pulled("module", rel, rel, False)

        # Drop closure entries that are already in the selection — the preview's
        # "pulled in" list is what the selection ADDS beyond what's chosen.
        pulled_in = [d for d in pulled.values() if not d.in_selection]

        # ── Reverse references (outside-of-selection warnings) ──────────────
        # Compare against the EFFECTIVE closure (seed + pulled-in workflows), so
        # a workflow the preview already surfaced isn't also flagged as an
        # outside referencer of itself (Codex). pulled-in tables/configs are NOT
        # added to the closure — an outside consumer of one is a real warning.
        closure_workflows = set(wf_worklist)
        outside = await self._reverse_refs(
            seed, closure_workflows, org_id, wf_by_id, wf_by_pathfn, tbl_by_id,
            tbl_by_name, forms_all, apps_all,
        )

        return SolutionDependencyPreview(
            pulled_in=sorted(pulled_in, key=lambda d: (d.kind, d.name)),
            outside_references=outside,
            scan_is_static=True,
        )

    # ── Reverse dependency scan ─────────────────────────────────────────────

    async def _reverse_refs(
        self, seed, closure_workflows, org_id, wf_by_id, wf_by_pathfn, tbl_by_id,
        tbl_by_name, forms_all, apps_all,
    ) -> list[OutsideReference]:
        """Find loose entities OUTSIDE the selection that reference inside it.

        Walk every loose same-scope workflow/app/form/agent NOT in the selection
        and check whether it references a selected table/config/workflow.
        ``closure_workflows`` is the effective workflow closure (seed + pulled
        in), so a workflow the preview already surfaces isn't flagged as its own
        outside referencer.
        """
        selected_table_names = {
            tbl_by_id[t].name for t in seed.tables if t in tbl_by_id
        }
        out: list[OutsideReference] = []

        # Workflows outside the closure that read a selected table/config.
        for wf_id, wf in wf_by_id.items():
            if wf_id in closure_workflows or not wf.path:
                continue
            src = await self._read(wf.path)
            if src is None:
                continue
            for tname in scan_table_refs(src):
                if tname in selected_table_names:
                    tbl = tbl_by_name[tname]
                    out.append(OutsideReference(
                        referencer_kind="workflow", referencer_ref=str(wf.id),
                        referencer_name=wf.name,
                        target_kind="table", target_ref=str(tbl.id),
                        target_name=tbl.name,
                    ))
            for key in scan_config_refs(src):
                if key in seed.configs:
                    out.append(OutsideReference(
                        referencer_kind="workflow", referencer_ref=str(wf.id),
                        referencer_name=wf.name,
                        target_kind="config", target_ref=key, target_name=key,
                    ))

        # Forms outside the selection that launch a selected workflow.
        for form_id, form in forms_all.items():
            if form_id in seed.forms:
                continue
            for wf in self._form_workflows(form, wf_by_id, wf_by_pathfn):
                if wf.id in closure_workflows:
                    out.append(OutsideReference(
                        referencer_kind="form", referencer_ref=str(form.id),
                        referencer_name=form.name,
                        target_kind="workflow", target_ref=str(wf.id),
                        target_name=wf.name,
                    ))

        # Apps outside the selection that reference a selected workflow/table.
        for app_id, app in apps_all.items():
            if app_id in seed.apps:
                continue
            for _rel, app_src in await self._read_app_sources(app):
                for ref in scan_workflow_refs(app_src):
                    wf = self._resolve_workflow_ref(ref, wf_by_id, wf_by_pathfn)
                    if wf is not None and wf.id in closure_workflows:
                        out.append(OutsideReference(
                            referencer_kind="app", referencer_ref=str(app.id),
                            referencer_name=app.name,
                            target_kind="workflow", target_ref=str(wf.id),
                            target_name=wf.name,
                        ))
                for tname in scan_table_refs(app_src):
                    if tname in selected_table_names:
                        tbl = tbl_by_name[tname]
                        out.append(OutsideReference(
                            referencer_kind="app", referencer_ref=str(app.id),
                            referencer_name=app.name,
                            target_kind="table", target_ref=str(tbl.id),
                            target_name=tbl.name,
                        ))

        # Agents outside the selection whose tool is a selected workflow.
        agent_tools = await self._all_agent_tool_workflows(
            org_id, exclude=seed.agents
        )
        for agent_id, agent_name, tool_wf_id in agent_tools:
            if tool_wf_id in closure_workflows:
                wf = wf_by_id.get(tool_wf_id)
                if wf is not None:
                    out.append(OutsideReference(
                        referencer_kind="agent", referencer_ref=str(agent_id),
                        referencer_name=agent_name,
                        target_kind="workflow", target_ref=str(wf.id),
                        target_name=wf.name,
                    ))
        return out

    # ── Source loading ──────────────────────────────────────────────────────

    async def _read(self, path: str) -> str | None:
        try:
            return (await self.repo.read(path)).decode("utf-8")
        except Exception:
            return None

    async def _read_app_sources(self, app: Application) -> list[tuple[str, str]]:
        prefix = app.repo_prefix
        out: list[tuple[str, str]] = []
        try:
            paths = await self.repo.list(prefix)
        except Exception:
            return out
        for path in sorted(paths):
            rel = path[len(prefix):]
            if not rel:
                continue
            src = await self._read(path)
            if src is not None:
                out.append((rel, src))
        return out

    async def _collect_module_closure(
        self, source: str, acc: dict[str, str]
    ) -> None:
        """Add the transitive ``modules/`` import closure of ``source`` to acc."""
        pending = [source]
        while pending:
            cur = pending.pop()
            for module in scan_imported_modules(cur):
                root = module.split(".")[0]
                if root != "modules":
                    continue
                base = module.replace(".", "/")
                for cand in (f"{base}.py", f"{base}/__init__.py"):
                    if cand in acc:
                        break
                    src = await self._read(cand)
                    if src is not None:
                        acc[cand] = src
                        pending.append(src)
                        break

    # ── DB loaders (same-scope loose universe) ──────────────────────────────

    def _scope(self, model, org_id):
        if org_id is None:
            return model.organization_id.is_(None)
        return model.organization_id == org_id

    async def _load_workflows(self, org_id):
        rows = (
            await self.db.execute(
                select(Workflow).where(
                    Workflow.solution_id.is_(None),
                    self._scope(Workflow, org_id),
                    Workflow.is_active.is_(True),
                )
            )
        ).scalars().all()
        by_id = {w.id: w for w in rows}
        by_pathfn = {f"{w.path}::{w.function_name}": w for w in rows if w.path}
        return by_id, by_pathfn

    async def _load_tables(self, org_id):
        rows = (
            await self.db.execute(
                select(Table).where(
                    Table.solution_id.is_(None), self._scope(Table, org_id)
                )
            )
        ).scalars().all()
        return {t.id: t for t in rows}, {t.name: t for t in rows}

    async def _load_configs(self, org_id) -> dict[str, Config]:
        # Match the capture-candidates filter: only plain config VALUES
        # (no integration-backed or schema-bound rows), so the preview never
        # offers an integration config as a capturable dependency.
        rows = (
            await self.db.execute(
                select(Config).where(
                    self._scope(Config, org_id),
                    Config.integration_id.is_(None),
                    Config.config_schema_id.is_(None),
                )
            )
        ).scalars().all()
        return {c.key: c for c in rows}

    async def _load_forms(self, org_id) -> dict[UUID, Form]:
        rows = (
            await self.db.execute(
                select(Form).where(
                    Form.solution_id.is_(None), self._scope(Form, org_id)
                )
            )
        ).scalars().all()
        return {f.id: f for f in rows}

    async def _load_apps(self, org_id) -> dict[UUID, Application]:
        rows = (
            await self.db.execute(
                select(Application).where(
                    Application.solution_id.is_(None),
                    self._scope(Application, org_id),
                )
            )
        ).scalars().all()
        return {a.id: a for a in rows}

    async def _agent_tool_workflows(self, agent_ids: set[UUID]) -> list[UUID]:
        rows = (
            await self.db.execute(
                select(AgentTool.workflow_id).where(
                    AgentTool.agent_id.in_(list(agent_ids))
                )
            )
        ).scalars().all()
        return list(rows)

    async def _all_agent_tool_workflows(
        self, org_id, *, exclude: set[UUID]
    ) -> list[tuple[UUID, str, UUID]]:
        """(agent_id, agent_name, tool workflow_id) for LOOSE same-scope agents
        not in exclude — scoped like the rest of the universe (solution_id IS
        NULL + org match) so a cross-org/solution-managed agent never warns."""
        rows = (
            await self.db.execute(
                select(Agent.id, Agent.name, AgentTool.workflow_id)
                .join(AgentTool, AgentTool.agent_id == Agent.id)
                .where(
                    Agent.solution_id.is_(None),
                    self._scope(Agent, org_id),
                )
            )
        ).all()
        return [(aid, name, wf) for aid, name, wf in rows if aid not in exclude]

    @staticmethod
    def _resolve_workflow_ref(ref: str, wf_by_id, wf_by_pathfn) -> Workflow | None:
        """Resolve a workflow ref string (``path::fn`` OR bare name) to a row."""
        if "::" in ref:
            return wf_by_pathfn.get(ref)
        # Bare name — match against the loose-universe name index.
        for wf in wf_by_id.values():
            if wf.name == ref:
                return wf
        return None

    @staticmethod
    def _form_workflows(form: Form, wf_by_id, wf_by_pathfn) -> list[Workflow]:
        """Workflows a form references: both ``workflow_id`` (handler) and
        ``launch_workflow_id``, plus the portable ``path::fn`` fallback.

        Either id field may hold a UUID OR a portable ``path::fn`` ref string
        (Form.workflow_id is not always a UUID), so resolve both ways.
        """
        out: list[Workflow] = []
        for wf_ref in (form.workflow_id, form.launch_workflow_id):
            if wf_ref is None:
                continue
            ref_str = str(wf_ref)
            wf = None
            try:
                wf = wf_by_id.get(UUID(ref_str))
            except (ValueError, TypeError):
                # Not a UUID — a portable path::fn ref stored in the id field.
                wf = wf_by_pathfn.get(ref_str)
            if wf is not None:
                out.append(wf)
        if not out and form.workflow_path and form.workflow_function_name:
            wf = wf_by_pathfn.get(
                f"{form.workflow_path}::{form.workflow_function_name}"
            )
            if wf is not None:
                out.append(wf)
        # De-dupe (workflow_id == launch_workflow_id is possible).
        seen: set[UUID] = set()
        uniq: list[Workflow] = []
        for wf in out:
            if wf.id not in seen:
                seen.add(wf.id)
                uniq.append(wf)
        return uniq
