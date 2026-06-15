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
from enum import Enum
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent, AgentRole
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.forms import Form, FormRole
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.services.solutions.storage import SolutionStorage
from src.services.sync_ops import Upsert

logger = logging.getLogger(__name__)

# App-logo limits — mirror the upload endpoint (applications.py).
_LOGO_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"}
_LOGO_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


def _decode_logo(
    label: str, b64: str | None, content_type: str | None
) -> tuple[bytes | None, str | None]:
    """Validate + decode a manifest-declared logo into (data, content_type).

    Returns (None, None) when no logo is declared — deploy then CLEARS any
    prior logo (deploy is the publish, so a logo dropped from the manifest is
    dropped from the row). Applies the same content-type allow-list, size cap,
    and SVG sanitization as the interactive upload endpoint, so a bundle can't
    smuggle an oversized or scriptable SVG. Used for both app logos and the
    solution-level icon.
    """
    if not b64:
        return None, None
    if content_type not in _LOGO_ALLOWED_CONTENT_TYPES:
        raise SolutionDeployConflict(
            f"{label}: logo content type {content_type!r} not allowed "
            f"(png, jpeg, or svg)"
        )
    import base64 as _b64

    data = _b64.b64decode(b64)
    if len(data) > _LOGO_MAX_SIZE:
        raise SolutionDeployConflict(
            f"{label}: logo exceeds {_LOGO_MAX_SIZE // 1024 // 1024} MB"
        )
    if content_type == "image/svg+xml":
        from shared.svg_sanitizer import SvgSanitizationError, sanitize_svg

        try:
            data = sanitize_svg(data)
        except SvgSanitizationError as exc:
            raise SolutionDeployConflict(f"{label}: invalid SVG logo: {exc}")
    return data, content_type


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


class SolutionDowngradeBlocked(Exception):
    """The bundle's version is older (PEP 440) than the installed version.

    Refused by default (Task 20) — re-run with ``force`` to downgrade. Only
    raised when BOTH versions parse as PEP 440; unordered versions never block.
    """


def _is_downgrade(new: str | None, current: str | None) -> bool:
    """True only when both versions parse as PEP 440 and new < current.
    Unparseable or absent versions are unordered — never block on them."""
    if not new or not current:
        return False
    try:
        from packaging.version import InvalidVersion, Version

        return Version(new) < Version(current)
    except InvalidVersion:
        return False


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
    claims_upserted: int = 0
    claims_deleted: int = 0
    integrations_shell_created: int = 0
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
    claims: list[dict[str, Any]] = field(default_factory=list)
    config_schemas: list[dict[str, Any]] = field(default_factory=list)
    # Each: {integration_name, template, position}. Secret-scrubbed skeletons
    # (no client_id/secret). Declared from integrations.get("X") refs.
    connection_schemas: list[dict[str, Any]] = field(default_factory=list)
    # Event/schedule triggers. Each is a ManifestEventSource-shaped dict (source
    # + nested schedule/webhook config + subscriptions). Webhook instance state
    # (external_id/state/expires_at) is scrubbed; the instance re-establishes it.
    events: list[dict[str, Any]] = field(default_factory=list)
    # The bundle's declared version (bifrost.solution.yaml ``version:``).
    # Recorded on the install by deploy; gates downgrades (Task 20).
    version: str | None = None
    # Solution-level icon (bifrost.solution.yaml ``logo:``), carried base64.
    # Deploy-owned: present => stamped on the install, absent => cleared.
    logo_b64: str | None = None
    logo_content_type: str | None = None
    # Long-form README markdown sourced from the repo-root README.md. Deploy-owned
    # and full-replaces exactly like the logo: present => set, absent => cleared.
    readme: str | None = None
    # Sensitive export tier — only populated when include_values=True (full mode).
    # Travels as password-encrypted .bifrost/secrets.enc; never in plaintext export.
    config_values: dict[str, str] = field(default_factory=dict)
    table_data: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class SolutionDeployer:
    """Applies a SolutionBundle to storage + DB as a scoped full replace."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def deploy(self, bundle: SolutionBundle, force: bool = False) -> DeployResult:
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

        # ── Module-closure backstop — before ANY writes ──────────────────────
        # Primary gate is in zip_install (returns a clean 422). This backstop
        # covers the OTHER deploy callers (the direct /deploy endpoint and
        # git-sync auto-pull): a bundle whose ``modules.X`` import isn't shipped
        # would otherwise install and fail at runtime with ModuleNotFoundError.
        # Raised as SolutionDeployConflict so it rolls back with no side effects.
        from src.services.solutions.dependency_walker import check_install_needs

        needs = check_install_needs(bundle.python_files)
        if needs:
            items = ", ".join(
                f"{n.ref} ({n.detail})" if n.detail else n.ref for n in needs
            )
            raise SolutionDeployConflict(
                f"Solution has unmet dependencies: {items}"
            )

        # ── Downgrade gate (Task 20) — before ANY writes ─────────────────────
        # An older bundle (both versions PEP 440-ordered) is refused unless
        # forced. Unparseable/absent versions are unordered and never block.
        if not force and _is_downgrade(bundle.version, solution.version):
            raise SolutionDowngradeBlocked(
                f"bundle version {bundle.version} is older than installed "
                f"{solution.version}; re-run with force to downgrade"
            )

        # ── Per-install identity remap (criteria 9/10) ───────────────────────
        # Rewrite every entity id to uuid5(install, manifest_id) and translate
        # in-bundle cross-refs through the same map, BEFORE any upsert/reconcile.
        # Returns a NEW bundle (the caller's `bundle` is never mutated), so
        # deploying the same SolutionBundle object twice cannot double-remap (the
        # 2nd pass would otherwise treat the 1st pass's uuid5 ids as fresh
        # manifest ids and remap them again — Codex #8 P2). Both phases below
        # operate on the remapped bundle; the manifest ids never touch the DB, so
        # a byte-identical bundle installs independently into N scopes (and a
        # redeploy is stable).
        rb = await self._remapped_bundle(bundle)

        # ── DB-only phase (validates + reconciles; rolls back cleanly) ───────
        await self._upsert_workflows(solution, rb.workflows)
        await self._upsert_claims(solution, rb.claims)
        adopted_table_ids = await self._upsert_tables(solution, rb.tables)
        builds = await self._upsert_apps(solution, rb.apps)
        await self._upsert_forms(solution, rb.forms)
        await self._upsert_agents(solution, rb.agents)
        await self._upsert_config_schemas(solution, rb.config_schemas)
        # Un-orphan this install's config VALUES that match the re-declared keys
        # (Task 14c) so the operator doesn't re-enter them on reinstall.
        reattached_configs = await self._reattach_orphan_configs(
            solution, {c["key"] for c in rb.config_schemas}
        )
        # Pre-create an empty integration shell for each declared connection that
        # doesn't yet exist globally (never clobbering a configured one). Uses the
        # ORIGINAL bundle: connection declarations key on integration NAME, which
        # carries no per-install id and is therefore not part of the remap.
        shells_created = await self._upsert_integration_shells(
            bundle.connection_schemas
        )
        # Persist the connection DECLARATIONS on the install (keyed by the
        # install's id + integration name) so /setup surfaces a connection item
        # for an installed solution — not only a captured one. The capture writer
        # (capture.py::_connection_entries) does this for the source install;
        # this mirrors it for every deploy/zip-install/CLI-deploy target.
        await self._upsert_connection_declarations(
            solution, bundle.connection_schemas
        )
        # Captured pre-commit: the finalize closure runs after the caller's
        # commit, when lazy-loading off the ORM row is no longer safe.
        install_org_id_str = (
            str(solution.organization_id) if solution.organization_id is not None else None
        )
        (
            wf_deleted, tbl_deleted, app_deleted, form_deleted, agent_deleted,
            claim_deleted,
            stale_app_dist,
        ) = await self._reconcile_deletions(sid, rb, adopted_table_ids)

        # ── Version bookkeeping (Task 20) — part of the DB phase, so it commits
        # (or rolls back) atomically with the reconcile. A version-carrying
        # bundle that CHANGES the version records the replaced one (None on
        # first set); a versionless bundle leaves both fields untouched.
        if bundle.version is not None and bundle.version != solution.version:
            solution.upgraded_from_version = solution.version
            solution.version = bundle.version

        # ── Solution-level icon — deploy-owned exactly like the app logo:
        # declared in the bundle => set, absent => cleared.
        sol_logo, sol_logo_ct = _decode_logo(
            f"solution '{solution.slug}'", bundle.logo_b64, bundle.logo_content_type
        )
        solution.logo_data = sol_logo
        solution.logo_content_type = sol_logo_ct

        # ── README — repo-sourced markdown, deploy-owned full-replace (absent
        # => cleared), same lifecycle as the logo above.
        self._apply_readme(solution, bundle)

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
            if reattached_configs:
                # The reattach is a Core UPDATE that never bumped the config
                # cache (mirror of the uninstall router's invalidation).
                # Without this, merged_for_sdk keeps serving the orphan-era
                # cache and the reattached value stays invisible until TTL.
                # Post-commit on purpose: invalidating pre-commit would let a
                # concurrent reader re-cache the OLD state before we commit.
                from src.core.cache import invalidate_all_config

                await invalidate_all_config(install_org_id_str)
            await _retry_idempotent(
                "write python source", sid,
                lambda: self._write_python(sid, rb.python_files),
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
            workflows_upserted=len(rb.workflows),
            workflows_deleted=wf_deleted,
            tables_upserted=len(rb.tables),
            tables_deleted=tbl_deleted,
            apps_upserted=len(rb.apps),
            apps_deleted=app_deleted,
            forms_upserted=len(rb.forms),
            forms_deleted=form_deleted,
            agents_upserted=len(rb.agents),
            agents_deleted=agent_deleted,
            claims_upserted=len(rb.claims),
            claims_deleted=claim_deleted,
            integrations_shell_created=shells_created,
            finalize_s3=_finalize_s3,
        )

    @staticmethod
    def _apply_readme(solution: Solution, bundle: Any) -> None:
        """README is repo-sourced and full-replaces (absent => cleared) — same
        lifecycle as the logo."""
        solution.readme = getattr(bundle, "readme", None)

    # ── Per-install identity remap ───────────────────────────────────────────
    async def _remapped_bundle(self, bundle: "SolutionBundle") -> "SolutionBundle":
        """Return a NEW bundle whose every entity id is ``uuid5(install,
        manifest_id)`` and whose in-bundle cross-refs are translated through the
        same map. The caller's ``bundle`` is NEVER mutated.

        Returning a fresh bundle (rather than mutating in place) makes deploy
        idempotent for the caller's object: deploying the SAME SolutionBundle
        instance twice in one process must not double-remap (the 2nd pass would
        otherwise treat the 1st pass's uuid5 ids as fresh manifest ids and remap
        them AGAIN, scrambling the wiring and making reconcile delete the rows it
        just created — Codex #8 P2). Entity dicts are deep-copied so the input's
        nested structures are untouched too.

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

        workflows = [copy.deepcopy(e) for e in bundle.workflows]
        tables = [copy.deepcopy(e) for e in bundle.tables]
        apps = [copy.deepcopy(e) for e in bundle.apps]
        forms = [copy.deepcopy(e) for e in bundle.forms]
        agents = [copy.deepcopy(e) for e in bundle.agents]
        claims = [copy.deepcopy(e) for e in bundle.claims]
        config_schemas = [copy.deepcopy(e) for e in bundle.config_schemas]

        typed_entries: list[tuple[type, dict[str, Any]]] = [
            *[(Workflow, e) for e in workflows],
            *[(Table, e) for e in tables],
            *[(Application, e) for e in apps],
            *[(Form, e) for e in forms],
            *[(Agent, e) for e in agents],
            *[(CustomClaim, e) for e in claims],
            *[(SolutionConfigSchema, e) for e in config_schemas],
        ]

        # Pass 1: remap each entity's own id.
        for model, entry in typed_entries:
            original = UUID(str(entry["id"]))
            owner = (
                await self.db.execute(
                    select(model.solution_id).where(model.id == original)  # type: ignore[attr-defined]
                )
            ).scalar_one_or_none()
            remapped = original if owner == sid else solution_entity_id(sid, original)
            id_map[original] = remapped
            entry["id"] = str(remapped)

        # Pass 2: translate cross-refs that name an in-bundle entity.
        for mform in forms:
            for fld in _FORM_WORKFLOW_REF_FIELDS:
                if mform.get(fld) is not None:
                    mform[fld] = _remap_ref(mform[fld], id_map)
            self._remap_form_field_providers(mform, id_map)
        for magent in agents:
            for fld in _AGENT_WORKFLOW_LIST_FIELDS + _AGENT_AGENT_LIST_FIELDS:
                vals = magent.get(fld)
                if isinstance(vals, list):
                    magent[fld] = [_remap_ref(v, id_map) for v in vals]

        return SolutionBundle(
            solution=bundle.solution,
            python_files=bundle.python_files,
            workflows=workflows,
            tables=tables,
            apps=apps,
            forms=forms,
            agents=agents,
            claims=claims,
            config_schemas=config_schemas,
            version=bundle.version,
            readme=bundle.readme,
        )

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

    @staticmethod
    def _validate_access_level(
        value: Any, enum_cls: type[Enum], entity: str
    ) -> str:
        """Coerce a manifest access_level against its enum BEFORE the DB write.

        Writing an unknown value straight into the enum-backed column raises a raw
        asyncpg ``InvalidTextRepresentationError`` that escapes as a 500. Validate
        here so a bad bundle fails loud as a SolutionDeployConflict (→ 409) with a
        clear message naming the offending value (Codex P3).
        """
        valid = {e.value for e in enum_cls}
        if value not in valid:
            raise SolutionDeployConflict(
                f"{entity} has invalid access_level '{value}'; "
                f"must be one of {sorted(valid)}"
            )
        return value

    @staticmethod
    def _parse_uuids(values: Any) -> list[UUID]:
        """Coerce a manifest list of id strings to UUIDs (None/empty → [])."""
        if not isinstance(values, list):
            return []
        return [UUID(str(v)) for v in values]

    async def _sync_agent_mcp_connections(
        self, agent_id: UUID, connection_ids: list[UUID]
    ) -> None:
        """Full-replace the agent's grants in the ``agent_mcp_connections``
        junction.

        Deploy is the only writer of solution-managed MCP grants — the AgentIndexer
        ignores the junction and the REST grant endpoints are read-only here — so
        this delete-all + insert reflects both adds AND removes across redeploys.
        ``connection_id`` refers to an env-scoped MCPConnection (not a solution
        entity), so the ids are used verbatim (no remap). ``granted_by`` is NULL
        for deploy-managed grants.
        """
        from src.models.orm.external_mcp import AgentMCPConnection

        await self.db.execute(
            delete(AgentMCPConnection).where(
                AgentMCPConnection.agent_id == agent_id
            )
        )
        now = datetime.now(timezone.utc)
        for connection_id in dict.fromkeys(connection_ids):  # dedupe, preserve order
            self.db.add(
                AgentMCPConnection(
                    agent_id=agent_id,
                    connection_id=connection_id,
                    granted_at=now,
                    granted_by=None,
                )
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
    ) -> set[UUID]:
        """Upsert table SCHEMA + POLICIES only. Row data (Document records) is
        runtime state and is never written or wiped by deploy (criterion 11).

        ``policies`` in the manifest is a flat list stored under the Table
        ``access`` JSONB column. A redeploy with a changed schema updates the
        ``schema`` JSONB in place; the table row (and its Documents via the
        FK) survives untouched.

        Returns the set of ADOPTED orphan table ids (Task 14c). A reattached
        table keeps the ORPHAN's id (its Documents reference it), which differs
        from this deploy's remapped bundle id — so the caller must add these ids
        to the reconcile's present-set, or the same deploy's reconcile sweep
        (``solution_id == sid AND id NOT IN bundle_ids``) would delete the table
        it just re-adopted.
        """
        from shared.policies.probe import make_seed_admin_bypass
        from src.core.pubsub import publish_policy_changed
        from src.models.contracts.policies import TablePolicies

        sid = solution.id
        adopted_ids: set[UUID] = set()

        # Table NAME is unique per install (ix_tables_solution_name_unique). Two
        # tables in THIS bundle sharing a name would hit that index as an
        # IntegrityError → an unhandled 500. Catch it deterministically up front
        # as a 409 SolutionDeployConflict naming the offending table. (A name
        # shared with a _repo/ or OTHER install's table is fine — uniqueness is
        # solution-scoped, so the developer never reasons about that namespace.)
        seen_names: set[str] = set()
        for mtbl in tables:
            nm = str(mtbl.get("name"))
            if nm in seen_names:
                raise SolutionDeployConflict(
                    f"two tables named '{nm}' in this Solution bundle; table names "
                    f"must be unique within an install"
                )
            seen_names.add(nm)

        for mtbl in tables:
            tbl_id = UUID(mtbl["id"])
            name = mtbl["name"]

            # Resolve + VALIDATE policies before persisting (mirrors REST/manifest
            # paths) so a malformed AST is rejected at deploy, not at read time.
            # Computed up front so both the reattach and the normal path use it.
            policies = mtbl.get("policies")
            if policies is not None:
                access = {"policies": policies}
                policy_model = TablePolicies.model_validate(access)  # raises on a bad AST
                from src.routers.tables import _validate_table_policy_claim_refs

                try:
                    await _validate_table_policy_claim_refs(
                        self.db,
                        solution.organization_id,
                        policy_model,
                        solution.id,
                    )
                except ValueError as exc:
                    raise SolutionDeployConflict(str(exc)) from exc
            else:
                # None / absent -> seed admin_bypass, matching API-created tables
                # and manifest import; without it RLS denies all table I/O.
                access = make_seed_admin_bypass()

            # ── Reattach (Task 14c) ─────────────────────────────────────────
            # Before creating a fresh (empty) table, adopt a surviving orphan
            # from a PRIOR install of THIS Solution (same slug + name + org) so
            # the customer's documents flow back in. We KEEP the orphan's id (its
            # Documents reference it); identity for resolution is the
            # solution-scoped (solution_id, name) uniqueness, not the id, so a
            # reattached table's id legitimately won't match this deploy's
            # remapped bundle id. If multiple orphans match (repeated
            # install/uninstall cycles), adopt the MOST RECENTLY orphaned one
            # (max orphaned_at) and leave the rest orphaned for manual cleanup.
            org_pred = (
                Table.organization_id == solution.organization_id
                if solution.organization_id is not None
                else Table.organization_id.is_(None)
            )
            # Fetch id + current access only — NOT the ORM object. The reattach
            # writes via a Core update() (below), which bypasses the unit-of-work
            # so the read-only before_flush backstop (solutions/guard.py) doesn't
            # fire; loading + mutating the ORM object would trip that guard the
            # moment we stamp solution_id (it then looks solution-managed).
            orphan_row = (
                await self.db.execute(
                    select(Table.id, Table.access)
                    .where(
                        Table.orphaned_at.is_not(None),
                        Table.origin_solution_slug == solution.slug,
                        Table.name == name,
                        org_pred,
                    )
                    .order_by(Table.orphaned_at.desc())
                )
            ).first()
            if orphan_row is not None:
                orphan_id, prev_access = orphan_row[0], orphan_row[1]
                await self.db.execute(
                    update(Table)
                    .where(Table.id == orphan_id)
                    .values(
                        solution_id=sid,
                        organization_id=solution.organization_id,
                        orphaned_at=None,
                        origin_solution_slug=None,
                        origin_solution_id=None,
                        name=name,
                        description=mtbl.get("description"),
                        schema=mtbl.get("schema"),
                        access=access,
                    )
                )
                adopted_ids.add(orphan_id)
                # A reattach with a changed policy must invalidate subscribers'
                # policy cache too (mirrors the normal path's intent).
                if prev_access != access:
                    await publish_policy_changed(str(orphan_id))
                continue  # adopted — skip the id-based upsert for this entry

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

            # Full-replace: description and schema are always set from the bundle
            # (solution-owned metadata), so removing them in the bundle clears
            # the DB value rather than leaving it stale.
            values: dict[str, Any] = {
                "name": name,
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

        return adopted_ids

    async def _upsert_claims(
        self, solution: Solution, claims: list[dict[str, Any]]
    ) -> None:
        """Upsert solution-owned Custom Claim definitions.

        Claims are deploy-owned definitions, not runtime data. Names are unique
        per install, matching the policy resolver's own-first lookup.
        """
        from src.models.contracts.claims import ClaimQuery

        sid = solution.id
        seen_names: set[str] = set()
        for mclaim in claims:
            name = str(mclaim.get("name"))
            if name in seen_names:
                raise SolutionDeployConflict(
                    f"two claims named '{name}' in this Solution bundle; claim names "
                    f"must be unique within an install"
                )
            seen_names.add(name)

        for mclaim in claims:
            claim_id = UUID(mclaim["id"])
            await self._guard_owner(CustomClaim, claim_id, sid)
            query = ClaimQuery.model_validate(mclaim["query"]).model_dump(mode="json")
            values: dict[str, Any] = {
                "organization_id": solution.organization_id,
                "solution_id": sid,
                "name": mclaim["name"],
                "description": mclaim.get("description"),
                "type": mclaim.get("type", "list"),
                "query": query,
            }
            await Upsert(
                model=CustomClaim, id=claim_id, values=values, match_on="id"
            ).execute(self.db)

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
            # Solution apps must be standalone_v2: only those are built to dist/
            # and served from _apps/{id}/. An inline_v1 app (the legacy default
            # when app_model is omitted) has NO working deploy path here — its
            # source would be dropped, leaving a published-but-sourceless app that
            # 404s or serves unrelated _repo/ source (Codex #11). Reject it loudly
            # BEFORE writing any row, rather than persist a broken app.
            if app_model != "standalone_v2":
                raise SolutionDeployConflict(
                    f"app '{slug}' has app_model='{app_model}'; Solution apps must "
                    f"be standalone_v2 (scaffold with `bifrost solution scaffold-app`). "
                    f"inline_v1 apps are not supported in a Solution bundle."
                )
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
            # App LOGO declared in the manifest (`logo:` path), carried by the
            # collector as base64 (the only way a solution-managed app gets a
            # logo — the upload endpoint is blocked for it). Validate + sanitize
            # like the upload endpoint, then stamp the row. Deploy is the publish,
            # so the logo is deploy-owned: present => set, absent => cleared,
            # keeping deploy idempotent/round-tripping.
            logo_data, logo_ct = _decode_logo(
                f"app '{slug}'", mapp.get("logo_b64"), mapp.get("logo_content_type")
            )
            values["logo_data"] = logo_data
            values["logo_content_type"] = logo_ct

            await Upsert(
                model=Application, id=app_id, values=values, match_on="id"
            ).execute(self.db)
            await self._sync_entity_roles(
                AppRole, "app_id", app_id, await self._resolve_roles(mapp)
            )

            # Every Solution app is standalone_v2 (guarded above) and is built to
            # dist/, served from _apps/{id}/.
            builds.append({
                "app_id": app_id,
                "src": mapp.get("src_files") or {},
                # Non-text assets (png/fonts/public/) carried as base64 by the
                # CLI/git collectors — decoded into the build input (P2-j/R4).
                "bin": mapp.get("bin_files") or {},
                # Prebuilt fast-path: UTF-8 dist text + non-UTF-8 dist binaries
                # (base64). Kept separate so the binaries are base64-decoded, not
                # UTF-8-encoded (which would corrupt them).
                "dist": mapp.get("dist_files"),
                "bin_dist": mapp.get("bin_dist_files"),
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
            bin_prebuilt = b.get("bin_dist")
            prebuilt_bytes: dict[str, bytes] | None = None
            if prebuilt or bin_prebuilt:
                prebuilt_bytes = {}
                # UTF-8 dist text → raw bytes.
                for k, v in (prebuilt or {}).items():
                    prebuilt_bytes[k] = v.encode("utf-8") if isinstance(v, str) else v
                # Non-UTF-8 dist assets travel as base64 — decode to the original
                # bytes so images/fonts/wasm round-trip byte-for-byte (a plain
                # .encode("utf-8") on the base64 string would write the base64
                # TEXT to S3, corrupting the asset).
                for k, v in (bin_prebuilt or {}).items():
                    prebuilt_bytes[k] = _b64.b64decode(v) if isinstance(v, str) else v
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
            # Stamp the install scope. The indexer preserves org/access (they are
            # env-specific), but access_level IS deploy-owned for a Solution: the
            # manifest declares it, so apply it here (the entity is read-only
            # outside deploy, so this is the only place it can be set — Codex #14).
            form_values: dict[str, Any] = {
                "organization_id": solution.organization_id,
                "solution_id": sid,
            }
            if mform.get("access_level") is not None:
                from src.models.enums import FormAccessLevel

                form_values["access_level"] = self._validate_access_level(
                    mform["access_level"], FormAccessLevel, f"form {form_id}"
                )
            await self.db.execute(
                update(Form).where(Form.id == form_id).values(**form_values)
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
            # access_level is deploy-owned (manifest-declared); apply it here —
            # the indexer preserves it and the entity is read-only outside deploy
            # (Codex #14). org/solution scope is stamped alongside.
            #
            # max_iterations / max_token_budget are likewise deploy-owned: the
            # AgentIndexer does NOT persist them (it handles tool_ids/delegations
            # only), so without stamping them here a redeploy silently drops the
            # manifest's values back to the column defaults. Apply when present.
            agent_values: dict[str, Any] = {
                "organization_id": solution.organization_id,
                "solution_id": sid,
            }
            if magent.get("access_level") is not None:
                from src.models.enums import AgentAccessLevel

                agent_values["access_level"] = self._validate_access_level(
                    magent["access_level"], AgentAccessLevel, f"agent {agent_id}"
                )
            if magent.get("max_iterations") is not None:
                agent_values["max_iterations"] = magent["max_iterations"]
            if magent.get("max_token_budget") is not None:
                agent_values["max_token_budget"] = magent["max_token_budget"]
            await self.db.execute(
                update(Agent).where(Agent.id == agent_id).values(**agent_values)
            )
            # Sync role bindings (indexer doesn't touch role rows) — Codex P1-d.
            await self._sync_entity_roles(
                AgentRole, "agent_id", agent_id, await self._resolve_roles(magent)
            )
            # Sync MCP-connection grants. Like role bindings, the AgentIndexer does
            # NOT touch the agent_mcp_connections junction and the REST grant
            # endpoints are read-only for managed entities, so deploy is the only
            # writer — full-replace from the manifest so a redeploy reflects both
            # adds and removes. connection_ids reference env-scoped MCPConnection
            # rows (NOT solution entities), so they are NOT id-remapped.
            await self._sync_agent_mcp_connections(
                agent_id, self._parse_uuids(magent.get("mcp_connection_ids"))
            )

    async def _upsert_config_schemas(
        self, solution: Solution, config_schemas: list[dict[str, Any]]
    ) -> None:
        """Upsert this install's config DECLARATIONS (key/type/required/desc/
        default/position). Config VALUES are NEVER written here — they are
        instance-owned Config rows set by the operator. Mirrors
        :meth:`_upsert_tables`: solution-scoped key uniqueness, ownership guard
        (via ``_guard_owner``), full-replace.
        """
        sid = solution.id

        # Key is unique per install (ix_solution_config_schema_sol_key_unique).
        # Two declarations sharing a key in THIS bundle would hit the index as an
        # IntegrityError → 500. Catch deterministically up front as a 409.
        seen: set[str] = set()
        for entry in config_schemas:
            k = str(entry.get("key"))
            if k in seen:
                raise SolutionDeployConflict(
                    f"two config declarations named '{k}' in this Solution bundle; "
                    f"config keys must be unique within an install"
                )
            seen.add(k)

        for entry in config_schemas:
            cid = UUID(entry["id"])
            await self._guard_owner(SolutionConfigSchema, cid, sid)
            values: dict[str, Any] = {
                "solution_id": sid,
                "key": entry["key"],
                "type": entry["type"],
                "required": bool(entry.get("required", False)),
                "description": entry.get("description"),
                "default": entry.get("default"),
                "position": int(entry.get("position", 0)),
            }
            await Upsert(
                model=SolutionConfigSchema, id=cid, values=values, match_on="id"
            ).execute(self.db)

    async def _upsert_integration_shells(
        self, connection_schemas: list[dict[str, Any]]
    ) -> int:
        """Create an EMPTY integration (+ config schema + OAuth skeleton) for any
        declared connection whose global ``Integration`` doesn't already exist.

        Never touches an existing integration — a configured integration carries
        the admin's real client_id/secret and org mappings, which a bundle must
        never clobber. The shell gives the admin a pre-wired place to enter
        credentials (config schema + an OAuthProvider with empty
        ``client_id``/``encrypted_client_secret``). Returns the count created.
        """
        from src.models.orm.integrations import Integration, IntegrationConfigSchema
        from src.models.orm.oauth import OAuthProvider

        created = 0
        for decl in connection_schemas:
            name = decl["integration_name"]
            template = decl.get("template") or {}
            exists = (
                await self.db.execute(
                    select(Integration).where(Integration.name == name)
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue  # never clobber a configured integration
            integ = Integration(
                name=name,
                entity_id_name=template.get("entity_id_name"),
                default_entity_id=template.get("default_entity_id"),
            )
            self.db.add(integ)
            await self.db.flush()  # need integ.id for the child rows
            for s in template.get("config_schema") or []:
                self.db.add(
                    IntegrationConfigSchema(
                        integration_id=integ.id,
                        key=s["key"],
                        type=s["type"],
                        required=bool(s.get("required")),
                        description=s.get("description"),
                        options=s.get("options"),
                        position=s.get("position", 0),
                    )
                )
            oauth = template.get("oauth")
            if oauth:
                # Global shells (organization_id NULL) never collide on provider_name: the unique index (organization_id, provider_name) treats NULLs as distinct in Postgres.
                self.db.add(
                    OAuthProvider(
                        integration_id=integ.id,
                        provider_name=oauth.get("provider_name") or name,
                        display_name=oauth.get("display_name"),
                        oauth_flow_type=oauth.get("oauth_flow_type")
                        or "authorization_code",
                        client_id="",  # empty shell — admin fills credentials
                        encrypted_client_secret=b"",
                        authorization_url=oauth.get("authorization_url"),
                        token_url=oauth.get("token_url"),
                        audience=oauth.get("audience"),
                        token_url_defaults=oauth.get("token_url_defaults") or {},
                        entity_id_source=oauth.get("entity_id_source"),
                        scopes=oauth.get("scopes") or [],
                        redirect_uri=oauth.get("redirect_uri"),
                        status="not_connected",
                    )
                )
            created += 1
        return created

    async def _upsert_connection_declarations(
        self, solution: Solution, connection_schemas: list[dict[str, Any]]
    ) -> None:
        """Persist the install's connection DECLARATIONS as SolutionConnectionSchema
        rows (upsert by ``(solution_id, integration_name)``), reconciling removals.

        Mirrors the capture writer (capture.py::_connection_entries), but keyed to
        the INSTALL's id so a plain deploy / zip-install / CLI-deploy surfaces the
        connection at /setup — not only a capture-in-place. Declarations key on the
        integration NAME (no per-install id), so this never goes through the remap.
        Full-replace semantics: a re-deploy whose bundle drops a connection deletes
        the now-stale row, matching the deploy-owned full-replace of every other
        entity.
        """
        from src.models.orm.solution_connection_schema import SolutionConnectionSchema

        # Read just the existing NAMES — never hold managed ORM instances, so a
        # later mutation can't land them in session.dirty and trip the always-on
        # read-only guard. Every write below is a Core statement (insert/update/
        # delete) that bypasses the ORM unit-of-work, matching the rest of deploy.
        existing_names: set[str] = set(
            (
                await self.db.execute(
                    select(SolutionConnectionSchema.integration_name).where(
                        SolutionConnectionSchema.solution_id == solution.id
                    )
                )
            )
            .scalars()
            .all()
        )

        declared_names: set[str] = set()
        for decl in connection_schemas:
            name = decl["integration_name"]
            declared_names.add(name)
            template = decl.get("template") or {}
            position = int(decl.get("position", 0))
            if name in existing_names:
                await self.db.execute(
                    update(SolutionConnectionSchema)
                    .where(
                        SolutionConnectionSchema.solution_id == solution.id,
                        SolutionConnectionSchema.integration_name == name,
                    )
                    .values(template=template, position=position)
                )
            else:
                await self.db.execute(
                    insert(SolutionConnectionSchema).values(
                        solution_id=solution.id,
                        integration_name=name,
                        template=template,
                        position=position,
                    )
                )

        # Reconcile removals: drop declarations no longer in the bundle.
        stale_names = existing_names - declared_names
        if stale_names:
            await self.db.execute(
                delete(SolutionConnectionSchema).where(
                    SolutionConnectionSchema.solution_id == solution.id,
                    SolutionConnectionSchema.integration_name.in_(stale_names),
                )
            )

    async def _reattach_orphan_configs(
        self, solution: Solution, declared_keys: set[str]
    ) -> int:
        """Un-orphan config VALUES from a prior install of this Solution so the
        operator doesn't re-enter them (Task 14c).

        Config has no ``solution_id`` — values are keyed by ``(key, org)``, so
        "reattach" is just clearing the orphan stamp on the matching live rows.
        Scoped to this install's slug + declared keys + org. Idempotent and safe
        even when ANOTHER live install in the same org shares one of these keys:
        a Config value is matched by (key, org), not an FK, so two installs can
        share a row. Clearing the stamp on an already-live value is a no-op, and
        we only touch rows that are CURRENTLY orphaned (``orphaned_at IS NOT
        NULL``) and tattooed with THIS slug — so we never disturb a value that a
        different live install owns.
        """
        if not declared_keys:
            return 0
        from src.models.orm.config import Config

        org_pred = (
            Config.organization_id == solution.organization_id
            if solution.organization_id is not None
            else Config.organization_id.is_(None)
        )
        result = await self.db.execute(
            update(Config)
            .where(
                org_pred,
                Config.key.in_(declared_keys),
                Config.origin_solution_slug == solution.slug,
                Config.orphaned_at.is_not(None),
            )
            .values(orphaned_at=None, origin_solution_slug=None, origin_solution_id=None)
        )
        return result.rowcount or 0

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
        self, sid: UUID, bundle: SolutionBundle, adopted_table_ids: set[UUID]
    ) -> tuple[int, int, int, int, int, int, set[UUID]]:
        """Delete this install's entities that are absent from the bundle.

        Strictly scoped: ``solution_id == sid AND id NOT IN bundle_ids``. Never
        touches _repo/ (solution_id IS NULL) or another install. For tables,
        only the Table row is swept — Document (row) data is never deleted here;
        a removed table's rows go via the Table FK cascade, which only fires when
        the table itself is genuinely absent from the bundle. For apps, the
        ``_apps/{id}/dist/`` artifact is deleted alongside the row. Config
        DECLARATIONS (SolutionConfigSchema) are also reconciled here, though
        their deleted count is intentionally not surfaced. Returns
        (workflows, tables, apps, forms, agents, claims) deleted counts.

        ``adopted_table_ids`` are orphan ids re-adopted by THIS deploy
        (Task 14c). They carry the orphan's id, not this deploy's remapped bundle
        id, so they must be added to the tables' present-set — otherwise this same
        sweep would delete the table we just reattached.
        """
        wf_deleted = len(
            await self._reconcile_one(
                Workflow, sid, {UUID(w["id"]) for w in bundle.workflows}
            )
        )
        tbl_deleted = len(
            await self._reconcile_one(
                Table,
                sid,
                {UUID(t["id"]) for t in bundle.tables} | adopted_table_ids,
            )
        )
        # Stale app ids are kept (not just counted): their ``_apps/{id}/dist/``
        # artifacts must be swept in the S3 phase (deferred via
        # :meth:`_delete_stale_app_dist` so a DB rollback leaves no dangling S3
        # deletions — Codex P1-e).
        stale_app_dist = await self._reconcile_one(
            Application, sid, {UUID(a["id"]) for a in bundle.apps}
        )
        form_deleted = len(
            await self._reconcile_one(Form, sid, {UUID(f["id"]) for f in bundle.forms})
        )
        agent_deleted = len(
            await self._reconcile_one(Agent, sid, {UUID(a["id"]) for a in bundle.agents})
        )
        claim_deleted = len(
            await self._reconcile_one(
                CustomClaim, sid, {UUID(c["id"]) for c in bundle.claims}
            )
        )
        # Config declarations reconcile alongside the rest; deploy is the single
        # writer for solution-owned schema rows. The count is not surfaced — no
        # consumer needs a config-deleted tally — so the return value is dropped.
        _ = await self._reconcile_one(
            SolutionConfigSchema, sid, {UUID(c["id"]) for c in bundle.config_schemas}
        )
        return (
            wf_deleted, tbl_deleted, len(stale_app_dist), form_deleted, agent_deleted,
            claim_deleted,
            stale_app_dist,
        )

    async def _reconcile_one(
        self, model: type, sid: UUID, present_ids: set[UUID]
    ) -> set[UUID]:
        """Delete this install's stale rows (DB-only). Returns the stale ids."""
        # Find this install's rows that are NOT in the bundle.
        stmt = select(model.id).where(model.solution_id == sid)  # type: ignore[attr-defined]
        existing = set((await self.db.execute(stmt)).scalars().all())
        stale = existing - present_ids
        if not stale:
            return set()
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
        return stale
