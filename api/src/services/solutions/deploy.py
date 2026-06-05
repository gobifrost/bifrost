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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent, AgentRole
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.forms import Form, FormRole
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.services.solutions.storage import SolutionStorage
from src.services.sync_ops import Upsert

logger = logging.getLogger(__name__)



class SolutionDeployConflict(Exception):
    """A bundle references an entity id owned by _repo/ or another install."""


async def _noop_finalize() -> None:  # default so an unbound result is still awaitable
    return None


@dataclass
class DeployResult:
    """Counts from one full-replace deploy.

    ``finalize_s3`` is the deferred S3 phase (Python source write + app builds +
    stale-dist sweep). ``deploy()`` returns BEFORE running it; the caller awaits
    it only after a durable ``commit()`` so a commit failure changes no running
    code (Codex P1-c). ``compare=False`` keeps the closure out of equality.
    """

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
    finalize_s3: Callable[[], Awaitable[None]] = field(
        default=_noop_finalize, compare=False, repr=False
    )


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
        """Full-replace this install from ``bundle`` — **DB phase only**.

        All DB upserts + the scoped reconcile run here, so the common failure
        modes (ownership conflict, FK/unique constraint, content validation)
        roll back the DB with ZERO S3 side effects. The S3 phase (Python source
        write + app builds + stale-dist sweep) is NOT run here — it is bound onto
        the returned ``DeployResult.finalize_s3`` and the caller awaits it only
        **after a durable ``commit()``**. So a commit that fails after this
        returns changes no running code (Codex P1-c) — the prior order ran S3
        before the caller's commit, leaving a window where a failed deploy had
        already replaced the install's executing Python source.
        """
        solution = bundle.solution
        sid = solution.id

        # ── DB-only phase (validates + reconciles; rolls back cleanly) ───────
        await self._upsert_workflows(solution, bundle.workflows)
        await self._upsert_tables(solution, bundle.tables)
        builds = await self._upsert_apps(solution, bundle.apps)
        await self._upsert_forms(solution, bundle.forms)
        await self._upsert_agents(solution, bundle.agents)
        (
            wf_deleted, tbl_deleted, app_deleted, form_deleted, agent_deleted,
            stale_app_dist,
        ) = await self._reconcile_deletions(sid, bundle)

        # ── S3 phase, DEFERRED until after the caller's commit ───────────────
        async def _finalize_s3() -> None:
            await self._write_python(sid, bundle.python_files)
            await self._run_app_builds(builds)
            await self._delete_stale_app_dist(stale_app_dist)

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
            finalize_s3=_finalize_s3,
        )

    # ── Role bindings (full-replace into the entity↔role junction) ───────────
    async def _resolve_roles(self, entry: dict[str, Any]) -> list[UUID]:
        """Resolve a manifest entry's role refs to role UUIDs in the target env.

        ``role_names`` (portable, cross-env) wins over ``roles`` (raw UUIDs) when
        present — deploy is cross-environment, so names are the durable ref. Both
        are optional; absent → no roles. Unknown names fail loud (the role must
        exist in the install's env first).
        """
        from src.services.manifest_import import _resolve_role_names

        role_names = entry.get("role_names")
        if role_names:
            return [UUID(r) for r in await _resolve_role_names(self.db, list(role_names))]
        return [UUID(str(r)) for r in (entry.get("roles") or [])]

    async def _sync_entity_roles(
        self,
        junction: type,
        fk_col: str,
        entity_id: UUID,
        role_ids: list[UUID],
        assigned_by: str = "solution",
    ) -> None:
        """Full-replace the entity's rows in a ``*_roles`` junction.

        Deploy is the only writer of solution-managed role bindings (the REST
        role-mutation endpoints are read-only for managed entities), so this must
        delete-all + insert to reflect adds AND removes across redeploys
        (Codex P1-d). Mirrors the canonical FormRole/AppRole write pattern.
        """
        await self.db.execute(
            delete(junction).where(getattr(junction, fk_col) == entity_id)
        )
        now = datetime.now(timezone.utc)
        for role_id in dict.fromkeys(role_ids):  # dedupe, preserve order
            self.db.add(
                junction(**{fk_col: entity_id, "role_id": role_id},
                         assigned_by=assigned_by, assigned_at=now)
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
                # Full-replace deploy-owned metadata so a redeploy that changes
                # (or clears) these is reflected, not left stale (criteria 10/14).
                "description": mwf.get("description"),
                "endpoint_enabled": mwf.get("endpoint_enabled", False),
                "public_endpoint": mwf.get("public_endpoint", False),
                "timeout_seconds": mwf.get("timeout_seconds", 1800),
                "category": mwf.get("category", "General"),
                "tags": mwf.get("tags") or [],
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
            await self._sync_entity_roles(
                WorkflowRole, "workflow_id", wf_id, await self._resolve_roles(mwf)
            )

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
    ) -> list[dict[str, Any]]:
        """DB-only: upsert app metadata, return the deferred build specs.

        The Application row is stamped with ``solution_id`` + inherited scope +
        ``app_model`` and marked published (deploy IS the publish). The actual
        ``dist/`` build/upload to ``_apps/{id}/`` is DEFERRED — returned here as
        build specs and run by :meth:`_run_app_builds` only after all DB work
        succeeds (Codex P1-e: no S3 mutation before the DB is known-good). App
        ``src/`` is never persisted under ``_solutions/`` (§3.6).

        Ownership guard mirrors workflows/tables: a bundle UUID must not collide
        with a row owned by ``_repo/`` (NULL) or another install.
        """
        sid = solution.id
        builds: list[dict[str, Any]] = []
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
            # Route-collision guard (Codex P2-f + R4): the per-install unique
            # index keeps (solution_id, slug) unique, but the /apps/{slug}
            # resolver (scalar_one_or_none) raises MultipleResultsFound if two
            # apps VISIBLE TO ONE ORG share a slug. Visibility is asymmetric: a
            # global app (org NULL) is seen by EVERY org, an org app only by its
            # own org. So the collision set for the deploying app's scope is:
            #   - global install (org NULL): ANY other app with this slug — a
            #     global one, or an org one (that org would then see two).
            #   - org install (org X): an app with this slug whose org is X OR
            #     NULL (org X sees its own apps AND globals).
            # A purely cross-org pair (two different non-global orgs) is fine —
            # no single org sees both, and the resolver disambiguates (criterion 9).
            org_id = solution.organization_id
            collision_pred = [
                Application.slug == slug,
                Application.id != app_id,
            ]
            if org_id is not None:
                collision_pred.append(
                    (Application.organization_id == org_id)
                    | Application.organization_id.is_(None)
                )
            # global install: no org filter → collide with any same-slug app.
            collision = (
                await self.db.execute(
                    select(Application.id, Application.solution_id).where(*collision_pred)
                )
            ).first()
            if collision is not None:
                other = collision[1]
                raise SolutionDeployConflict(
                    f"app slug '{slug}' is already in use by a visible app "
                    f"({'a _repo/ app' if other is None else f'solution {other}'}); "
                    f"two apps cannot share /apps/{slug} for any org — rename one."
                )
            app_model = mapp.get("app_model", "inline_v1")
            now = datetime.now(timezone.utc)
            values: dict[str, Any] = {
                "name": mapp.get("name") or slug,
                "slug": slug,
                "repo_path": mapp.get("repo_path") or f"apps/{slug}",
                "description": mapp.get("description"),
                "dependencies": mapp.get("dependencies") or None,
                "app_model": app_model,
                "organization_id": solution.organization_id,
                "solution_id": sid,
                "published_snapshot": {"deployed_by": "solution", "app_model": app_model},
                "published_at": now,
            }
            if mapp.get("access_level") is not None:
                values["access_level"] = mapp["access_level"]

            await Upsert(
                model=Application, id=app_id, values=values, match_on="id"
            ).execute(self.db)
            await self._sync_entity_roles(
                AppRole, "app_id", app_id, await self._resolve_roles(mapp)
            )

            # Only standalone_v2 apps are built to dist/. inline_v1 render via
            # the esbuild path; a Vite build on them would fail.
            if app_model == "standalone_v2":
                builds.append({
                    "app_id": app_id,
                    "src": mapp.get("src_files") or {},
                    # Non-text assets (png/fonts/public/) carried as base64 by the
                    # CLI/git collectors — decoded into the build input (P2-j/R4).
                    "bin": mapp.get("bin_files") or {},
                    "dist": mapp.get("dist_files"),
                    "dependencies": mapp.get("dependencies") or {},
                })
        return builds

    async def _run_app_builds(self, builds: list[dict[str, Any]]) -> None:
        """S3 phase: build/upload each deferred app dist. Runs only after all DB
        work succeeded (Codex P1-e)."""
        from src.services.solutions.app_build import SolutionAppBuilder

        if not builds:
            return
        builder = SolutionAppBuilder()
        for b in builds:
            prebuilt = b["dist"]
            prebuilt_bytes = (
                {k: v.encode("utf-8") if isinstance(v, str) else v for k, v in prebuilt.items()}
                if prebuilt
                else None
            )
            src_bytes = {
                k: v.encode("utf-8") if isinstance(v, str) else v
                for k, v in b["src"].items()
            }
            # Merge decoded binary assets (base64 → bytes) into the build input.
            import base64 as _b64

            for rel, b64 in (b.get("bin") or {}).items():
                src_bytes[rel] = _b64.b64decode(b64)
            await builder.build(
                app_id=b["app_id"],
                src_files=src_bytes,
                dependencies=b["dependencies"],
                prebuilt_dist=prebuilt_bytes,
            )

    async def _delete_stale_app_dist(self, app_ids: set[UUID]) -> None:
        """S3 phase: delete the dist artifacts of apps reconciled away."""
        from src.services.solutions.app_build import SolutionAppBuilder

        if not app_ids:
            return
        builder = SolutionAppBuilder()
        for app_id in app_ids:
            await builder.delete_dist(app_id)

    async def _upsert_forms(
        self, solution: Solution, forms: list[dict[str, Any]]
    ) -> None:
        """Deploy forms by delegating ALL content to the canonical FormIndexer.

        The indexer (the same code git-sync/file-sync use) parses the form YAML
        and full-replaces the form row + ALL its FormField rows — so every
        portable form field flows through one place and a new field can't create
        a deploy gap. Deploy then stamps the install's scope (``solution_id`` +
        ``organization_id``) on the row, which the indexer intentionally leaves
        untouched. Ownership guard mirrors workflows/apps/tables.
        """
        from sqlalchemy import update

        from bifrost.manifest import ManifestForm
        from src.services.file_storage.indexers.form import FormIndexer
        from src.services.manifest_import import _form_content_from_manifest

        sid = solution.id
        indexer = FormIndexer(self.db)
        for mform in forms:
            form_id = UUID(mform["id"])
            await self._guard_owner(Form, form_id, sid)
            # Build the canonical YAML the indexer expects from the manifest body.
            mf = ManifestForm.model_validate({**mform, "id": str(form_id)})
            content = _form_content_from_manifest(mf)
            await indexer.index_form(f"forms/{form_id}.form.yaml", content)
            # Stamp the install scope (the indexer preserves org/access on purpose).
            await self.db.execute(
                update(Form).where(Form.id == form_id).values(
                    organization_id=solution.organization_id,
                    solution_id=sid,
                )
            )
            # Sync role bindings — the indexer does NOT handle role rows, and the
            # REST role endpoints are read-only for managed entities, so deploy is
            # the only writer of these (Codex P1-d).
            await self._sync_entity_roles(
                FormRole, "form_id", form_id, await self._resolve_roles(mform)
            )

    async def _upsert_agents(
        self, solution: Solution, agents: list[dict[str, Any]]
    ) -> None:
        """Deploy agents by delegating content to the canonical AgentIndexer.

        Mirrors :meth:`_upsert_forms`: the indexer full-replaces the agent row +
        its tool/delegation/MCP junctions + knowledge/system-tools/limits
        (gap-resistant — same code as git-sync); deploy stamps the install scope.
        Role bindings are NOT handled by the indexer — deploy syncs them itself
        below (Codex P1-d), since the REST role endpoints are read-only here.
        """
        from sqlalchemy import update

        from bifrost.manifest import ManifestAgent
        from src.services.file_storage.indexers.agent import AgentIndexer
        from src.services.manifest_import import _agent_content_from_manifest

        sid = solution.id
        indexer = AgentIndexer(self.db)
        for magent in agents:
            agent_id = UUID(magent["id"])
            await self._guard_owner(Agent, agent_id, sid)
            ma = ManifestAgent.model_validate({**magent, "id": str(agent_id)})
            content = _agent_content_from_manifest(ma)
            await indexer.index_agent(f"agents/{agent_id}.agent.yaml", content)
            await self.db.execute(
                update(Agent).where(Agent.id == agent_id).values(
                    organization_id=solution.organization_id,
                    solution_id=sid,
                )
            )
            # Sync role bindings (indexer doesn't touch role rows) — Codex P1-d.
            await self._sync_entity_roles(
                AgentRole, "agent_id", agent_id, await self._resolve_roles(magent)
            )

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
    ) -> tuple[int, int, int, int, int, set[UUID]]:
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
        app_deleted, stale_app_dist = await self._reconcile_apps(
            sid, {UUID(a["id"]) for a in bundle.apps}
        )
        form_deleted = await self._reconcile_one(
            Form, sid, {UUID(f["id"]) for f in bundle.forms}
        )
        agent_deleted = await self._reconcile_one(
            Agent, sid, {UUID(a["id"]) for a in bundle.agents}
        )
        return (
            wf_deleted, tbl_deleted, app_deleted, form_deleted, agent_deleted,
            stale_app_dist,
        )

    async def _reconcile_apps(
        self, sid: UUID, present_ids: set[UUID]
    ) -> tuple[int, set[UUID]]:
        """Delete this install's stale Application ROWS (DB-only). Returns the
        count + the stale ids whose ``_apps/{id}/dist/`` artifacts must be swept
        in the S3 phase (deferred via :meth:`_delete_stale_app_dist` so a DB
        rollback leaves no dangling S3 deletions — Codex P1-e)."""
        stmt = select(Application.id).where(Application.solution_id == sid)
        existing = set((await self.db.execute(stmt)).scalars().all())
        stale = existing - present_ids
        if not stale:
            return 0, set()
        await self.db.execute(
            delete(Application).where(
                Application.solution_id == sid,
                Application.id.in_(stale),
            )
        )
        logger.info("Solution %s: deleted %d stale application row(s)", sid, len(stale))
        return len(stale), stale

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
