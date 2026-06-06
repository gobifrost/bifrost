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
from uuid import UUID, uuid5

from sqlalchemy import delete, select, text
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



def solution_entity_id(install_id: UUID, manifest_id: UUID) -> UUID:
    """Per-install entity id: ``uuid5(install_id, original_manifest_id)``.

    The "fresh phone numbers per customer" primitive. A byte-identical bundle
    (same manifest UUIDs) deploys into two installs as two INDEPENDENT entity
    rows, because the namespace (the install id) differs (criterion 9). And a
    redeploy of the same install reproduces the SAME id, so an update never
    scrambles a customer's internal wiring (criterion 10).

    Install-time only: the source repo / manifest keeps the original author-time
    ids; only an install's DB rows carry the remapped id.
    """
    return uuid5(install_id, str(manifest_id))


# Cross-reference fields that may carry an IN-BUNDLE entity id (workflow/agent).
# When the referenced entity is itself in this bundle, its id is remapped, so the
# reference must follow. Refs that are portable ``path::fn``/name strings, or that
# point outside the bundle, are left untouched — they resolve by path/name at
# runtime within the install's solution scope (see WorkflowRepository.resolve).
_FORM_WORKFLOW_REF_FIELDS = ("workflow_id", "launch_workflow_id")
_AGENT_WORKFLOW_LIST_FIELDS = ("tool_ids",)
_AGENT_AGENT_LIST_FIELDS = ("delegated_agent_ids",)


def _remap_ref(value: Any, id_map: dict[UUID, UUID]) -> Any:
    """Translate a single scalar cross-ref through the remap map.

    Only a raw UUID that names an in-bundle entity is translated. A ``path::fn``
    or name string, or a UUID outside the bundle, passes through unchanged.
    """
    if not isinstance(value, str):
        return value
    try:
        as_uuid = UUID(value)
    except ValueError:
        return value  # portable path::fn / name ref — resolved by scope at runtime
    mapped = id_map.get(as_uuid)
    return str(mapped) if mapped is not None else value


class SolutionDeployConflict(Exception):
    """A bundle references an entity id owned by _repo/ or another install."""


class SolutionFinalizeIncomplete(Exception):
    """Deploy committed but a post-commit S3 finalize step failed even after
    retries (a real storage outage). The deploy is full-replace + idempotent, so
    re-running it heals the state."""


# Post-commit finalize retry policy. Steps are idempotent full-replace writes, so
# a transient blip is absorbed by retrying; only a sustained outage escalates.
_FINALIZE_RETRIES = 3
_FINALIZE_BACKOFF_S = 0.5


async def _retry_idempotent(
    what: str, sid: object, op: Callable[[], Awaitable[None]]
) -> None:
    """Run an idempotent finalize step, retrying transient failures with backoff.

    Raises :class:`SolutionFinalizeIncomplete` only if every attempt fails — a
    genuine storage outage, which a later deploy/sync still heals (the writes are
    full-replace). Logs each retry so the blip is observable.
    """
    import asyncio

    last: Exception | None = None
    for attempt in range(1, _FINALIZE_RETRIES + 1):
        try:
            await op()
            return
        except Exception as exc:  # noqa: BLE001 - storage is the only failure here
            last = exc
            if attempt < _FINALIZE_RETRIES:
                logger.warning(
                    "Solution %s finalize step '%s' failed (attempt %d/%d): %s — retrying",
                    sid, what, attempt, _FINALIZE_RETRIES, exc,
                )
                await asyncio.sleep(_FINALIZE_BACKOFF_S * attempt)
    logger.error(
        "Solution %s finalize step '%s' failed after %d attempts: %s. The deploy "
        "is committed; re-run it (or wait for the next sync) to heal — every step "
        "is full-replace and safe to repeat.",
        sid, what, _FINALIZE_RETRIES, last,
    )
    raise SolutionFinalizeIncomplete(str(sid)) from last


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
        """Full-replace this install from ``bundle`` — DB phase + app COMPILE.

        Everything that can fail on bad input runs BEFORE the caller's commit, so
        a failure rolls the deploy back with ZERO durable side effects:
          - DB upserts + scoped reconcile (ownership/FK/unique/content), and
          - **app dist compilation** (npm install + vite build) — the
            failure-prone step — done here, IN MEMORY, no S3 write yet.
        Only the cheap, durable-after-commit work is deferred onto
        ``DeployResult.finalize_s3``: write Python source, UPLOAD the
        already-compiled dists, sweep stale dist artifacts. The caller commits
        first, then awaits ``finalize_s3``. So neither a failed commit (Codex
        P1-c) NOR a failed build (Codex R4) leaves DB ahead of S3 — a build error
        raises here, before commit; finalize is just retryable PUTs.
        """
        solution = bundle.solution
        sid = solution.id

        # ── Per-install identity remap (criteria 9/10) ───────────────────────
        # Rewrite every entity id to uuid5(install, manifest_id) and translate
        # in-bundle cross-refs through the same map, BEFORE any upsert/reconcile.
        # Both phases then operate uniformly on remapped ids — the manifest ids
        # never touch the DB, so a byte-identical bundle installs independently
        # into N scopes (and a redeploy is stable).
        self._remap_bundle_ids(bundle)

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

        # ── COMPILE app dists to memory NOW (pre-commit) — a vite/npm failure
        #    raises here and rolls back the whole deploy, no S3 touched. ───────
        compiled = await self._compile_app_dists(builds)

        # ── S3 phase, DEFERRED until after the caller's commit (cheap PUTs) ───
        # Every step is FULL-REPLACE (idempotent), so a transient storage blip is
        # absorbed by RETRYING the step rather than failing an already-committed
        # deploy (Codex R5: "there is no queued retry"). Steps run execution-first
        # (Python source before app dist) so even a mid-finalize hiccup leaves the
        # install runnable. Only an outage that survives all retries raises
        # SolutionFinalizeIncomplete — and even then a later deploy/sync heals it.
        async def _finalize_s3() -> None:
            await _retry_idempotent(
                "write python source", sid,
                lambda: self._write_python(sid, bundle.python_files),
            )
            await _retry_idempotent(
                "upload app dists", sid,
                lambda: self._upload_compiled_dists(compiled),
            )
            await _retry_idempotent(
                "sweep stale dist", sid,
                lambda: self._delete_stale_app_dist(stale_app_dist),
            )

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

    # ── Per-install identity remap ───────────────────────────────────────────
    def _remap_bundle_ids(self, bundle: "SolutionBundle") -> None:
        """Rewrite every bundle entity id to ``uuid5(install, manifest_id)`` and
        translate in-bundle cross-references through the same map.

        Works on DEEP COPIES of each entity dict and REPLACES the bundle's lists
        — the caller's dicts are never mutated. This keeps the transform pure and
        idempotent: the same bundle structure can be deployed to two installs (or
        the same install twice) without a prior pass corrupting the input by
        double-remapping (an already-remapped id has no entry in this install's
        map, so it would otherwise be re-remapped into a third, wrong id).

        Two-pass so a cross-ref can point at any entity regardless of order:
          1. Build ``id_map`` (manifest id → remapped id) across ALL entity
             types, stamping each copy's own ``id``.
          2. Rewrite cross-ref fields (form→workflow, agent→workflow/agent)
             through ``id_map``. Portable ``path::fn``/name refs and refs that
             point outside the bundle are left untouched — they resolve by path
             within the install's solution scope at runtime.

        Apps reference workflows/tables only by string (``useWorkflow("p::f")`` /
        ``useTable("name")``) in their SOURCE, never by id in metadata, so app
        entries need no cross-ref rewrite (only their own id is remapped).
        """
        import copy

        sid = bundle.solution.id
        id_map: dict[UUID, UUID] = {}

        bundle.workflows = [copy.deepcopy(e) for e in bundle.workflows]
        bundle.tables = [copy.deepcopy(e) for e in bundle.tables]
        bundle.apps = [copy.deepcopy(e) for e in bundle.apps]
        bundle.forms = [copy.deepcopy(e) for e in bundle.forms]
        bundle.agents = [copy.deepcopy(e) for e in bundle.agents]

        # Pass 1: remap each entity's own id.
        for entry in (
            bundle.workflows
            + bundle.tables
            + bundle.apps
            + bundle.forms
            + bundle.agents
        ):
            original = UUID(str(entry["id"]))
            remapped = solution_entity_id(sid, original)
            id_map[original] = remapped
            entry["id"] = str(remapped)

        # Pass 2: translate cross-refs that name an in-bundle entity.
        for mform in bundle.forms:
            for fld in _FORM_WORKFLOW_REF_FIELDS:
                if mform.get(fld) is not None:
                    mform[fld] = _remap_ref(mform[fld], id_map)
            self._remap_form_field_providers(mform, id_map)
        for magent in bundle.agents:
            for fld in _AGENT_WORKFLOW_LIST_FIELDS + _AGENT_AGENT_LIST_FIELDS:
                vals = magent.get(fld)
                if isinstance(vals, list):
                    magent[fld] = [_remap_ref(v, id_map) for v in vals]

    @staticmethod
    def _remap_form_field_providers(
        mform: dict[str, Any], id_map: dict[UUID, UUID]
    ) -> None:
        """Translate the nested ``form_schema.fields[].data_provider_id`` ref
        (a workflow id) through the remap map."""
        schema = mform.get("form_schema")
        if not isinstance(schema, dict):
            return
        for field_def in schema.get("fields") or []:
            if isinstance(field_def, dict) and field_def.get("data_provider_id") is not None:
                field_def["data_provider_id"] = _remap_ref(
                    field_def["data_provider_id"], id_map
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
            # Serialize the check-then-insert across CONCURRENT deploys (Codex
            # R5): two deploys with the same slug could both pass the SELECT
            # below before either commits (the DB's per-solution_id unique index
            # doesn't stop a cross-scope route collision). A transaction-scoped
            # advisory lock keyed on the slug makes this atomic — a racing deploy
            # blocks here until the first commits, then sees the row. Released at
            # commit/rollback. (hashtext gives a stable bigint key per slug.)
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('bifrost:appslug:' || :s))"),
                {"s": slug},
            )
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

    async def _compile_app_dists(
        self, builds: list[dict[str, Any]]
    ) -> list[tuple[UUID, dict[str, bytes]]]:
        """PRE-COMMIT: compile each app's dist to memory (npm install + vite
        build, or a shipped prebuilt dist). This is the failure-prone step — a
        build error raises HERE, before the deploy commits, so the whole deploy
        rolls back with no S3 side effects (Codex R4 atomicity). No S3 writes.

        Returns ``[(app_id, dist_bytes), ...]`` for the post-commit upload.
        """
        import asyncio
        import base64 as _b64

        from src.services.solutions.app_build import SolutionAppBuilder

        if not builds:
            return []
        builder = SolutionAppBuilder()
        out: list[tuple[UUID, dict[str, bytes]]] = []
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
            for rel, b64 in (b.get("bin") or {}).items():
                src_bytes[rel] = _b64.b64decode(b64)
            # compile_dist is subprocess-bound (npm/vite) → run off the loop.
            dist = await asyncio.to_thread(
                builder.compile_dist,
                b["app_id"],
                src_bytes,
                b["dependencies"],
                prebuilt_bytes,
            )
            out.append((b["app_id"], dist))
        return out

    async def _upload_compiled_dists(
        self, compiled: list[tuple[UUID, dict[str, bytes]]]
    ) -> None:
        """POST-COMMIT: upload the already-compiled dists (cheap, retryable
        PUTs). The compile already succeeded pre-commit, so this can't fail the
        deploy on bad input — only a transient S3 outage, which is re-runnable."""
        from src.services.solutions.app_build import SolutionAppBuilder

        if not compiled:
            return
        builder = SolutionAppBuilder()
        for app_id, dist in compiled:
            await builder.upload_dist(app_id, dist)

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
