"""
Solution deploy — full-replace reconcile scoped strictly to ``solution_id``.

Deploy is the single writer for a disconnected install (success-criteria §3.6):
it upserts everything in the bundle and deletes entities previously under THIS
``solution_id`` that are absent from the new bundle. The deletion sweep is
gated on ``WHERE solution_id == sid AND id NOT IN bundle_ids`` — so it can never
touch ``_repo/`` rows (``solution_id IS NULL``) or any other install (a
different ``solution_id``). Scope correctness is by construction, not by a
path-existence heuristic (the destructive global sweep that the viability study
flagged is deliberately NOT reused here).

Python (workflows, modules) installs **as source** to ``_solutions/{id}/`` via
SolutionStorage and is executed as source by the virtual importer (§3.6). Every
deployed entity inherits the install's scope — its ``organization_id`` is the
install's ``organization_id`` (org-scoped or NULL/global), with no per-entity
scope binding (criterion 8).

Sub-plan 1 wires workflows end-to-end (the load-bearing path proven by the
execution criteria). Apps/forms/agents/tables hang off the same reconcile shape
and are added in their sub-plans.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.forms import Form, FormField
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.storage import SolutionStorage
from src.services.sync_ops import Upsert

logger = logging.getLogger(__name__)


def _form_fields_from_manifest(mform: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a form's fields from a manifest body.

    The canonical manifest serializes fields under ``form_schema.fields`` (see
    ``manifest_generator.serialize_form``); a top-level ``fields`` key is also
    accepted for the ad-hoc/test shape. Reading the wrong key silently deploys a
    form with NO fields.
    """
    schema = mform.get("form_schema")
    if isinstance(schema, dict) and schema.get("fields"):
        return list(schema["fields"])
    return list(mform.get("fields") or [])


class SolutionDeployConflict(Exception):
    """A bundle references an entity id owned by _repo/ or another install."""


@dataclass
class DeployResult:
    """Counts from one full-replace deploy."""

    workflows_upserted: int = 0
    workflows_deleted: int = 0
    tables_upserted: int = 0
    tables_deleted: int = 0
    apps_upserted: int = 0
    apps_deleted: int = 0
    forms_upserted: int = 0
    forms_deleted: int = 0
    agents_upserted: int = 0
    agents_deleted: int = 0


@dataclass
class SolutionBundle:
    """The deployable contents of one Solution install.

    ``python_files`` maps relative paths (e.g. ``workflows/w1.py``,
    ``modules/x.py``) to source text, installed verbatim under the install's
    ``_solutions/{id}/`` prefix. ``workflows`` (and, in later sub-plans,
    apps/forms/agents/tables) are manifest-shaped entity dicts to upsert.
    """

    solution: Solution
    python_files: dict[str, str] = field(default_factory=dict)
    workflows: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    apps: list[dict[str, Any]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)


class SolutionDeployer:
    """Applies a SolutionBundle to storage + DB as a scoped full replace."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def deploy(self, bundle: SolutionBundle) -> DeployResult:
        """Full-replace this install from ``bundle``.

        1. Write Python source to SolutionStorage (_solutions/{id}/).
        2. Upsert bundle entities, stamping solution_id + inherited scope.
        3. Delete entities under THIS solution_id that are absent from the bundle.
        """
        solution = bundle.solution
        sid = solution.id

        await self._write_python(sid, bundle.python_files)
        await self._upsert_workflows(solution, bundle.workflows)
        await self._upsert_tables(solution, bundle.tables)
        await self._upsert_apps(solution, bundle.apps)
        await self._upsert_forms(solution, bundle.forms)
        await self._upsert_agents(solution, bundle.agents)
        (
            wf_deleted, tbl_deleted, app_deleted, form_deleted, agent_deleted
        ) = await self._reconcile_deletions(sid, bundle)
        return DeployResult(
            workflows_upserted=len(bundle.workflows),
            workflows_deleted=wf_deleted,
            tables_upserted=len(bundle.tables),
            tables_deleted=tbl_deleted,
            apps_upserted=len(bundle.apps),
            apps_deleted=app_deleted,
            forms_upserted=len(bundle.forms),
            forms_deleted=form_deleted,
            agents_upserted=len(bundle.agents),
            agents_deleted=agent_deleted,
        )

    # ── 1. Python source → SolutionStorage (full replace + cache sync) ───────
    async def _write_python(self, sid: UUID, python_files: dict[str, str]) -> None:
        """Full-replace this install's Python source and keep the module cache
        consistent.

        get_module_sync reads Redis (keyed by the _solutions/{id}/ storage path)
        BEFORE S3, so a plain S3 write would leave stale bytes cached for the
        24h TTL and removed files would still resolve. So: write-through each
        bundle file to Redis with fresh content, and delete (S3 + Redis) any
        prior solution file absent from the new bundle (Codex P1).
        """
        from src.core.module_cache import invalidate_module, set_module

        storage = SolutionStorage(sid)

        # Prior state: every file currently under this install's prefix.
        prior = set(await storage.list(""))
        new_rel = set(python_files.keys())

        for rel_path, content in python_files.items():
            content_hash = await storage.write(rel_path, content.encode("utf-8"))
            storage_key = storage._key(rel_path)  # _solutions/{id}/<rel>
            # Write-through so the next execution reads the new bytes, not the
            # 24h-TTL cache. Only .py files are import-cached.
            if rel_path.endswith(".py"):
                await set_module(storage_key, content, content_hash)

        # Remove files dropped from the bundle (full replace of source).
        for rel_path in prior - new_rel:
            await storage.delete(rel_path)
            if rel_path.endswith(".py"):
                await invalidate_module(storage._key(rel_path))

    # ── 2. Entity upserts (stamp solution_id + inherited scope) ──────────────
    async def _upsert_workflows(
        self, solution: Solution, workflows: list[dict[str, Any]]
    ) -> None:
        sid = solution.id
        for mwf in workflows:
            wf_id = UUID(mwf["id"])

            # Guard: a bundle UUID must not collide with a row owned elsewhere
            # (a _repo/ row, or another install). Updating it would re-stamp
            # solution_id and silently hijack an unrelated workflow — the very
            # thing the scoped full-replace guarantee forbids. Fetch (exists,
            # owner) as a row so a real NULL owner is distinct from "absent".
            row = (
                await self.db.execute(
                    select(Workflow.id, Workflow.solution_id).where(Workflow.id == wf_id)
                )
            ).first()
            if row is not None:
                owner = row[1]
                if owner != sid:
                    raise SolutionDeployConflict(
                        f"workflow {wf_id} is already owned by "
                        f"{'_repo/' if owner is None else f'solution {owner}'}; "
                        f"a bundle may not reuse another owner's entity id"
                    )

            values = {
                "name": mwf["name"],
                "function_name": mwf["function_name"],
                "path": mwf["path"],
                "type": mwf.get("type", "workflow"),
                "is_active": True,
                # Full-replace: description is always set from the bundle so a
                # removed description clears the DB value rather than going stale.
                "description": mwf.get("description"),
                # Scope is inherited from the install — no per-entity binding.
                "organization_id": solution.organization_id,
                "solution_id": sid,
            }
            if mwf.get("access_level") is not None:
                values["access_level"] = mwf["access_level"]
            # Safe now: the id is either absent or already this install's.
            await Upsert(
                model=Workflow, id=wf_id, values=values, match_on="id"
            ).execute(self.db)

    async def _upsert_tables(
        self, solution: Solution, tables: list[dict[str, Any]]
    ) -> None:
        """Upsert table SCHEMA + POLICIES only. Row data (Document records) is
        runtime state and is never written or wiped by deploy (criterion 11).

        ``policies`` in the manifest is a flat list stored under the Table
        ``access`` JSONB column. A redeploy with a changed schema updates the
        ``schema`` JSONB in place; the table row (and its Documents via the
        FK) survives untouched.
        """
        from shared.policies.probe import make_seed_admin_bypass
        from src.core.pubsub import publish_policy_changed
        from src.models.contracts.policies import TablePolicies

        sid = solution.id
        for mtbl in tables:
            tbl_id = UUID(mtbl["id"])

            # Fetch existing (owner + current access) in one shot — used for the
            # ownership guard AND to decide whether to emit policy_changed.
            row = (
                await self.db.execute(
                    select(Table.solution_id, Table.access).where(Table.id == tbl_id)
                )
            ).first()
            existed = row is not None
            if existed and row[0] != sid:
                owner = row[0]
                raise SolutionDeployConflict(
                    f"table {tbl_id} is already owned by "
                    f"{'_repo/' if owner is None else f'solution {owner}'}; "
                    f"a bundle may not reuse another owner's entity id"
                )
            prev_access = row[1] if existed else None

            # Resolve + VALIDATE policies before persisting (mirrors REST/manifest
            # paths) so a malformed AST is rejected at deploy, not at read time.
            policies = mtbl.get("policies")
            if policies is not None:
                access = {"policies": policies}
                TablePolicies.model_validate(access)  # raises on a bad AST
            else:
                # None / absent -> seed admin_bypass, matching API-created tables
                # and manifest import; without it RLS denies all table I/O.
                access = make_seed_admin_bypass()

            # Full-replace: description and schema are always set from the bundle
            # (solution-owned metadata), so removing them in the bundle clears
            # the DB value rather than leaving it stale.
            values: dict[str, Any] = {
                "name": mtbl["name"],
                "description": mtbl.get("description"),
                "schema": mtbl.get("schema"),
                "access": access,
                "organization_id": solution.organization_id,
                "solution_id": sid,
            }

            await Upsert(
                model=Table, id=tbl_id, values=values, match_on="id"
            ).execute(self.db)

            # Invalidate active websocket subscribers' policy cache when the
            # access policy actually changed (the REST PATCH path does this too;
            # without it subscribers keep the old authorization until reconnect).
            if existed and prev_access != access:
                await publish_policy_changed(str(tbl_id))

    async def _upsert_apps(
        self, solution: Solution, apps: list[dict[str, Any]]
    ) -> None:
        """Upsert app metadata + build/ship its ``dist/`` to ``_apps/{id}/``.

        The Application row is stamped with ``solution_id`` + inherited scope +
        ``app_model``. App ``src/`` is NEVER persisted under ``_solutions/`` — it
        is transient build input; only the built ``dist/`` lands in ``_apps/``
        (§3.6). For ``standalone_v2`` apps the server-side vite build runs unless
        the bundle ships a prebuilt ``dist_files`` (disconnected fast-path).

        Ownership guard mirrors workflows/tables: a bundle UUID must not collide
        with a row owned by ``_repo/`` (NULL) or another install — upserting it
        would silently re-stamp ``solution_id`` and hijack an unrelated app.
        """
        from src.services.solutions.app_build import SolutionAppBuilder

        sid = solution.id
        builder = SolutionAppBuilder()
        for mapp in apps:
            app_id = UUID(mapp["id"])

            row = (
                await self.db.execute(
                    select(Application.id, Application.solution_id).where(
                        Application.id == app_id
                    )
                )
            ).first()
            if row is not None and row[1] != sid:
                owner = row[1]
                raise SolutionDeployConflict(
                    f"app {app_id} is already owned by "
                    f"{'_repo/' if owner is None else f'solution {owner}'}; "
                    f"a bundle may not reuse another owner's entity id"
                )

            slug = mapp["slug"]
            values: dict[str, Any] = {
                "name": mapp.get("name") or slug,
                "slug": slug,
                "repo_path": mapp.get("repo_path") or f"apps/{slug}",
                "description": mapp.get("description"),
                "dependencies": mapp.get("dependencies") or None,
                "app_model": mapp.get("app_model", "inline_v1"),
                "organization_id": solution.organization_id,
                "solution_id": sid,
            }
            if mapp.get("access_level") is not None:
                values["access_level"] = mapp["access_level"]

            await Upsert(
                model=Application, id=app_id, values=values, match_on="id"
            ).execute(self.db)

            # Build (or accept prebuilt) dist → _apps/{id}/dist/. dist_files is a
            # str→str map in the bundle (JSON-friendly); the builder takes bytes.
            prebuilt = mapp.get("dist_files")
            prebuilt_bytes = (
                {k: v.encode("utf-8") if isinstance(v, str) else v for k, v in prebuilt.items()}
                if prebuilt
                else None
            )
            src = mapp.get("src_files") or {}
            src_bytes = {
                k: v.encode("utf-8") if isinstance(v, str) else v for k, v in src.items()
            }
            await builder.build(
                app_id=app_id,
                src_files=src_bytes,
                dependencies=mapp.get("dependencies") or {},
                prebuilt_dist=prebuilt_bytes,
            )

    async def _upsert_forms(
        self, solution: Solution, forms: list[dict[str, Any]]
    ) -> None:
        """Upsert form rows stamped with solution_id + inherited scope.

        Forms become solution-managed (read-only on the platform). The portable
        ``content`` (title/fields/prompts) lives in the ``content`` text column;
        env-specific fields (access_level/roles/created_by) stay instance-owned.
        Ownership guard mirrors workflows/apps/tables.
        """
        sid = solution.id
        for mform in forms:
            form_id = UUID(mform["id"])
            await self._guard_owner(Form, form_id, sid)
            values: dict[str, Any] = {
                "name": mform["name"],
                "description": mform.get("description"),
                "workflow_id": mform.get("workflow_id"),
                "launch_workflow_id": mform.get("launch_workflow_id"),
                "is_active": True,
                "organization_id": solution.organization_id,
                "solution_id": sid,
                # created_by is NOT NULL; deploy is the writer.
                "created_by": mform.get("created_by") or "solution-deploy",
            }
            await Upsert(model=Form, id=form_id, values=values, match_on="id").execute(self.db)
            await self._upsert_form_fields(form_id, _form_fields_from_manifest(mform))

    async def _upsert_form_fields(
        self, form_id: UUID, fields: list[dict[str, Any]]
    ) -> None:
        """Full-replace a form's fields from the bundle (portable content).

        Maps the full FormField column set the manifest carries (see
        ``manifest_generator._form_field_to_schema_dict``) so deployed forms are
        complete, not stripped.
        """
        from uuid import UUID as _UUID

        await self.db.execute(delete(FormField).where(FormField.form_id == form_id))
        for pos, fld in enumerate(fields):
            dp = fld.get("data_provider_id")
            self.db.add(FormField(
                form_id=form_id,
                name=fld["name"],
                label=fld.get("label"),
                type=fld.get("type", "text"),
                required=bool(fld.get("required", False)),
                position=fld.get("position", pos),
                placeholder=fld.get("placeholder"),
                help_text=fld.get("help_text"),
                default_value=fld.get("default_value"),
                options=fld.get("options"),
                data_provider_id=_UUID(dp) if dp else None,
                data_provider_inputs=fld.get("data_provider_inputs"),
                visibility_expression=fld.get("visibility_expression"),
                validation=fld.get("validation"),
                allowed_types=fld.get("allowed_types"),
                multiple=fld.get("multiple"),
                max_size_mb=fld.get("max_size_mb"),
                content=fld.get("content"),
                allow_as_query_param=fld.get("allow_as_query_param"),
            ))

    async def _upsert_agents(
        self, solution: Solution, agents: list[dict[str, Any]]
    ) -> None:
        """Upsert agent rows stamped with solution_id + inherited scope."""
        sid = solution.id
        for magent in agents:
            agent_id = UUID(magent["id"])
            await self._guard_owner(Agent, agent_id, sid)
            values: dict[str, Any] = {
                "name": magent["name"],
                "system_prompt": magent.get("system_prompt") or "",
                "is_active": True,
                "organization_id": solution.organization_id,
                "solution_id": sid,
                # created_by is NOT NULL; deploy is the writer.
                "created_by": magent.get("created_by") or "solution-deploy",
            }
            if magent.get("description") is not None:
                values["description"] = magent["description"]
            if magent.get("channels") is not None:
                values["channels"] = magent["channels"]
            if magent.get("llm_model") is not None:
                values["llm_model"] = magent["llm_model"]
            await Upsert(model=Agent, id=agent_id, values=values, match_on="id").execute(self.db)

    async def _guard_owner(self, model: type, entity_id: UUID, sid: UUID) -> None:
        """Raise SolutionDeployConflict if ``entity_id`` exists and is owned by
        _repo/ (NULL) or a different install — a bundle may not hijack it."""
        row = (
            await self.db.execute(
                select(model.solution_id).where(model.id == entity_id)  # type: ignore[attr-defined]
            )
        ).first()
        if row is not None and row[0] != sid:
            owner = row[0]
            raise SolutionDeployConflict(
                f"{model.__tablename__} {entity_id} is already owned by "  # type: ignore[attr-defined]
                f"{'_repo/' if owner is None else f'solution {owner}'}; "
                f"a bundle may not reuse another owner's entity id"
            )

    # ── 3. Scoped full-replace deletion ─────────────────────────────────────
    async def _reconcile_deletions(
        self, sid: UUID, bundle: SolutionBundle
    ) -> tuple[int, int, int, int, int]:
        """Delete this install's entities that are absent from the bundle.

        Strictly scoped: ``solution_id == sid AND id NOT IN bundle_ids``. Never
        touches _repo/ (solution_id IS NULL) or another install. For tables,
        only the Table row is swept — Document (row) data is never deleted here;
        a removed table's rows go via the Table FK cascade, which only fires when
        the table itself is genuinely absent from the bundle. For apps, the
        ``_apps/{id}/dist/`` artifact is deleted alongside the row. Returns
        (workflows, tables, apps, forms, agents) deleted counts.
        """
        wf_deleted = await self._reconcile_one(
            Workflow, sid, {UUID(w["id"]) for w in bundle.workflows}
        )
        tbl_deleted = await self._reconcile_one(
            Table, sid, {UUID(t["id"]) for t in bundle.tables}
        )
        app_deleted = await self._reconcile_apps(
            sid, {UUID(a["id"]) for a in bundle.apps}
        )
        form_deleted = await self._reconcile_one(
            Form, sid, {UUID(f["id"]) for f in bundle.forms}
        )
        agent_deleted = await self._reconcile_one(
            Agent, sid, {UUID(a["id"]) for a in bundle.agents}
        )
        return wf_deleted, tbl_deleted, app_deleted, form_deleted, agent_deleted

    async def _reconcile_apps(self, sid: UUID, present_ids: set[UUID]) -> int:
        """Sweep this install's Application rows absent from the bundle, and
        delete their ``_apps/{id}/dist/`` artifacts (the built output is owned by
        the install, unlike Table row data which is runtime state)."""
        from src.services.solutions.app_build import SolutionAppBuilder

        stmt = select(Application.id).where(Application.solution_id == sid)
        existing = set((await self.db.execute(stmt)).scalars().all())
        stale = existing - present_ids
        if not stale:
            return 0
        builder = SolutionAppBuilder()
        for app_id in stale:
            await builder.delete_dist(app_id)
        await self.db.execute(
            delete(Application).where(
                Application.solution_id == sid,
                Application.id.in_(stale),
            )
        )
        logger.info("Solution %s: deleted %d stale application row(s)", sid, len(stale))
        return len(stale)

    async def _reconcile_one(
        self, model: type, sid: UUID, present_ids: set[UUID]
    ) -> int:
        # Find this install's rows that are NOT in the bundle.
        stmt = select(model.id).where(model.solution_id == sid)  # type: ignore[attr-defined]
        existing = set((await self.db.execute(stmt)).scalars().all())
        stale = existing - present_ids
        if not stale:
            return 0
        await self.db.execute(
            delete(model).where(
                model.solution_id == sid,  # type: ignore[attr-defined]
                model.id.in_(stale),  # type: ignore[attr-defined]
            )
        )
        logger.info(
            "Solution %s: deleted %d stale %s row(s)",
            sid,
            len(stale),
            model.__tablename__,  # type: ignore[attr-defined]
        )
        return len(stale)
