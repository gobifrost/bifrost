"""Capture loose `_repo/` entities into a Solution install.

Capture is the explicit road from legacy/ad-hoc entities into a lifecycle-owned
Solution. It stamps ownership in place and builds an export bundle containing
the captured definitions. Runtime data stays in place.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    import pathspec

from src.models.enums import ConfigType
from src.models.orm.agents import Agent, AgentDelegation, AgentRole, AgentTool
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.events import (
    EventSource,
    EventSubscription,
    ScheduleSource,
    WebhookSource,
)
from src.models.orm.forms import Form, FormField, FormRole
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution
from src.models.orm.tables import Document, Table
from src.models.orm.users import Role
from src.models.orm.workflows import Workflow
from src.models.orm.workflow_roles import WorkflowRole
from src.services.repo_storage import RepoStorage
from src.services.solutions.deploy import SolutionBundle, solution_entity_id
from src.services.solutions.vendoring import vendor_shared_deps


class SolutionCaptureConflict(ValueError):
    """The requested capture would violate ownership or scope rules."""


@dataclass
class SolutionCaptureSelectors:
    workflows: list[UUID]
    tables: list[UUID]
    apps: list[UUID]
    forms: list[UUID]
    agents: list[UUID]
    claims: list[UUID]
    configs: list[str]
    events: list[UUID] = field(default_factory=list)


@dataclass
class SolutionCaptureResult:
    workflows_captured: int = 0
    tables_captured: int = 0
    apps_captured: int = 0
    forms_captured: int = 0
    agents_captured: int = 0
    claims_captured: int = 0
    config_declarations_captured: int = 0
    events_captured: int = 0


logger = logging.getLogger(__name__)

# Hard cap on rows exported per table to keep the encrypted blob bounded.
# If a table exceeds this, a WARNING is logged (table name + actual count)
# and only the first TABLE_ROW_CAP rows are included — no silent truncation.
TABLE_ROW_CAP = 50_000

# Hard cap on files exported per solution to keep the encrypted blob bounded.
# If a solution exceeds this, a WARNING is logged (solution slug + actual count)
# and only the first FILE_CAP files are included — no silent truncation.
FILE_CAP = 1_000


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


@lru_cache(maxsize=1)
def _ignore_spec() -> "pathspec.PathSpec":
    """Matcher over the canonical CLI skip-list (build output, caches, .env*).

    Reuses ``bifrost.ignore_patterns`` so capture skips exactly what the CLI
    skips on push/sync — secret files and bloat never enter a captured bundle.
    """
    import pathspec

    from bifrost.ignore_patterns import DEFAULT_IGNORE_PATTERNS

    return pathspec.PathSpec.from_lines("gitwildmatch", DEFAULT_IGNORE_PATTERNS)


class SolutionCaptureService:
    def __init__(self, db: AsyncSession, repo: RepoStorage | None = None):
        self.db = db
        self.repo = repo or RepoStorage()

    async def capture(
        self,
        solution: Solution,
        selectors: SolutionCaptureSelectors,
        *,
        include_imports: bool = False,
        captured_by: UUID | None = None,
    ) -> SolutionCaptureResult:
        """Validate candidates, stamp ownership, and build the stored export.

        ``include_imports`` controls Python bundling: False (default) bundles
        only the captured workflows' own source files; True also bundles the
        transitive import closure of ``modules/`` they reference (never the
        whole ``modules/`` tree).

        ``captured_by`` is the acting user, recorded on each ``pending_captures``
        queue row so a deploy can name who captured an un-pulled entity.
        """
        await self._reject_inline_apps(selectors.apps)
        await self._capture_model(Workflow, solution, selectors.workflows)
        await self._capture_model(Table, solution, selectors.tables)
        await self._capture_model(Application, solution, selectors.apps)
        await self._capture_model(Form, solution, selectors.forms)
        await self._capture_model(Agent, solution, selectors.agents)
        await self._capture_model(CustomClaim, solution, selectors.claims)
        await self._capture_configs(solution, selectors.configs)
        await self._capture_events(solution, selectors.events)
        await self._enqueue_pending(solution, selectors, captured_by)
        await self.db.flush()

        return SolutionCaptureResult(
            workflows_captured=len(set(selectors.workflows)),
            tables_captured=len(set(selectors.tables)),
            apps_captured=len(set(selectors.apps)),
            forms_captured=len(set(selectors.forms)),
            agents_captured=len(set(selectors.agents)),
            claims_captured=len(set(selectors.claims)),
            config_declarations_captured=len(set(selectors.configs)),
            events_captured=len(set(selectors.events)),
        )

    async def _enqueue_pending(
        self,
        solution: Solution,
        selectors: SolutionCaptureSelectors,
        captured_by: UUID | None,
    ) -> None:
        """Insert a ``pending_captures`` row per captured entity that deploy's
        full-replace reconcile could silently delete (table/form/agent/config/
        event/claim). Idempotent via the UNIQUE constraint — re-capture is a
        no-op. Workflows round-trip through ``.bifrost/workflows.yaml`` and apps
        are file-source, so neither is at risk and neither is enqueued.

        Uses a Core upsert (not ORM add) to stay consistent with the
        solution-managed write discipline.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from src.models.orm.pending_capture import PendingCaptureORM

        queued: list[tuple[str, str]] = []
        queued += [("table", str(i)) for i in dict.fromkeys(selectors.tables)]
        queued += [("form", str(i)) for i in dict.fromkeys(selectors.forms)]
        queued += [("agent", str(i)) for i in dict.fromkeys(selectors.agents)]
        queued += [("config", str(k)) for k in dict.fromkeys(selectors.configs)]
        queued += [("event", str(i)) for i in dict.fromkeys(selectors.events)]
        queued += [("claim", str(i)) for i in dict.fromkeys(selectors.claims)]

        for entity_type, entity_id in queued:
            stmt = (
                pg_insert(PendingCaptureORM.__table__)
                .values(
                    id=uuid4(),
                    solution_id=solution.id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    captured_at=datetime.now(timezone.utc),
                    captured_by=captured_by,
                )
                .on_conflict_do_nothing(constraint="uq_pending_capture_entity")
            )
            await self.db.execute(stmt)

    async def _capture_events(self, solution: Solution, ids: list[UUID]) -> None:
        """Adopt EventSources (+ their subscriptions) into the install.

        Reuses ``_capture_model`` for the EventSource row (scope rules, ownership
        guard, org re-stamp), then stamps ``solution_id`` on the source's
        EventSubscription rows so they too become managed. Child schedule/webhook
        rows are owned transitively via their EventSource FK cascade — no
        ``solution_id`` of their own.
        """
        await self._capture_model(EventSource, solution, ids)
        for source_id in dict.fromkeys(ids):
            await self.db.execute(
                update(EventSubscription)
                .where(EventSubscription.event_source_id == source_id)
                .values(solution_id=solution.id)
            )

    async def _capture_model(
        self, model: type, solution: Solution, ids: list[UUID]
    ) -> None:
        for entity_id in dict.fromkeys(ids):
            row = (
                await self.db.execute(
                    select(model.solution_id, model.organization_id).where(  # type: ignore[attr-defined]
                        model.id == entity_id  # type: ignore[attr-defined]
                    )
                )
            ).first()
            if row is None:
                raise SolutionCaptureConflict(
                    f"{model.__tablename__} {entity_id} does not exist"  # type: ignore[attr-defined]
                )
            owner, org_id = row
            if owner is not None and owner != solution.id:
                raise SolutionCaptureConflict(
                    f"{model.__tablename__} {entity_id} is already owned by solution {owner}"  # type: ignore[attr-defined]
                )

            # Scope rules (capture-design Task 5):
            #  - same org           → adopt in place.
            #  - global → org-scoped solution → adopt AND re-stamp the entity's
            #    org down to the solution's org (the common migration case: a
            #    loose global _repo entity that really belonged to one org).
            #  - any OTHER concrete org (org-A entity into an org-B solution) →
            #    REFUSE (cross-tenant, never allowed).
            restamp_org = False
            if org_id == solution.organization_id:
                pass
            elif org_id is None and solution.organization_id is not None:
                restamp_org = True
            else:
                raise SolutionCaptureConflict(
                    f"{model.__tablename__} {entity_id} is scoped to {org_id}; "
                    f"solution {solution.id} is scoped to {solution.organization_id}"
                )

            if owner == solution.id and not restamp_org:
                continue

            values: dict[str, Any] = {"solution_id": solution.id}
            if restamp_org:
                # Re-stamp global → the solution's org. Documents/rows carry no
                # org of their own (table data stays in place, not copied), so
                # only the entity row's organization_id changes.
                values["organization_id"] = solution.organization_id
            await self.db.execute(
                update(model)
                .where(model.id == entity_id)  # type: ignore[attr-defined]
                .values(**values)
            )

    async def _reject_inline_apps(self, app_ids: list[UUID]) -> None:
        """Refuse to capture an inline_v1 app — Solution apps must be
        standalone_v2 (only v2 builds to ``dist/`` + serves from ``_apps/{id}``).

        Capturing a v1 app would stamp ``solution_id`` on a row that the
        deployer then REJECTS (deploy.py: "Solution apps must be standalone_v2"),
        producing an export bundle that can never be installed. Fail loudly here,
        at the moment of the mistake, instead of shipping a broken bundle. The
        migration path is to re-author the app as v2 (``solution scaffold-app``)
        and capture only its backing tables/workflows — see the v1→v2 migration
        guide.
        """
        for app_id in dict.fromkeys(app_ids):
            row = (
                await self.db.execute(
                    select(Application.app_model, Application.name).where(
                        Application.id == app_id
                    )
                )
            ).first()
            if row is None:
                continue  # _capture_model raises the not-found below.
            app_model, name = row
            if app_model != "standalone_v2":
                raise SolutionCaptureConflict(
                    f"app '{name}' has app_model={app_model!r}; only standalone_v2 "
                    f"apps can be captured into a Solution. Re-author it as v2 "
                    f"(`bifrost solution scaffold-app`) and capture its backing "
                    f"tables/workflows instead — see the v1→v2 migration guide."
                )

    async def _capture_configs(self, solution: Solution, keys: list[str]) -> None:
        for position, key in enumerate(dict.fromkeys(keys)):
            config = (
                await self.db.execute(
                    select(Config).where(
                        Config.key == key,
                        Config.organization_id == solution.organization_id
                        if solution.organization_id is not None
                        else Config.organization_id.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if config is None:
                raise SolutionCaptureConflict(
                    f"config '{key}' does not exist in this solution scope"
                )
            existing = (
                await self.db.execute(
                    select(SolutionConfigSchema.id).where(
                        SolutionConfigSchema.solution_id == solution.id,
                        SolutionConfigSchema.key == key,
                    )
                )
            ).scalar_one_or_none()
            schema_id = existing or solution_entity_id(solution.id, config.id)
            values = {
                "solution_id": solution.id,
                "key": key,
                "type": _enum_value(config.config_type or ConfigType.STRING),
                "required": False,
                "description": config.description,
                "default": None,
                "position": position,
            }
            if existing is None:
                self.db.add(SolutionConfigSchema(id=schema_id, **values))
            else:
                await self.db.execute(
                    update(SolutionConfigSchema)
                    .where(SolutionConfigSchema.id == schema_id)
                    .values(**values)
                )

    async def bundle_for(
        self,
        solution: Solution,
        *,
        include_imports: bool = False,
        include_values: bool = False,
        include_data: bool = False,
    ) -> SolutionBundle:
        workflows = await self._workflow_entries(solution.id)
        tables = await self._table_entries(solution.id)
        apps = await self._app_entries(solution.id)
        forms = await self._form_entries(solution.id)
        agents = await self._agent_entries(solution.id)
        claims = await self._claim_entries(solution.id)
        config_schemas = await self._config_entries(solution.id)
        connection_schemas = await self._connection_entries(solution.id)
        events = await self._event_entries(solution.id)
        python_files = await self._python_files(
            workflows, include_imports=include_imports
        )
        config_values: dict[str, str] = {}
        if include_values:
            config_values = await self._config_values(solution)
        table_data: dict[str, list[dict[str, Any]]] = {}
        if include_data:
            table_data = await self._table_data(solution)
        solution_files: list[Any] = []
        if include_data:
            solution_files = await self._solution_file_entries(solution)
        return SolutionBundle(
            solution=solution,
            python_files=python_files,
            workflows=workflows,
            tables=tables,
            apps=apps,
            forms=forms,
            agents=agents,
            claims=claims,
            config_schemas=config_schemas,
            connection_schemas=connection_schemas,
            events=events,
            readme=solution.readme,
            version=solution.version,
            config_values=config_values,
            table_data=table_data,
            solution_files=solution_files,
        )

    async def _workflow_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        from bifrost.manifest import ManifestWorkflow
        from bifrost.manifest_codec import Destination

        rows = (
            await self.db.execute(
                select(Workflow).where(Workflow.solution_id == solution_id)
            )
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for w in rows:
            role_ids = await self._role_ids(WorkflowRole, "workflow_id", w.id)
            role_names = await self._role_names(role_ids)
            out.append(
                ManifestWorkflow.from_row(w, roles=role_ids).view(
                    Destination.INSTALL,
                    extras={"roles": role_ids, "role_names": role_names},
                )
            )
        return out

    async def _event_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        """Portable trigger entries for this install's managed EventSources.

        Each entry is a ManifestEventSource-shaped dict (source + nested
        schedule/webhook config + subscriptions). Built via the canonical
        ``ManifestEventSource.from_row`` (same code git-sync uses), which already
        OMITS the webhook instance secrets (``state``/``external_id``/
        ``expires_at``) — only the portable ``config`` travels. The instance
        re-establishes the external subscription + binds ``integration_id`` after
        install.
        """
        from bifrost.manifest import ManifestEventSource
        from bifrost.manifest_codec import Destination

        sources = (
            await self.db.execute(
                select(EventSource).where(EventSource.solution_id == solution_id)
            )
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for es in sources:
            schedule = (
                await self.db.execute(
                    select(ScheduleSource).where(
                        ScheduleSource.event_source_id == es.id
                    )
                )
            ).scalar_one_or_none()
            webhook = (
                await self.db.execute(
                    select(WebhookSource).where(
                        WebhookSource.event_source_id == es.id
                    )
                )
            ).scalar_one_or_none()
            subs = (
                await self.db.execute(
                    select(EventSubscription).where(
                        EventSubscription.event_source_id == es.id
                    )
                )
            ).scalars().all()
            out.append(ManifestEventSource.from_row(es, schedule=schedule, webhook=webhook, subscriptions=list(subs)).view(Destination.INSTALL))
        return out

    async def _table_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        from bifrost.manifest import ManifestTable
        from bifrost.manifest_codec import Destination

        rows = (
            await self.db.execute(select(Table).where(Table.solution_id == solution_id))
        ).scalars().all()
        return [ManifestTable.from_row(t).view(Destination.INSTALL) for t in rows]

    async def _claim_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        from bifrost.manifest import ManifestCustomClaim
        from bifrost.manifest_codec import Destination

        rows = (
            await self.db.execute(
                select(CustomClaim).where(CustomClaim.solution_id == solution_id)
            )
        ).scalars().all()
        return [ManifestCustomClaim.from_row(c).view(Destination.INSTALL) for c in rows]

    async def _app_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        from bifrost.manifest import ManifestApp
        from bifrost.manifest_codec import Destination

        rows = (
            await self.db.execute(
                select(Application).where(Application.solution_id == solution_id)
            )
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for app in rows:
            src_files, bin_files = await self._app_source_files(app)
            # Carry the app icon (base64) under the keys the deployer reads
            # (deploy.py:_decode_logo via mapp["logo_b64"]/["logo_content_type"]).
            # Without these, deploy treats an absent logo as "clear it", so a
            # capture → re-deploy round-trip would wipe the icon (Codex 6b).
            logo_b64 = (
                base64.b64encode(app.logo_data).decode("ascii")
                if app.logo_data
                else None
            )
            # When an app was deployed with a prebuilt dist and no source files
            # (dist_files-only fast path), src_files and bin_files are both empty.
            # The export strips dist_files (build output) from the zip manifest, so
            # a plain re-install would receive an app with nothing to build — the
            # Vite step fails on an empty workdir. Carry the dist from S3 so the
            # deployer can use the prebuilt fast-path on re-install without a Vite
            # rebuild. Mirror the src/bin split: UTF-8 text → dist_files (raw),
            # non-UTF-8 binary (images/fonts/wasm) → bin_dist_files (base64). They
            # MUST stay in separate keys — the deployer .encode("utf-8")s a
            # dist_files value verbatim, which would corrupt a base64-of-binary
            # string into the base64 text bytes instead of the original asset.
            dist_files: dict[str, str] | None = None
            bin_dist_files: dict[str, str] | None = None
            if not src_files and not bin_files:
                from src.services.solutions.app_build import SolutionAppBuilder

                builder = SolutionAppBuilder()
                rels = await builder.list_dist(app.id)
                if rels:
                    text_dist: dict[str, str] = {}
                    binary_dist: dict[str, str] = {}
                    for rel in rels:
                        data = await builder.read_dist(app.id, rel)
                        try:
                            text_dist[rel] = data.decode("utf-8")
                        except UnicodeDecodeError:
                            binary_dist[rel] = base64.b64encode(data).decode("ascii")
                    if text_dist:
                        dist_files = text_dist
                    if binary_dist:
                        bin_dist_files = binary_dist
            roles = await self._role_ids(AppRole, "app_id", app.id)
            out.append(
                ManifestApp.from_row(app, roles=roles).view(
                    Destination.INSTALL,
                    extras={
                        "repo_path": app.repo_path,
                        "logo_b64": logo_b64,
                        "logo_content_type": app.logo_content_type,
                        "src_files": src_files if src_files else None,
                        "bin_files": bin_files if bin_files else None,
                        "dist_files": dist_files,
                        "bin_dist_files": bin_dist_files,
                        "role_names": await self._role_names(roles),
                    },
                )
            )
        return out

    async def _form_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        from bifrost.manifest import ManifestForm
        from bifrost.manifest_codec import Destination

        rows = (
            await self.db.execute(select(Form).where(Form.solution_id == solution_id))
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for form in rows:
            fields = (
                await self.db.execute(
                    select(FormField)
                    .where(FormField.form_id == form.id)
                    .order_by(FormField.position)
                )
            ).scalars().all()
            roles = await self._role_ids(FormRole, "form_id", form.id)
            # form_schema for install uses _form_field_entry (includes position) — passed
            # via extras to override the model's schema (built without position).
            form_schema = {"fields": [self._form_field_entry(f) for f in fields]}
            out.append(
                ManifestForm.from_row(form, roles=roles).view(
                    Destination.INSTALL,
                    extras={
                        "workflow_path": form.workflow_path,
                        "workflow_function_name": form.workflow_function_name,
                        "role_names": await self._role_names(roles),
                        "form_schema": form_schema,
                    },
                )
            )
        return out

    async def _agent_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        from bifrost.manifest import ManifestAgent
        from bifrost.manifest_codec import Destination

        rows = (
            await self.db.execute(select(Agent).where(Agent.solution_id == solution_id))
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for agent in rows:
            roles = await self._role_ids(AgentRole, "agent_id", agent.id)
            tool_ids = await self._junction_ids(AgentTool, "agent_id", "workflow_id", agent.id)
            delegated_agent_ids = await self._junction_ids(
                AgentDelegation, "parent_agent_id", "child_agent_id", agent.id
            )
            # Install bundle omits mcp_connection_ids — only git_sync carries them.
            # max_run_timeout is a transport extra (not a model field).
            out.append(
                ManifestAgent.from_row(
                    agent,
                    roles=roles,
                    tool_ids=tool_ids,
                    delegated_agent_ids=delegated_agent_ids,
                ).view(
                    Destination.INSTALL,
                    extras={
                        "max_run_timeout": agent.max_run_timeout,
                        "role_names": await self._role_names(roles),
                    },
                )
            )
        return out

    async def _config_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        from bifrost.manifest import ManifestSolutionConfigSchema
        from bifrost.manifest_codec import Destination

        rows = (
            await self.db.execute(
                select(SolutionConfigSchema)
                .where(SolutionConfigSchema.solution_id == solution_id)
                .order_by(SolutionConfigSchema.position)
            )
        ).scalars().all()
        return [
            ManifestSolutionConfigSchema.from_row(c).view(Destination.INSTALL)
            for c in rows
        ]

    async def _connection_entries(self, solution_id: UUID) -> list[dict[str, Any]]:
        """Return the install's integration declarations.

        Source of truth is the persisted ``SolutionConnectionSchema`` rows
        (created by deploy / a prior capture). When they exist — the
        export / DR path for an installed or deployed solution — return them
        directly: they already carry the scrubbed template + position, and the
        workflow source is unreadable for a deployed install (it lives under
        ``_solutions/`` not ``_repo/``), so a re-scan would silently drop them.

        Only when NO persisted rows exist (a fresh capture from a ``_repo/``
        workspace that was never deployed) do we fall back to scanning each
        workflow's source for ``integrations.get("X")`` refs, resolving each to
        a global Integration, building a scrubbed template, and upserting
        ``SolutionConnectionSchema`` rows (by name) as a side-effect.
        """
        from src.models.orm.integrations import Integration
        from src.models.orm.solution_connection_schema import SolutionConnectionSchema
        from src.services.solutions.integration_template import (
            build_integration_template,
        )
        from src.services.solutions.ref_scanner import scan_integration_refs

        persisted = (
            await self.db.execute(
                select(SolutionConnectionSchema)
                .where(SolutionConnectionSchema.solution_id == solution_id)
                .order_by(SolutionConnectionSchema.position)
            )
        ).scalars().all()
        if persisted:
            return [
                {
                    "integration_name": r.integration_name,
                    "template": r.template,
                    "position": r.position,
                }
                for r in persisted
            ]

        wfs = (
            await self.db.execute(
                select(Workflow).where(Workflow.solution_id == solution_id)
            )
        ).scalars().all()
        names: set[str] = set()
        for wf in wfs:
            if not wf.path:
                continue
            try:
                src = (await self.repo.read(wf.path)).decode("utf-8")
            except Exception:
                # Source not in _repo/ (already-deployed under _solutions/) —
                # mirror _python_files: skip rather than fail the capture.
                continue
            names |= scan_integration_refs(src)

        entries: list[dict[str, Any]] = []
        for pos, name in enumerate(sorted(names)):
            integ = (
                await self.db.execute(
                    select(Integration).where(Integration.name == name)
                )
            ).scalar_one_or_none()
            if integ is None:
                template: dict[str, Any] = {
                    "name": name, "config_schema": [], "oauth": None,
                }
            else:
                template = build_integration_template(integ)
            entries.append(
                {"integration_name": name, "template": template, "position": pos}
            )
            existing = (
                await self.db.execute(
                    select(SolutionConnectionSchema).where(
                        SolutionConnectionSchema.solution_id == solution_id,
                        SolutionConnectionSchema.integration_name == name,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self.db.add(SolutionConnectionSchema(
                    solution_id=solution_id, integration_name=name,
                    template=template, position=pos,
                ))
            else:
                existing.template = template
                existing.position = pos
        return entries

    async def _config_values(self, solution: Solution) -> dict[str, str]:
        """Read the plaintext value for each declared config key that has a value set.

        Config.value is stored as JSONB ``{"value": <stored_value>}``.  For
        STRING configs the stored value IS the plaintext.  For SECRET configs
        the stored value is encrypted (via ``encrypt_secret``); we decrypt it
        here with ``decrypt_secret`` so the blob carries the original plaintext.

        Only keys that actually have a Config row in scope are included — keys
        with no set value are silently skipped (they will require manual entry
        after install on the target environment).
        """
        from src.core.security import decrypt_secret
        from src.models.enums import ConfigType as ConfigTypeEnum

        # Get all declared keys and their types from SolutionConfigSchema.
        schema_rows = (
            await self.db.execute(
                select(SolutionConfigSchema)
                .where(SolutionConfigSchema.solution_id == solution.id)
                .order_by(SolutionConfigSchema.position)
            )
        ).scalars().all()

        out: dict[str, str] = {}
        for schema in schema_rows:
            # Look up the Config value in the solution's org scope (no cascade
            # — we want the value actually set for this install's org, not a
            # global fallback that might belong to a different install).
            q = select(Config).where(
                Config.key == schema.key,
                Config.organization_id == solution.organization_id
                if solution.organization_id is not None
                else Config.organization_id.is_(None),
                Config.orphaned_at.is_(None),
            )
            config = (await self.db.execute(q)).scalar_one_or_none()
            if config is None:
                continue  # Not set — skip; install target must supply this key.

            # Extract the scalar from the JSONB envelope {"value": ...}.
            raw = (
                config.value.get("value")
                if isinstance(config.value, dict)
                else config.value
            )
            if raw is None:
                continue

            # Decrypt SECRET-typed values so the blob carries the plaintext.
            # SolutionConfigSchema.type is a plain str column; canonicalize case
            # (mirrors zip_install._config_type) so a stored "SECRET" still
            # matches and the secret is never exported still-encrypted.
            schema_type = schema.type
            is_secret = bool(schema_type) and schema_type.lower() == ConfigTypeEnum.SECRET.value
            if is_secret:
                raw = decrypt_secret(str(raw))

            out[schema.key] = str(raw)

        return out

    async def _table_data(self, solution: Solution) -> dict[str, list[dict[str, Any]]]:
        """Read each owned table's rows for a full-backup export.

        Only tables that have at least one row are included in the output dict
        (empty tables are omitted to keep the encrypted blob lean).

        Each row is represented as the JSONB ``data`` dict stored in the
        ``Document`` row.  The ``data`` field is already JSON-serializable
        (it came from JSON on write), so no coercion is needed here.

        Row cap: if a table exceeds TABLE_ROW_CAP rows a WARNING is logged
        naming the table and actual count, and only the first TABLE_ROW_CAP
        rows are returned.  This is never silent — callers can observe the
        warning in logs.
        """
        # Query owned tables directly — _table_entries returns serialized dicts,
        # but here we need ORM Table objects for their id + name.
        table_rows = (
            await self.db.execute(select(Table).where(Table.solution_id == solution.id))
        ).scalars().all()

        out: dict[str, list[dict[str, Any]]] = {}
        for tbl in table_rows:
            # Fetch all rows; apply the cap after counting so we can log accurately.
            docs = (
                await self.db.execute(
                    select(Document)
                    .where(Document.table_id == tbl.id)
                    .order_by(Document.created_at.asc(), Document.id)
                    .limit(TABLE_ROW_CAP + 1)
                )
            ).scalars().all()

            if not docs:
                # Empty table — omit the key (keep blob lean).
                continue

            if len(docs) > TABLE_ROW_CAP:
                # We fetched one extra to detect overflow; trim to the cap and warn.
                # A real row count may be even higher — the +1 trick only confirms
                # there are MORE than TABLE_ROW_CAP rows, not the exact total.
                logger.warning(
                    "bundle_for: table %r has more than %d rows; "
                    "only the first %d rows are included in the export bundle.",
                    tbl.name,
                    TABLE_ROW_CAP,
                    TABLE_ROW_CAP,
                )
                docs = docs[:TABLE_ROW_CAP]

            # Represent each row as its JSONB data dict — already JSON-serializable.
            out[tbl.name] = [doc.data for doc in docs]

        return out

    async def _solution_file_entries(
        self, solution: Solution
    ) -> list[Any]:
        """Read each solution-owned file's bytes for a full-backup export.

        Enumerates via the Task-17 service (metadata-only, no S3) then reads
        each file's bytes from S3. Only files that can be read are included.

        File cap: if a solution exceeds FILE_CAP files, a WARNING is logged
        naming the solution slug and actual count, and only the first FILE_CAP
        files are returned — no silent truncation.

        Empty → returns [] (omit from encrypted blob when empty).
        """
        from src.services.solution_files import (
            SolutionFileEntry,
            enumerate_solution_files,
            read_solution_file,
        )

        entries = await enumerate_solution_files(self.db, solution.id)
        if not entries:
            return []

        if len(entries) > FILE_CAP:
            logger.warning(
                "bundle_for: solution %r has more than %d files; "
                "only the first %d files are included in the export bundle.",
                solution.slug,
                FILE_CAP,
                FILE_CAP,
            )
            entries = entries[:FILE_CAP]

        result: list[SolutionFileEntry] = []
        for entry in entries:
            try:
                content_bytes = await read_solution_file(
                    self.db, solution.id, entry.location, entry.path
                )
            except Exception:
                # File disappeared between enumerate and read — skip rather than
                # fail the whole capture.
                logger.warning(
                    "bundle_for: could not read file %r/%r for solution %r; skipping.",
                    entry.location,
                    entry.path,
                    solution.slug,
                )
                continue
            result.append(
                SolutionFileEntry(
                    location=entry.location,
                    path=entry.path,
                    sha256=entry.sha256,
                    size=entry.size,
                    content_bytes=content_bytes,
                )
            )

        return result

    async def _python_files(
        self, workflows: list[dict[str, Any]], *, include_imports: bool = False
    ) -> dict[str, str]:
        """Collect the captured workflows' Python source.

        Default: only the captured workflows' OWN ``path`` files. ``modules/``
        is never blind-globbed — modules are often intentionally global, and a
        module nothing in the solution imports must never be bundled.

        ``include_imports=True``: additionally vendor the transitive import
        closure (only ``modules/`` files actually reached by following imports
        from the captured workflows, recursively). Uses the canonical scanner
        in ``solution_vendoring`` — same one ``bifrost deploy`` uses — so the
        two paths agree. The scan is STATIC (AST): dynamic imports
        (``importlib.import_module(var)``) are invisible, which is why the
        capture preview lets a human add a missed file manually (§3.3).
        """
        out: dict[str, str] = {}
        for path in sorted({str(w["path"]) for w in workflows}):
            try:
                out[path] = (await self.repo.read(path)).decode("utf-8")
            except Exception:
                # A workflow whose source isn't in _repo/ is one that was ALREADY
                # solution-deployed before this capture (its source lives under
                # _solutions/{id}/, e.g. the scaffold's sample workflow). Capture
                # adopts LOOSE _repo/ entities; an already-deployed one is part of
                # the install's deployed bundle already, so skip it here rather
                # than fail the whole capture (a capture into a non-empty install
                # would otherwise always 409 on the pre-existing workflows).
                continue

        if not include_imports:
            return out

        async def _repo_read(rel: str) -> str | None:
            try:
                return (await self.repo.read(rel)).decode("utf-8")
            except Exception:
                # Absent / non-text path → stdlib/third-party/typo; nothing to
                # vendor (the scanner treats None as "not a shared module").
                return None

        # `workflows` files are already in `out`; only `modules/` (and other
        # shared roots) get vendored. Excluding the `workflows` root keeps the
        # scanner from re-pulling sibling workflow files it already has.
        vendored = await vendor_shared_deps(
            dict(out),
            _repo_read,
            solution_local_roots=frozenset({"workflows"}),
        )
        out.update(vendored)
        return out

    async def _app_source_files(
        self, app: Application
    ) -> tuple[dict[str, str], dict[str, str]]:
        prefix = app.repo_prefix
        paths = await self.repo.list(prefix)
        ignore = _ignore_spec()
        src_files: dict[str, str] = {}
        bin_files: dict[str, str] = {}
        for path in sorted(paths):
            rel = path[len(prefix):]
            if not rel:
                continue
            # Skip build output, caches, editor turds, and SECRET files
            # (.env*) — same canonical skip-list the CLI applies on push/sync.
            # Capturing them would leak credentials and bloat the export.
            if ignore.match_file(rel):
                continue
            data = await self.repo.read(path)
            try:
                src_files[rel] = data.decode("utf-8")
            except UnicodeDecodeError:
                bin_files[rel] = base64.b64encode(data).decode("ascii")
        return src_files, bin_files

    async def _role_ids(self, junction: type, fk_col: str, entity_id: UUID) -> list[str]:
        return [
            str(v)
            for v in await self._junction_ids(junction, fk_col, "role_id", entity_id)
        ]

    async def _role_names(self, role_ids: list[str]) -> list[str]:
        """Resolve role UUIDs to names for portable, cross-env install.

        Bundles carry BOTH ``roles`` (origin UUIDs) and ``role_names``; the
        deployer prefers ``role_names`` (deploy.py:_resolve_roles) so a captured
        role-based entity binds the right role in a fresh env instead of
        FK-failing on an unknown UUID (Codex 6c — same fix workflows.yaml got).
        """
        if not role_ids:
            return []
        rows = (
            await self.db.execute(
                select(Role.id, Role.name).where(
                    Role.id.in_([UUID(r) for r in role_ids])
                )
            )
        ).all()
        by_id = {str(rid): name for rid, name in rows}
        # Preserve the role_ids order; drop ids with no surviving Role row.
        return [by_id[r] for r in role_ids if r in by_id]

    async def _junction_ids(
        self, junction: type, fk_col: str, value_col: str, entity_id: UUID
    ) -> list[UUID]:
        rows = (
            await self.db.execute(
                select(getattr(junction, value_col)).where(
                    getattr(junction, fk_col) == entity_id
                )
            )
        ).scalars().all()
        return list(rows)

    @staticmethod
    def _form_field_entry(field: FormField) -> dict[str, Any]:
        return _drop_none({
            "name": field.name,
            "label": field.label,
            "type": field.type,
            "required": field.required,
            "position": field.position,
            "placeholder": field.placeholder,
            "help_text": field.help_text,
            "default_value": field.default_value,
            "options": field.options,
            "data_provider_id": str(field.data_provider_id) if field.data_provider_id else None,
            "data_provider_inputs": field.data_provider_inputs,
            "visibility_expression": field.visibility_expression,
            "validation": field.validation,
            "allowed_types": field.allowed_types,
            "multiple": field.multiple,
            "max_size_mb": field.max_size_mb,
            "content": field.content,
            "allow_as_query_param": field.allow_as_query_param,
            "auto_fill": field.auto_fill,
        })
