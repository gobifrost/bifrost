"""
Manifest Import Service

Standalone module for importing .bifrost/ manifest files into the database.
Extracts entity resolution logic from GitHubSyncService into a reusable
ManifestResolver class and standalone import functions.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.services.repo_storage import RepoStorage
    from src.services.sync_ops import SyncOp

from bifrost.manifest import (
    Manifest,
    read_manifest_from_dir,
)

logger = logging.getLogger(__name__)

RoleResolution = Literal["uuid", "name"]


# =============================================================================
# Manifest diff (pure in-memory comparison)
# =============================================================================


def _diff_and_collect(
    incoming: "Manifest", current: "Manifest",
) -> tuple[list[dict[str, str]], set[str]]:
    """Single-pass manifest comparison returning both display changes and changed IDs.

    Returns:
        (changes, changed_ids) where:
        - changes: list of dicts with keys action, entity_type, name, organization
        - changed_ids: set of entity IDs that differ (includes integration-cascade)
    """
    # Build org ID → name lookup from both manifests
    org_lookup: dict[str, str] = {}
    for org in incoming.organizations:
        org_lookup[org.id] = org.name
    for org in current.organizations:
        org_lookup.setdefault(org.id, org.name)

    # Build integration ID → name lookup for config display
    integ_lookup: dict[str, str] = {}
    for integ in incoming.integrations.values():
        integ_lookup[integ.id] = integ.name
    for integ in current.integrations.values():
        integ_lookup.setdefault(integ.id, integ.name)

    def _resolve_org(entity: object) -> str:
        oid = getattr(entity, "organization_id", None)
        if not oid:
            return "Global"
        return org_lookup.get(oid, oid) or "Global"

    changes: list[dict[str, str]] = []
    changed_ids: set[str] = set()
    changed_integration_ids: set[str] = set()

    # -- List-based entities (organizations, roles) --
    _diff_list_entities(
        incoming.organizations, current.organizations,
        "organizations", org_lookup, changes, changed_ids,
    )
    _diff_list_entities(
        incoming.roles, current.roles,
        "roles", org_lookup, changes, changed_ids,
    )

    # -- Dict-based entities --
    _DICT_ENTITY_TYPES: list[tuple[str, str]] = [
        ("workflows", "workflows"),
        ("integrations", "integrations"),
        ("configs", "configs"),
        ("tables", "tables"),
        ("events", "events"),
        ("forms", "forms"),
        ("agents", "agents"),
        ("apps", "apps"),
    ]

    for attr, entity_type in _DICT_ENTITY_TYPES:
        incoming_dict: dict = getattr(incoming, attr)
        current_dict: dict = getattr(current, attr)

        # Index by entity .id
        incoming_by_id = {v.id: v for v in incoming_dict.values()}
        current_by_id = {v.id: v for v in current_dict.values()}

        all_ids = set(incoming_by_id) | set(current_by_id)
        for eid in all_ids:
            inc = incoming_by_id.get(eid)
            cur = current_by_id.get(eid)

            if inc and not cur:
                action = "add"
                entity = inc
            elif cur and not inc:
                action = "delete"
                entity = cur
            else:
                assert inc is not None and cur is not None
                # Compare serialized form
                if inc.model_dump(mode="json", by_alias=True) == cur.model_dump(mode="json", by_alias=True):
                    continue  # No change
                action = "update"
                entity = inc

            changed_ids.add(eid)
            if attr == "integrations":
                changed_integration_ids.add(eid)

            # Resolve display name
            if entity_type == "configs":
                name = getattr(entity, "key", "") or str(eid)
                iid = getattr(entity, "integration_id", None)
                if iid and iid in integ_lookup:
                    name = f"{integ_lookup[iid]}/{name}"
            else:
                name = getattr(entity, "name", None) or getattr(entity, "function_name", None) or str(eid)

            changes.append({
                "id": eid,
                "action": action,
                "entity_type": entity_type,
                "name": name,
                "organization": _resolve_org(entity),
            })

    # When an integration changes, include all its dependent configs
    if changed_integration_ids:
        for mcfg in incoming.configs.values():
            if mcfg.integration_id in changed_integration_ids:
                changed_ids.add(mcfg.id)

    # Sort: entity_type, then action priority (add > update > delete), then name
    _ACTION_ORDER = {"add": 0, "update": 1, "delete": 2, "keep": 3}
    changes.sort(key=lambda c: (c["entity_type"], _ACTION_ORDER.get(c["action"], 9), c["name"]))

    return changes, changed_ids


def _diff_list_entities(
    incoming_list: list,
    current_list: list,
    entity_type: str,
    org_lookup: dict[str, str],
    changes: list[dict[str, str]],
    changed_ids: set[str] | None = None,
) -> None:
    """Diff list-based manifest entities (organizations, roles)."""
    incoming_by_id = {e.id: e for e in incoming_list}
    current_by_id = {e.id: e for e in current_list}

    for eid in set(incoming_by_id) | set(current_by_id):
        inc = incoming_by_id.get(eid)
        cur = current_by_id.get(eid)

        if inc and not cur:
            action = "add"
            entity = inc
        elif cur and not inc:
            action = "delete"
            entity = cur
        else:
            assert inc is not None and cur is not None
            if inc.model_dump(mode="json", by_alias=True) == cur.model_dump(mode="json", by_alias=True):
                continue
            action = "update"
            entity = inc

        if changed_ids is not None:
            changed_ids.add(eid)

        oid = getattr(entity, "organization_id", None)
        org = (org_lookup.get(oid, oid) or "Global") if oid else "Global"

        changes.append({
            "id": eid,
            "action": action,
            "entity_type": entity_type,
            "name": getattr(entity, "name", "") or str(eid),
            "organization": org,
        })


def _diff_manifests(incoming: "Manifest", current: "Manifest") -> list[dict[str, str]]:
    """Compare two Manifest objects and return entity-level changes."""
    changes, _ = _diff_and_collect(incoming, current)
    return changes


def _collect_changed_ids(incoming: "Manifest", current: "Manifest") -> set[str]:
    """Return the set of entity IDs that differ between two manifests."""
    _, ids = _diff_and_collect(incoming, current)
    return ids


# =============================================================================
# Inline-content helpers
# =============================================================================
#
# Form/agent content is now inlined under each entity's UUID in the manifest
# (see ManifestForm/ManifestAgent in api/bifrost/manifest.py). The indexers
# (FormIndexer, AgentIndexer) still parse YAML, so we synthesize the YAML
# bytes from the manifest entry to keep the indexer interface stable.
#
# Back-compat: if a manifest entry doesn't carry inline content but a
# companion .form.yaml / .agent.yaml exists, we still read it and emit a
# deprecation warning. This branch will be removed once all checked-in
# manifests have been regenerated.


_DEPRECATION_MSG_TEMPLATE = (
    "{kind} content in separate file is deprecated; "
    "regenerate with 'bifrost sync' to inline (path={path})"
)


def _form_has_inline_content(mform) -> bool:
    """Return True if the manifest form entry carries inline content."""
    return any(
        getattr(mform, attr, None) is not None
        for attr in (
            "description",
            "workflow_id",
            "launch_workflow_id",
            "default_launch_params",
            "allowed_query_params",
            "form_schema",
        )
    )


def _agent_has_inline_content(magent) -> bool:
    """Return True if the manifest agent entry carries inline content.

    ``system_prompt`` is required in the DB so its presence is the strongest
    signal that this entry was generated under the inline layout.
    """
    if getattr(magent, "system_prompt", None):
        return True
    return any(
        bool(getattr(magent, attr, None))
        for attr in (
            "description",
            "channels",
            "tool_ids",
            "delegated_agent_ids",
            "knowledge_sources",
            "system_tools",
            "llm_model",
            "llm_max_tokens",
        )
    )


def _form_content_from_manifest(mform) -> bytes:
    """Build the YAML bytes the FormIndexer expects from a manifest form entry."""
    data: dict = {"id": mform.id, "name": mform.name or ""}
    if mform.description is not None:
        data["description"] = mform.description
    if mform.workflow_id is not None:
        data["workflow_id"] = mform.workflow_id
    if mform.launch_workflow_id is not None:
        data["launch_workflow_id"] = mform.launch_workflow_id
    if mform.default_launch_params is not None:
        data["default_launch_params"] = mform.default_launch_params
    if mform.allowed_query_params is not None:
        data["allowed_query_params"] = mform.allowed_query_params
    if mform.form_schema is not None:
        data["form_schema"] = mform.form_schema
    return (yaml.dump(data, default_flow_style=False, sort_keys=True).rstrip() + "\n").encode("utf-8")


def _agent_content_from_manifest(magent) -> bytes:
    """Build the YAML bytes the AgentIndexer expects from a manifest agent entry."""
    data: dict = {"id": magent.id, "name": magent.name or ""}
    if magent.description is not None:
        data["description"] = magent.description
    if magent.system_prompt is not None:
        data["system_prompt"] = magent.system_prompt
    if magent.channels:
        data["channels"] = list(magent.channels)
    if magent.tool_ids:
        data["tool_ids"] = list(magent.tool_ids)
    if magent.delegated_agent_ids:
        data["delegated_agent_ids"] = list(magent.delegated_agent_ids)
    if magent.knowledge_sources:
        data["knowledge_sources"] = list(magent.knowledge_sources)
    if magent.system_tools:
        data["system_tools"] = list(magent.system_tools)
    if magent.llm_model is not None:
        data["llm_model"] = magent.llm_model
    if magent.llm_max_tokens is not None:
        data["llm_max_tokens"] = magent.llm_max_tokens
    if magent.max_iterations is not None:
        data["max_iterations"] = magent.max_iterations
    if magent.max_token_budget is not None:
        data["max_token_budget"] = magent.max_token_budget
    return (yaml.dump(data, default_flow_style=False, sort_keys=True).rstrip() + "\n").encode("utf-8")


async def _resolve_form_content(
    mform,
    read_fn: "Callable[[str], Awaitable[bytes | None]]",
) -> bytes | None:
    """Return YAML bytes for a manifest form entry.

    Prefers inline content; falls back to ``mform.path`` companion file with a
    deprecation warning (back-compat for manifests written before the inline
    rollout). Returns ``None`` if neither source is available.
    """
    if _form_has_inline_content(mform):
        return _form_content_from_manifest(mform)
    if mform.path:
        content = await read_fn(mform.path)
        if content is not None:
            logger.warning(_DEPRECATION_MSG_TEMPLATE.format(kind="Form", path=mform.path))
            return content
    return None


async def _resolve_agent_content(
    magent,
    read_fn: "Callable[[str], Awaitable[bytes | None]]",
) -> bytes | None:
    """Return YAML bytes for a manifest agent entry.

    Prefers inline content; falls back to ``magent.path`` companion file with a
    deprecation warning. Returns ``None`` if neither source is available.
    """
    if _agent_has_inline_content(magent):
        return _agent_content_from_manifest(magent)
    if magent.path:
        content = await read_fn(magent.path)
        if content is not None:
            logger.warning(_DEPRECATION_MSG_TEMPLATE.format(kind="Agent", path=magent.path))
            return content
    return None


# =============================================================================
# Cross-environment rebinding helpers
# =============================================================================


async def _resolve_role_names(db: AsyncSession, names: list[str]) -> list[str]:
    """Resolve role display names to UUID strings against the target DB.

    Fails loud on any unknown name. Returned list preserves input order.
    """
    from src.models.orm.users import Role

    if not names:
        return []
    result = await db.execute(select(Role.id, Role.name).where(Role.name.in_(list(set(names)))))
    by_name: dict[str, str] = {row[1]: str(row[0]) for row in result.all()}
    resolved: list[str] = []
    for name in names:
        role_id = by_name.get(name)
        if role_id is None:
            raise ValueError(f"unknown role: {name} — create it first in the target env.")
        resolved.append(role_id)
    return resolved


async def _apply_role_name_resolution(db: AsyncSession, manifest: "Manifest") -> "Manifest":
    """Return a copy of the manifest with ``role_names`` → ``roles`` resolved.

    Entities affected: workflows, forms, agents, apps. If an entity carries
    both ``role_names`` (new) and ``roles`` (legacy), ``role_names`` wins
    when ``role_resolution='name'``.  Missing names raise ``ValueError``.
    """
    def _copy_with_resolved(entity, resolved: list[str]):
        return entity.model_copy(update={"roles": resolved, "role_names": None})

    # Workflows
    new_workflows: dict[str, object] = {}
    for key, mwf in manifest.workflows.items():
        if mwf.role_names is not None:
            resolved = await _resolve_role_names(db, list(mwf.role_names))
            new_workflows[key] = _copy_with_resolved(mwf, resolved)
        else:
            new_workflows[key] = mwf

    # Forms
    new_forms: dict[str, object] = {}
    for key, mform in manifest.forms.items():
        if mform.role_names is not None:
            resolved = await _resolve_role_names(db, list(mform.role_names))
            new_forms[key] = _copy_with_resolved(mform, resolved)
        else:
            new_forms[key] = mform

    # Agents
    new_agents: dict[str, object] = {}
    for key, magent in manifest.agents.items():
        if magent.role_names is not None:
            resolved = await _resolve_role_names(db, list(magent.role_names))
            new_agents[key] = _copy_with_resolved(magent, resolved)
        else:
            new_agents[key] = magent

    # Apps
    new_apps: dict[str, object] = {}
    for key, mapp in manifest.apps.items():
        if mapp.role_names is not None:
            resolved = await _resolve_role_names(db, list(mapp.role_names))
            new_apps[key] = _copy_with_resolved(mapp, resolved)
        else:
            new_apps[key] = mapp

    return manifest.model_copy(update={
        "workflows": new_workflows,
        "forms": new_forms,
        "agents": new_agents,
        "apps": new_apps,
    })


def _rewrite_org_ids(manifest: "Manifest", target_organization_id: UUID) -> "Manifest":
    """Return a copy of the manifest with every entity's ``organization_id``
    rewritten to ``target_organization_id``.

    Does NOT touch ``manifest.organizations`` (the bundle-level org list); the
    caller guards against that combination and rejects before calling here.
    """
    target = str(target_organization_id)

    def _with_org(entity):
        return entity.model_copy(update={"organization_id": target})

    new_workflows = {k: _with_org(v) for k, v in manifest.workflows.items()}
    new_forms = {k: _with_org(v) for k, v in manifest.forms.items()}
    new_agents = {k: _with_org(v) for k, v in manifest.agents.items()}
    new_apps = {k: _with_org(v) for k, v in manifest.apps.items()}
    new_configs = {k: _with_org(v) for k, v in manifest.configs.items()}
    new_tables = {k: _with_org(v) for k, v in manifest.tables.items()}
    new_events = {k: _with_org(v) for k, v in manifest.events.items()}

    # Integrations have per-mapping org_id; rewrite each mapping.
    new_integrations: dict[str, object] = {}
    for k, minteg in manifest.integrations.items():
        new_mappings = [m.model_copy(update={"organization_id": target}) for m in minteg.mappings]
        new_integrations[k] = minteg.model_copy(update={"mappings": new_mappings})

    return manifest.model_copy(update={
        "workflows": new_workflows,
        "forms": new_forms,
        "agents": new_agents,
        "apps": new_apps,
        "configs": new_configs,
        "tables": new_tables,
        "events": new_events,
        "integrations": new_integrations,
    })


# =============================================================================
# Standalone manifest import (no git, reads from S3)
# =============================================================================

@dataclass
class ManifestImportResult:
    """Result of importing manifest from repo."""
    applied: bool = False
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)
    manifest_files: dict[str, str] = field(default_factory=dict)
    modified_files: dict[str, str] = field(default_factory=dict)
    deleted_entities: list[str] = field(default_factory=list)
    entity_changes: list[dict[str, str]] = field(default_factory=list)


async def import_manifest_from_repo(
    db: AsyncSession,
    delete_removed_entities: bool = False,
    dry_run: bool = False,
    target_organization_id: UUID | None = None,
    role_resolution: RoleResolution = "uuid",
    entity_ids: set[str] | None = None,
) -> ManifestImportResult:
    """Import manifest from S3 _repo/.bifrost/ into DB.

    Standalone function (not a method on GitHubSyncService) that:
    1. Reads .bifrost/*.yaml from S3 via RepoStorage
    2. Parses with parse_manifest_dir()
    3. Validates with validate_manifest()
    4. Resolves entities to DB using ManifestResolver.plan_import
    5. Runs indexer side-effects for forms/agents
    6. Regenerates manifest from DB
    7. Returns ManifestImportResult

    Cross-environment rebinding:
    - ``target_organization_id``: when set, every entity in the bundle is
      rewritten to belong to this organization before upsert. Applies to
      forms, agents, workflows, apps, integrations, configs, tables, event
      sources, and integration mappings. Does NOT apply to organizations
      themselves — if the bundle carries an ``organizations`` section and
      this override is set, the import is rejected with a ``ValueError``
      surfaced as HTTP 422 at the router level.
    - ``role_resolution``: ``"uuid"`` (default) assumes role UUIDs in the
      bundle match the target environment. ``"name"`` reads ``role_names``
      from each entity and resolves them to UUIDs in the target DB; missing
      names raise ``ValueError`` before any DB writes.
    """
    from src.services.repo_storage import RepoStorage
    from bifrost.manifest import (
        MANIFEST_FILES,
        filter_manifest_by_ids,
        parse_manifest_dir,
        serialize_manifest_dir,
        validate_manifest,
    )
    from src.services.manifest_generator import generate_manifest

    result = ManifestImportResult()
    repo = RepoStorage()

    # 1. Read .bifrost/*.yaml from S3
    manifest_yaml_files: dict[str, str] = {}
    for _entity_type, filename in MANIFEST_FILES.items():
        s3_path = f".bifrost/{filename}"
        try:
            content = await repo.read(s3_path)
            manifest_yaml_files[filename] = content.decode("utf-8")
        except Exception:
            pass  # File doesn't exist in S3, skip

    if not manifest_yaml_files:
        result.warnings.append("No .bifrost/ manifest files found in repo")
        return result

    # 2. Parse
    try:
        manifest = parse_manifest_dir(manifest_yaml_files)
    except Exception as e:
        result.warnings.append(f"Failed to parse manifest: {e}")
        return result

    # 3. Validate (warnings only — DB FK constraints are the real safety net)
    validation_errors = validate_manifest(manifest)
    if validation_errors:
        result.warnings.extend(validation_errors)

    # 3a. Cross-env guard: orgs section incompatible with target_organization_id
    if target_organization_id is not None and manifest.organizations:
        raise ValueError(
            "cannot carry organizations section when target_organization_id is set — "
            "drop the orgs section or remove the target."
        )

    # 3b. Pre-resolve role names when requested. Fails loud before any DB writes.
    if role_resolution == "name":
        manifest = await _apply_role_name_resolution(db, manifest)

    # 3c. Rewrite organization_id on every entity when override is set.
    # Does NOT touch manifest.organizations (guarded above).
    if target_organization_id is not None:
        manifest = _rewrite_org_ids(manifest, target_organization_id)

    # 4. Compute diff against current DB state
    db_manifest = await generate_manifest(db)
    entity_changes, changed_ids = _diff_and_collect(manifest, db_manifest)

    # 4a. Dry-run: return diff without writing
    if dry_run:
        result.entity_changes = entity_changes
        result.dry_run = True
        return result

    # 4b. Caller-supplied subset filter (e.g. interactive import TUI). Restrict
    # the write to the user's selection, and trim entity_changes to match so
    # the response accurately reflects what was applied.
    if entity_ids is not None:
        changed_ids &= entity_ids
        entity_changes = [c for c in entity_changes if c.get("id") in changed_ids]

    # 4c. Short-circuit: nothing changed — no write-back needed
    if not changed_ids:
        result.applied = True
        result.entity_changes = entity_changes  # empty list
        return result

    # Check if diff has any deletes (to skip _resolve_deletions later)
    has_deletes = any(c["action"] == "delete" for c in entity_changes)

    # Helper: read a file from S3, returning None on failure
    async def _read_or_none(path: str) -> bytes | None:
        try:
            return await repo.read(path)
        except Exception:
            return None

    # 5. Run entity resolution via direct S3 reads (no temp dir needed)
    resolver = ManifestResolver(db)

    try:
        async with db.begin_nested():
            # Delete stale entities FIRST to avoid unique constraint violations
            # (e.g. workflow name collision when a workflow moves paths/gets new UUID)
            deletion_changes = []
            if delete_removed_entities and has_deletes:
                deletion_changes = await resolver._resolve_deletions(
                    manifest=manifest, repo=repo, dry_run=False,
                )

            await resolver.plan_import(manifest, repo=repo, dry_run=False, changed_ids=changed_ids)

            # Use diff-computed entity_changes (more accurate than op-based)
            result.entity_changes = [c for c in entity_changes if c["action"] != "delete"]

            # Append deletion results after the entity_changes reset above
            for ec in deletion_changes:
                result.deleted_entities.append(
                    f"{ec.entity_type}: {ec.name}"
                )
                result.entity_changes.append({
                    "action": "delete" if ec.action == "removed" else ec.action,
                    "entity_type": ec.entity_type,
                    "name": ec.name,
                })

            # Run indexer side-effects (workflows, forms, and agents)
            await resolver._index_workflows_from_manifest(manifest, _read_or_none, changed_ids)
            result.modified_files.update(
                await resolver._index_forms_from_manifest(manifest, _read_or_none, changed_ids)
            )
            result.modified_files.update(
                await resolver._index_agents_from_manifest(manifest, _read_or_none, changed_ids)
            )

            result.applied = True

    except IntegrityError as e:
        detail = str(e.orig) if e.orig else str(e)
        if "foreign key" in detail.lower():
            result.warnings.append(
                "Entity resolution failed: a referenced entity could not be "
                "deleted because other entities still depend on it. "
                "This is usually resolved by syncing again."
            )
        else:
            result.warnings.append(f"Entity resolution failed (database constraint): {detail}")
        logger.warning(f"Manifest import entity resolution failed: {e}", exc_info=True)
    except Exception as e:
        result.warnings.append(f"Entity resolution failed: {e}")
        logger.warning(f"Manifest import entity resolution failed: {e}", exc_info=True)

    # 6b. Refresh MCP tool registry so new/changed tools appear immediately
    if result.applied and not dry_run:
        try:
            from src.services.mcp_server.server import refresh_workflow_tools
            await refresh_workflow_tools()
        except Exception as e:
            logger.warning(f"Failed to refresh MCP workflow tools after manifest import: {e}")

    # 7. Regenerate manifest from DB (partial: only changed entities).
    # NOTE: control reaches this point only when ``changed_ids`` is truthy —
    # the empty-changes case returns at step 4c (around line 596) before
    # entity resolution runs. The partial filter is therefore always applied.
    try:
        new_manifest = await generate_manifest(db)
        partial = filter_manifest_by_ids(new_manifest, changed_ids)
        result.manifest_files = serialize_manifest_dir(partial)
    except Exception as e:
        result.warnings.append(f"Manifest regeneration failed: {e}")

    return result


# =============================================================================
# Manifest Resolver
# =============================================================================


class ManifestResolver:
    """Resolves manifest entities into database operations.

    Extracted from GitHubSyncService to allow standalone manifest import
    without git dependencies. Only requires a database session.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _prefetch_existing_entities(self) -> dict:
        """Prefetch all existing entity IDs/natural-keys in bulk queries.

        Returns a cache dict that _resolve_* methods use for O(1) lookups
        instead of per-entity SELECT queries.

        Cache invariant
        ---------------
        All caches are keyed on parent UUIDs **as of prefetch time**. When a
        resolver rewrites an entity's primary key in the same transaction
        (cross-env id sync via the existing-by-name path), Postgres FK ON
        UPDATE CASCADE migrates dependents to the new id, but the cache is
        still keyed on the old id. The resolver responsible for the rewrite
        MUST refresh / rekey any dependent caches before subsequent reads —
        otherwise downstream lookups miss and fall through to INSERT, which
        collides with the cascade-migrated rows. See `_resolve_integration`
        for the canonical pattern and issue #148 for the original incident.
        """
        from src.models.orm.applications import Application
        from src.models.orm.config import Config
        from src.models.orm.integrations import (
            Integration,
            IntegrationConfigSchema,
            IntegrationMapping,
        )
        from src.models.orm.organizations import Organization
        from src.models.orm.tables import Table
        from src.models.orm.users import Role
        from src.models.orm.workflows import Workflow

        cache: dict = {}

        # Organizations: {id} set + {name: id} dict
        org_result = await self.db.execute(select(Organization.id, Organization.name))
        cache["org_ids"] = set()
        cache["org_by_name"] = {}
        for row in org_result.all():
            cache["org_ids"].add(row[0])
            cache["org_by_name"][row[1]] = row[0]

        # Roles: {id} set + {name: id} dict
        role_result = await self.db.execute(select(Role.id, Role.name))
        cache["role_ids"] = set()
        cache["role_by_name"] = {}
        for row in role_result.all():
            cache["role_ids"].add(row[0])
            cache["role_by_name"][row[1]] = row[0]

        # Workflows: {(path, function_name): id} + {id} set
        wf_result = await self.db.execute(
            select(Workflow.id, Workflow.path, Workflow.function_name)
        )
        cache["wf_ids"] = set()
        cache["wf_by_natural"] = {}
        for row in wf_result.all():
            cache["wf_ids"].add(row[0])
            if row[1] and row[2]:
                cache["wf_by_natural"][(row[1], row[2])] = row[0]

        # Integrations: {name: id} + {id} set
        integ_result = await self.db.execute(select(Integration.id, Integration.name))
        cache["integ_ids"] = set()
        cache["integ_by_name"] = {}
        for row in integ_result.all():
            cache["integ_ids"].add(row[0])
            cache["integ_by_name"][row[1]] = row[0]

        # IntegrationConfigSchema: {integ_id: {key: schema_obj}}
        cs_result = await self.db.execute(select(IntegrationConfigSchema))
        cache["integ_cs"] = {}
        for cs in cs_result.scalars().all():
            cache["integ_cs"].setdefault(cs.integration_id, {})[cs.key] = cs

        # IntegrationMapping: {integ_id: {org_id_str: mapping_obj}}
        im_result = await self.db.execute(select(IntegrationMapping))
        cache["integ_mappings"] = {}
        for m in im_result.scalars().all():
            org_key = str(m.organization_id) if m.organization_id else None
            cache["integ_mappings"].setdefault(m.integration_id, {})[org_key] = m

        # Apps: {slug: id}
        app_result = await self.db.execute(select(Application.id, Application.slug))
        cache["app_by_slug"] = {}
        for row in app_result.all():
            cache["app_by_slug"][row[1]] = row[0]

        # Tables: {(name, org_id): id} + {id} set
        table_result = await self.db.execute(
            select(Table.id, Table.name, Table.organization_id)
        )
        cache["table_ids"] = set()
        cache["table_by_natural"] = {}
        for row in table_result.all():
            cache["table_ids"].add(row[0])
            cache["table_by_natural"][(row[1], row[2])] = row[0]

        # Configs: {(key, integ_id, org_id): (id, value, config_schema_id)}
        cfg_result = await self.db.execute(
            select(Config.id, Config.key, Config.integration_id, Config.organization_id, Config.value, Config.config_schema_id)
        )
        cache["config_by_natural"] = {}
        for row in cfg_result.all():
            cache["config_by_natural"][(row[1], row[2], row[3])] = (row[0], row[4], row[5])

        return cache

    async def plan_import(self, manifest: "Manifest", work_dir: Path | None = None, progress_fn=None, repo: "RepoStorage | None" = None, dry_run: bool = False, changed_ids: set[str] | None = None) -> "list[SyncOp]":
        """Build and execute SyncOps for importing a manifest (entities only).

        Resolves and immediately executes ops in dependency order.
        Uses prefetch cache to minimize per-entity DB lookups.
        Deletions are handled separately by _delete_removed_entities / _resolve_deletions.
        Indexer side-effects (WorkflowIndexer, FormIndexer, AgentIndexer) remain
        in _import_all_entities.

        File reads use either ``repo`` (direct S3 via RepoStorage) or
        ``work_dir`` (local filesystem).  At least one must be provided for
        entities that reference source files (workflows, forms, agents).

        Import order:
        0a. Organizations (no deps)
        0b. Roles (no deps)
        1.  Workflows (refs org_id)
        2.  Integrations (refs workflow UUIDs for data_provider)
        3.  Configs (refs integration + org UUIDs)
        4.  Apps (refs org UUIDs)
        5.  Tables (refs org + app UUIDs)
        6.  Event Sources + Subscriptions (refs integration + workflow UUIDs)
        7.  Forms (refs workflow + org UUIDs) — metadata only
        8.  Agents (refs workflow + org UUIDs) — metadata only

        Returns the collected ops for callers that want to inspect them
        (e.g. for entity change tracking or dry-run analysis).
        """
        from src.services.sync_ops import SyncOp, Upsert  # noqa: F401

        if not work_dir and not repo:
            raise ValueError("plan_import requires either work_dir or repo")

        all_ops: list[SyncOp] = []

        # Helpers: abstract file reads over repo (S3) or work_dir (filesystem)
        async def _file_exists(path: str) -> bool:
            if repo:
                return await repo.exists(path)
            elif work_dir:
                return (work_dir / path).exists()
            return False

        async def _file_read(path: str) -> bytes | None:
            if repo:
                try:
                    return await repo.read(path)
                except Exception:
                    return None
            elif work_dir:
                p = work_dir / path
                if p.exists():
                    return p.read_bytes()
            return None

        # Count total entities for progress tracking
        if changed_ids is not None:
            total = sum(1 for morg in manifest.organizations if morg.id in changed_ids)
            total += sum(1 for mrole in manifest.roles if mrole.id in changed_ids)
            total += sum(1 for mwf in manifest.workflows.values() if mwf.id in changed_ids)
            total += sum(1 for minteg in manifest.integrations.values() if minteg.id in changed_ids)
            total += sum(1 for mcfg in manifest.configs.values() if mcfg.id in changed_ids)
            total += sum(1 for mapp in manifest.apps.values() if mapp.id in changed_ids)
            total += sum(1 for mtable in manifest.tables.values() if mtable.id in changed_ids)
            total += sum(1 for mes in manifest.events.values() if mes.id in changed_ids)
            total += sum(1 for mform in manifest.forms.values() if mform.id in changed_ids)
            total += sum(1 for magent in manifest.agents.values() if magent.id in changed_ids)
        else:
            total = (len(manifest.organizations) + len(manifest.roles)
                     + len(manifest.workflows) + len(manifest.integrations)
                     + len(manifest.configs) + len(manifest.apps)
                     + len(manifest.tables) + len(manifest.events)
                     + len(manifest.forms) + len(manifest.agents))
        current = 0

        async def _prog(msg: str) -> None:
            nonlocal current
            current += 1
            if progress_fn:
                await progress_fn(msg, current, total)

        # Prefetch all existing entities for O(1) lookups
        cache = await self._prefetch_existing_entities()

        # 0a. Resolve organizations (no deps) — execute immediately
        org_ops: list[SyncOp] = []
        for morg in manifest.organizations:
            if changed_ids is not None and morg.id not in changed_ids:
                continue
            await _prog(f"Importing organization: {morg.name}")
            org_ops.extend(self._resolve_organization(morg, cache))
        for op in org_ops:
            if dry_run:
                if isinstance(op, Upsert):
                    op.action_taken = "updated" if op.id in cache.get("org_ids", set()) else "inserted"
            else:
                await op.execute(self.db)
        all_ops.extend(org_ops)

        # 0b. Resolve roles (no deps) — execute immediately
        role_ops: list[SyncOp] = []
        for mrole in manifest.roles:
            if changed_ids is not None and mrole.id not in changed_ids:
                continue
            await _prog(f"Importing role: {mrole.name}")
            role_ops.extend(self._resolve_role(mrole, cache))
        for op in role_ops:
            if dry_run:
                if isinstance(op, Upsert):
                    op.action_taken = "updated" if op.id in cache.get("role_ids", set()) else "inserted"
            else:
                await op.execute(self.db)
        all_ops.extend(role_ops)

        # 1. Resolve workflows — execute immediately
        # Track which workflow IDs were actually imported (file exists in repo/disk)
        imported_wf_ids: set[str] = set()
        for key, mwf in manifest.workflows.items():
            if changed_ids is not None and mwf.id not in changed_ids:
                imported_wf_ids.add(mwf.id)  # Still track as present for event source refs
                continue
            if await _file_exists(mwf.path):
                await _prog(f"Importing workflow: {mwf.name or key}")
                wf_ops = self._resolve_workflow(mwf.name or key, mwf, cache)
                for op in wf_ops:
                    if dry_run:
                        if isinstance(op, Upsert):
                            op.action_taken = "updated" if op.id in cache.get("wf_ids", set()) else "inserted"
                    else:
                        await op.execute(self.db)
                all_ops.extend(wf_ops)
                imported_wf_ids.add(mwf.id)

        # 2. Resolve integrations (with config_schema, oauth_provider, mappings)
        for key, minteg in manifest.integrations.items():
            if changed_ids is not None and minteg.id not in changed_ids:
                continue
            await _prog(f"Importing integration: {minteg.name or key}")
            integ_ops = await self._resolve_integration(minteg.name or key, minteg, cache)
            for op in integ_ops:
                if dry_run:
                    if isinstance(op, Upsert):
                        op.action_taken = "updated" if op.id in cache.get("integ_ids", set()) else "inserted"
                else:
                    await op.execute(self.db)
            all_ops.extend(integ_ops)

        # 3. Resolve configs
        _config_id_set = {v[0] for v in cache.get("config_by_natural", {}).values()}
        for _config_key, mcfg in manifest.configs.items():
            if changed_ids is not None and mcfg.id not in changed_ids:
                continue
            cfg_ops = self._resolve_config(mcfg, cache)
            for op in cfg_ops:
                if dry_run:
                    if isinstance(op, Upsert):
                        op.action_taken = "updated" if op.id in _config_id_set else "inserted"
                else:
                    await op.execute(self.db)
            all_ops.extend(cfg_ops)

        # 4. Resolve apps (before tables — tables ref application_id)
        _app_id_set = set(cache.get("app_by_slug", {}).values())
        for _app_name, mapp in manifest.apps.items():
            if changed_ids is not None and mapp.id not in changed_ids:
                continue
            await _prog(f"Importing app: {mapp.name}")
            app_ops = self._resolve_app(mapp, cache)
            for op in app_ops:
                if dry_run:
                    if isinstance(op, Upsert):
                        op.action_taken = "updated" if op.id in _app_id_set else "inserted"
                else:
                    await op.execute(self.db)
            all_ops.extend(app_ops)

            # Compile source files from _repo/ into _apps/{id}/preview/
            if not dry_run:
                try:
                    from src.services.app_storage import AppStorageService

                    _synced, errors = await AppStorageService().sync_preview_compiled(
                        mapp.id, mapp.path,
                    )
                    if errors:
                        logger.warning(f"App {mapp.name} compile warnings: {errors}")
                except Exception as e:
                    logger.warning(f"Preview sync failed for app {mapp.name}: {e}")

        # 5. Resolve tables (refs org + app UUIDs)
        for key, mtable in manifest.tables.items():
            if changed_ids is not None and mtable.id not in changed_ids:
                continue
            await _prog(f"Importing table: {mtable.name or key}")
            table_ops = await self._resolve_table(mtable.name or key, mtable, cache)
            for op in table_ops:
                if dry_run:
                    if isinstance(op, Upsert):
                        op.action_taken = "updated" if op.id in cache.get("table_ids", set()) else "inserted"
                else:
                    await op.execute(self.db)
            all_ops.extend(table_ops)

        # 6. Resolve event sources + subscriptions
        for key, mes in manifest.events.items():
            if changed_ids is not None and mes.id not in changed_ids:
                continue
            await _prog(f"Importing event source: {mes.name or key}")
            es_ops = await self._resolve_event_source(mes.name or key, mes, imported_wf_ids)
            for op in es_ops:
                if dry_run:
                    if isinstance(op, Upsert):
                        op.action_taken = "inserted"  # no ES cache; assume new
                else:
                    await op.execute(self.db)
            all_ops.extend(es_ops)

        # 7. Resolve forms (metadata ops only — indexer called in _import_all_entities)
        for _form_name, mform in manifest.forms.items():
            if changed_ids is not None and mform.id not in changed_ids:
                continue
            content = await _resolve_form_content(mform, _file_read)
            if content is not None:
                await _prog(f"Importing form: {mform.name}")
                form_ops = self._resolve_form(mform, content)
                for op in form_ops:
                    if dry_run:
                        if isinstance(op, Upsert):
                            op.action_taken = "inserted"  # no form cache; assume new
                    else:
                        await op.execute(self.db)
                all_ops.extend(form_ops)

        # 8. Resolve agents (metadata ops only — indexer called in _import_all_entities)
        for _agent_name, magent in manifest.agents.items():
            if changed_ids is not None and magent.id not in changed_ids:
                continue
            content = await _resolve_agent_content(magent, _file_read)
            if content is not None:
                await _prog(f"Importing agent: {magent.name}")
                agent_ops = self._resolve_agent(magent, content)
                for op in agent_ops:
                    if dry_run:
                        if isinstance(op, Upsert):
                            op.action_taken = "inserted"  # no agent cache; assume new
                    else:
                        await op.execute(self.db)
                all_ops.extend(agent_ops)

        return all_ops

    async def _index_forms_from_manifest(
        self,
        manifest: "Manifest",
        read_fn: "Callable[[str], Awaitable[bytes | None]]",
        changed_ids: "set[str] | None" = None,
    ) -> dict[str, str]:
        """Run FormIndexer for each form in the manifest.

        Args:
            manifest: Parsed manifest with form entries
            read_fn: Async callable that reads a file path, returning bytes or None
            changed_ids: If set, only process forms whose ID is in this set

        Returns:
            Dict of {path: modified_content} for forms whose data changed during ref resolution.
        """
        from sqlalchemy import update as sa_update
        from src.models.orm.forms import Form
        from src.services.file_storage.indexers.form import FormIndexer

        form_indexer = FormIndexer(self.db)
        modified: dict[str, str] = {}

        for _form_name, mform in manifest.forms.items():
            if changed_ids is not None and mform.id not in changed_ids:
                continue
            content_bytes = await _resolve_form_content(mform, read_fn)
            if content_bytes is None:
                continue
            original_data = yaml.safe_load(content_bytes.decode("utf-8"))
            if not original_data:
                continue
            data = dict(original_data)
            data["id"] = mform.id
            await self._resolve_ref_field(data, "workflow_id")
            await self._resolve_ref_field(data, "launch_workflow_id")
            updated_content = (yaml.dump(data, default_flow_style=False, sort_keys=True).rstrip() + "\n").encode("utf-8")
            await form_indexer.index_form(f"forms/{mform.id}.form.yaml", updated_content)

            # Only echo back to ``modified`` when the source was a companion
            # file that needed ref-resolution rewrites (back-compat path).
            # Inline content is regenerated from the DB on the next manifest
            # write, so there is nothing to echo back to disk.
            if mform.path and data != original_data and not _form_has_inline_content(mform):
                modified[mform.path] = updated_content.decode("utf-8")

            # Post-indexer: update org_id and access_level
            org_id_uuid = UUID(mform.organization_id) if mform.organization_id else None
            form_id_uuid = UUID(mform.id)
            post_values: dict = {}
            if org_id_uuid:
                post_values["organization_id"] = org_id_uuid
            if mform.access_level is not None:
                post_values["access_level"] = mform.access_level
            if post_values:
                post_values["updated_at"] = datetime.now(timezone.utc)
                await self.db.execute(
                    sa_update(Form).where(Form.id == form_id_uuid).values(**post_values)
                )

        return modified

    async def _index_workflows_from_manifest(
        self,
        manifest: "Manifest",
        read_fn: "Callable[[str], Awaitable[bytes | None]]",
        changed_ids: "set[str] | None" = None,
    ) -> None:
        """Run WorkflowIndexer for each workflow in the manifest.

        After plan_import creates/updates workflow DB records, this re-runs the
        AST-based indexer so that parameters_schema (and other code-derived
        fields) are populated.  Without this, workflows imported from the
        manifest would have an empty parameters_schema because the indexer
        skipped them during file-write (the DB record didn't exist yet).

        Args:
            manifest: Parsed manifest with workflow entries
            read_fn: Async callable that reads a file path, returning bytes or None
            changed_ids: If set, only process workflows whose ID is in this set
        """
        from src.services.file_storage.indexers.workflow import WorkflowIndexer

        indexer = WorkflowIndexer(self.db)

        for _wf_name, mworkflow in manifest.workflows.items():
            if changed_ids is not None and mworkflow.id not in changed_ids:
                continue
            content = await read_fn(mworkflow.path)
            if content is not None:
                await indexer.index_python_file(mworkflow.path, content)

        await self.db.flush()

    async def _index_agents_from_manifest(
        self,
        manifest: "Manifest",
        read_fn: "Callable[[str], Awaitable[bytes | None]]",
        changed_ids: "set[str] | None" = None,
    ) -> dict[str, str]:
        """Run AgentIndexer for each agent in the manifest.

        Args:
            manifest: Parsed manifest with agent entries
            read_fn: Async callable that reads a file path, returning bytes or None
            changed_ids: If set, only process agents whose ID is in this set

        Returns:
            Dict of {path: modified_content} for agents whose data changed during ref resolution.
        """
        from sqlalchemy import update as sa_update
        from src.models.orm.agents import Agent
        from src.services.file_storage.indexers.agent import AgentIndexer

        agent_indexer = AgentIndexer(self.db)
        modified: dict[str, str] = {}

        for _agent_name, magent in manifest.agents.items():
            if changed_ids is not None and magent.id not in changed_ids:
                continue
            content_bytes = await _resolve_agent_content(magent, read_fn)
            if content_bytes is None:
                continue
            original_data = yaml.safe_load(content_bytes.decode("utf-8"))
            if not original_data:
                continue
            data = dict(original_data)
            data["id"] = magent.id
            await self._resolve_ref_field(data, "tool_ids")
            if "tools" in data and "tool_ids" not in data:
                await self._resolve_ref_field(data, "tools")
            updated_content = (yaml.dump(data, default_flow_style=False, sort_keys=True).rstrip() + "\n").encode("utf-8")
            await agent_indexer.index_agent(f"agents/{magent.id}.agent.yaml", updated_content)

            # Only echo back to ``modified`` when the source was a companion
            # file that needed ref-resolution rewrites (back-compat path).
            if magent.path and data != original_data and not _agent_has_inline_content(magent):
                modified[magent.path] = updated_content.decode("utf-8")

            # Post-indexer: update org_id and access_level
            org_id_uuid = UUID(magent.organization_id) if magent.organization_id else None
            agent_id_uuid = UUID(magent.id)
            post_values: dict = {}
            if org_id_uuid:
                post_values["organization_id"] = org_id_uuid
            if magent.access_level:
                post_values["access_level"] = magent.access_level
            if post_values:
                post_values["updated_at"] = datetime.now(timezone.utc)
                await self.db.execute(
                    sa_update(Agent).where(Agent.id == agent_id_uuid).values(**post_values)
                )

        return modified

    def _resolve_organization(self, morg, cache: dict) -> "list[SyncOp]":
        """Resolve an organization from manifest into SyncOps.

        ID-first, name-fallback upsert strategy using prefetch cache.
        Returns ops list without executing.
        """
        from uuid import UUID

        from src.models.orm.organizations import Organization
        from src.services.sync_ops import SyncOp, Upsert  # noqa: F401

        org_id = UUID(morg.id)

        # 1. Try by ID first (handles renames)
        if org_id in cache["org_ids"]:
            return [Upsert(
                model=Organization,
                id=org_id,
                values={"name": morg.name, "is_active": morg.is_active},
                match_on="id",
            )]

        # 2. Try by name (cross-env ID sync)
        existing_by_name = cache["org_by_name"].get(morg.name)
        if existing_by_name is not None:
            return [Upsert(
                model=Organization,
                id=org_id,
                values={"id": org_id, "name": morg.name, "is_active": morg.is_active},
                match_on="name",
            )]

        # 3. Insert new
        return [Upsert(
            model=Organization,
            id=org_id,
            values={"name": morg.name, "is_active": morg.is_active, "created_by": "git-sync"},
            match_on="id",
        )]

    def _resolve_role(self, mrole, cache: dict) -> "list[SyncOp]":
        """Resolve a role from manifest into SyncOps.

        ID-first, name-fallback upsert strategy using prefetch cache.
        Returns ops list without executing.
        """
        from uuid import UUID

        from src.models.orm.users import Role
        from src.services.sync_ops import SyncOp, Upsert  # noqa: F401

        role_id = UUID(mrole.id)

        # 1. Try by ID first (handles renames)
        if role_id in cache["role_ids"]:
            return [Upsert(
                model=Role,
                id=role_id,
                values={"name": mrole.name},
                match_on="id",
            )]

        # 2. Try by name (cross-env ID sync)
        existing_by_name = cache["role_by_name"].get(mrole.name)
        if existing_by_name is not None:
            return [Upsert(
                model=Role,
                id=role_id,
                values={"id": role_id, "name": mrole.name},
                match_on="name",
            )]

        # 3. Insert new
        return [Upsert(
            model=Role,
            id=role_id,
            values={"name": mrole.name, "created_by": "git-sync"},
            match_on="id",
        )]

    async def _sync_role_assignments(self, entity_id, manifest_roles: list[str], junction_model, entity_fk_name: str) -> None:
        """Sync role assignments for an entity: add first, then remove (no permission gap).

        Args:
            entity_id: The entity's UUID
            manifest_roles: List of role UUID strings from manifest
            junction_model: The ORM model for the junction table (e.g. WorkflowRole)
            entity_fk_name: The FK column name on the junction table (e.g. 'workflow_id')
        """
        from uuid import UUID

        from sqlalchemy import delete as sa_delete
        from sqlalchemy.dialects.postgresql import insert

        desired_role_ids = {UUID(r) for r in manifest_roles}

        # Get current assignments
        entity_fk_col = getattr(junction_model, entity_fk_name)
        role_id_col = getattr(junction_model, "role_id")
        result = await self.db.execute(
            select(role_id_col).where(entity_fk_col == entity_id)
        )
        current_role_ids = {row[0] for row in result.all()}

        # ADD new assignments first (no permission gap)
        for role_id in desired_role_ids - current_role_ids:
            stmt = insert(junction_model).values(**{
                entity_fk_name: entity_id,
                "role_id": role_id,
                "assigned_by": "git-sync",
            }).on_conflict_do_nothing()
            await self.db.execute(stmt)

        # THEN remove stale assignments
        for role_id in current_role_ids - desired_role_ids:
            await self.db.execute(
                sa_delete(junction_model).where(
                    entity_fk_col == entity_id,
                    role_id_col == role_id,
                )
            )

    def _resolve_workflow(self, manifest_name: str, mwf, cache: dict) -> "list[SyncOp]":
        """Resolve a workflow from manifest into SyncOps.

        Uses prefetch cache for natural-key (path+function_name) or ID lookup.
        Returns ops list without executing.
        """
        from uuid import UUID

        from src.models.orm.workflow_roles import WorkflowRole
        from src.models.orm.workflows import Workflow
        from src.services.sync_ops import SyncOp, SyncRoles, Upsert  # noqa: F401

        wf_id = UUID(mwf.id)
        org_id = UUID(mwf.organization_id) if mwf.organization_id else None

        # Check prefetch cache for existing workflow
        existing_by_natural = cache["wf_by_natural"].get((mwf.path, mwf.function_name))
        existing_by_id = wf_id if wf_id in cache["wf_ids"] else None

        wf_values = {
            "name": manifest_name,
            "function_name": mwf.function_name,
            "path": mwf.path,
            "type": getattr(mwf, "type", "workflow"),
            "is_active": True,
            "organization_id": org_id,
            "endpoint_enabled": getattr(mwf, "endpoint_enabled", False),
            "timeout_seconds": mwf.timeout_seconds if mwf.timeout_seconds is not None else 1800,
            "public_endpoint": getattr(mwf, "public_endpoint", False),
            "category": getattr(mwf, "category", "General"),
            "tags": getattr(mwf, "tags", []),
        }
        if mwf.access_level is not None:
            wf_values["access_level"] = mwf.access_level

        # Only include description if manifest explicitly provides it
        if mwf.description is not None:
            wf_values["description"] = mwf.description

        ops: list[SyncOp] = []

        if existing_by_natural is not None:
            # Match on natural key — update (including ID if it changed)
            ops.append(Upsert(
                model=Workflow,
                id=existing_by_natural,
                values={"id": wf_id, **wf_values},
                match_on="id",
            ))
        elif existing_by_id is not None:
            # Same ID but path/function changed (rename) — update
            ops.append(Upsert(
                model=Workflow,
                id=wf_id,
                values=wf_values,
                match_on="id",
            ))
        else:
            # New workflow — insert
            ops.append(Upsert(
                model=Workflow,
                id=wf_id,
                values=wf_values,
                match_on="id",
            ))

        # Role sync op
        if hasattr(mwf, "roles") and mwf.roles:
            role_ids = {UUID(r) for r in mwf.roles}
            ops.append(SyncRoles(
                junction_model=WorkflowRole,
                entity_fk="workflow_id",
                entity_id=wf_id,
                role_ids=role_ids,
            ))

        return ops

    async def _resolve_workflow_ref(self, ref: str) -> "UUID | None":
        """Resolve a workflow reference: try UUID, then path::function_name, then name.

        Used by event subscription sync to support flexible workflow_id formats
        in the manifest (UUID, path::func, or workflow name).

        Returns UUID if found, None otherwise.
        """
        from uuid import UUID

        from src.models.orm.workflows import Workflow

        # 1. Try as UUID — direct ID match
        try:
            wf_id = UUID(ref)
            result = await self.db.execute(select(Workflow.id).where(Workflow.id == wf_id))
            if result.scalar_one_or_none():
                return wf_id
        except ValueError:
            # Not a UUID — fall through to path::func / name resolution
            logger.debug(f"workflow ref {ref!r} is not a UUID, trying alternate resolutions")

        # 2. Try as path::function_name
        if "::" in ref:
            path, func = ref.rsplit("::", 1)
            result = await self.db.execute(
                select(Workflow.id).where(Workflow.path == path, Workflow.function_name == func)
            )
            wf_id = result.scalar_one_or_none()
            if wf_id:
                return wf_id

        # 3. Try as workflow name
        result = await self.db.execute(select(Workflow.id).where(Workflow.name == ref))
        wf_id = result.scalar_one_or_none()
        if wf_id:
            return wf_id

        return None

    async def _resolve_portable_ref(self, ref: str) -> str | None:
        """Resolve a path::function_name portable ref to a workflow UUID string.

        Args:
            ref: A string like "workflows/foo.py::bar"

        Returns:
            UUID string if found, None otherwise
        """
        from src.models.orm.workflows import Workflow

        if "::" not in ref:
            return None

        path, _, function_name = ref.rpartition("::")
        if not path or not function_name:
            return None

        result = await self.db.execute(
            select(Workflow.id).where(
                Workflow.path == path,
                Workflow.function_name == function_name,
                Workflow.is_active.is_(True),
            )
        )
        wf_id = result.scalar_one_or_none()
        return str(wf_id) if wf_id else None

    async def _resolve_ref_field(self, data: dict, field_name: str) -> None:
        """Resolve a portable ref in a dict field to a UUID in-place.

        If the field value contains '::', attempts to resolve it.
        If resolution fails, the value is left unchanged (will be stored as-is).
        """
        value = data.get(field_name)
        if isinstance(value, str) and "::" in value:
            resolved = await self._resolve_portable_ref(value)
            if resolved:
                data[field_name] = resolved
                logger.info(f"Resolved portable ref '{value}' -> '{resolved}'")
            else:
                logger.warning(f"Could not resolve portable ref '{value}' for field '{field_name}'")
        elif isinstance(value, list):
            # Handle list fields like tool_ids
            resolved_list = []
            for item in value:
                if isinstance(item, str) and "::" in item:
                    resolved = await self._resolve_portable_ref(item)
                    resolved_list.append(resolved if resolved else item)
                else:
                    resolved_list.append(item)
            data[field_name] = resolved_list

    async def _resolve_deletions(self, work_dir: Path | None = None, manifest: "Manifest | None" = None, repo: "RepoStorage | None" = None, dry_run: bool = False) -> list:
        """Compute delete/deactivate ops for entities removed from the manifest.

        Optimized: pushes filtering to SQL with NOT IN clauses, returning only
        stale entity IDs. Executes bulk deletes inline instead of generating
        individual Delete/Deactivate ops.

        Deletion strategy per entity type:
        - Workflows, Forms, Agents, Apps: hard-delete (existing behavior)
        - Integrations, Configs, Events: hard-delete (manifest is source of truth)
        - Tables: soft-delete (keep data, set inactive — never created here currently)
        - Knowledge: not managed by git-sync (ephemeral, derived from documents)
        - Organizations, Roles: soft-delete (only git-sync created ones)

        Returns list of EntityChange entries for removed entities.
        """
        from uuid import UUID

        from sqlalchemy import delete as sa_delete
        from sqlalchemy import update as sa_update

        from src.models.contracts.github import EntityChange
        from src.models.orm.agents import Agent
        from src.models.orm.applications import Application
        from src.models.orm.config import Config
        from src.models.orm.events import EventSource, EventSubscription
        from src.models.orm.forms import Form
        from src.models.orm.integrations import Integration
        from src.models.orm.organizations import Organization
        from src.models.orm.tables import Table
        from src.models.orm.users import Role
        from src.models.orm.workflows import Workflow

        if manifest is None:
            if work_dir:
                manifest = read_manifest_from_dir(work_dir / ".bifrost")
            else:
                raise ValueError("Either manifest or work_dir must be provided")

        # Build existence-check helpers based on repo or work_dir
        if repo:
            all_s3_paths = set(await repo.list(""))

            def _path_exists(p: str) -> bool:
                return p in all_s3_paths

            def _dir_exists(p: str) -> bool:
                prefix = p.rstrip("/") + "/"
                return any(sp.startswith(prefix) for sp in all_s3_paths)
        elif work_dir:
            def _path_exists(p: str) -> bool:
                return (work_dir / p).exists()

            def _dir_exists(p: str) -> bool:
                return (work_dir / p).is_dir()
        else:
            def _path_exists(p: str) -> bool:
                return True

            def _dir_exists(p: str) -> bool:
                return True

        # Collect UUIDs of entities present in the manifest AND whose files exist.
        # Forms/agents now carry inline content under their UUID — there is no
        # required companion file. If ``path`` is set (back-compat), still gate
        # on file existence; otherwise the manifest entry alone is sufficient.
        present_wf_uuids = [
            UUID(mwf.id) for mwf in manifest.workflows.values()
            if _path_exists(mwf.path)
        ]
        present_form_uuids = [
            UUID(mform.id) for mform in manifest.forms.values()
            if not mform.path or _path_exists(mform.path)
        ]
        present_agent_uuids = [
            UUID(magent.id) for magent in manifest.agents.values()
            if not magent.path or _path_exists(magent.path)
        ]
        present_app_uuids = [
            UUID(mapp.id) for mapp in manifest.apps.values()
            if _dir_exists(mapp.path)
        ]

        present_integ_uuids = [UUID(m.id) for m in manifest.integrations.values()]
        present_config_uuids = [UUID(m.id) for m in manifest.configs.values()]
        present_table_uuids = [UUID(m.id) for m in manifest.tables.values()]
        present_event_uuids = [UUID(m.id) for m in manifest.events.values()]
        present_sub_uuids: list[UUID] = []
        for mes in manifest.events.values():
            for msub in mes.subscriptions:
                present_sub_uuids.append(UUID(msub.id))
        present_org_uuids = [UUID(m.id) for m in manifest.organizations]
        present_role_uuids = [UUID(m.id) for m in manifest.roles]

        entity_changes: list[EntityChange] = []
        now = datetime.now(timezone.utc)

        # Helper: query stale IDs (+ names when available) and bulk-delete
        async def _bulk_delete(model: type, base_filter: list, present: list[UUID], entity_type: str) -> int:
            """Find IDs not in present list and delete them. Returns count."""
            has_name = "name" in model.__table__.columns  # type: ignore[attr-defined]
            if has_name:
                q = select(model.id, model.name).where(*base_filter)  # type: ignore[attr-defined]
            else:
                q = select(model.id).where(*base_filter)  # type: ignore[attr-defined]
            if present:
                q = q.where(model.id.notin_(present))  # type: ignore[attr-defined]
            result = await self.db.execute(q)
            rows = result.all()
            if not rows:
                return 0
            stale_ids = []
            for row in rows:
                sid = row[0]
                name = row[1] if has_name else str(sid)
                stale_ids.append(sid)
                logger.info(f"Deleting {model.__tablename__} {sid} ({name}) — removed from repo")  # type: ignore[attr-defined]
                entity_changes.append(EntityChange(
                    action="removed",
                    entity_type=entity_type,
                    name=name,
                ))
            if not dry_run:
                await self.db.execute(
                    sa_delete(model).where(model.id.in_(stale_ids))  # type: ignore[attr-defined]
                )
            return len(stale_ids)

        # Helper: query stale IDs and soft-delete (deactivate)
        async def _bulk_deactivate(model: type, base_filter: list, present: list[UUID], entity_type: str) -> int:
            has_name = "name" in model.__table__.columns  # type: ignore[attr-defined]
            if has_name:
                q = select(model.id, model.name).where(*base_filter)  # type: ignore[attr-defined]
            else:
                q = select(model.id).where(*base_filter)  # type: ignore[attr-defined]
            if present:
                q = q.where(model.id.notin_(present))  # type: ignore[attr-defined]
            result = await self.db.execute(q)
            rows = result.all()
            if not rows:
                return 0
            stale_ids = []
            for row in rows:
                sid = row[0]
                name = row[1] if has_name else str(sid)
                stale_ids.append(sid)
                logger.info(f"Deactivating {model.__tablename__} {sid} ({name}) — removed from manifest")  # type: ignore[attr-defined]
                entity_changes.append(EntityChange(
                    action="removed",
                    entity_type=entity_type,
                    name=name,
                ))
            if not dry_run:
                await self.db.execute(
                    sa_update(model)
                    .where(model.id.in_(stale_ids))  # type: ignore[attr-defined]
                    .values(is_active=False, updated_at=now)
                )
            return len(stale_ids)

        # Delete workflows synced from git that are no longer present
        await _bulk_delete(
            Workflow,
            [Workflow.is_active == True, Workflow.path.isnot(None)],  # noqa: E712
            present_wf_uuids,
            "workflows",
        )

        # Delete integrations not in manifest
        await _bulk_delete(
            Integration,
            [Integration.is_deleted == False],  # noqa: E712
            present_integ_uuids,
            "integrations",
        )

        # Delete configs not in manifest (skip integration-schema-linked configs —
        # those are user-set values managed by IntegrationConfigSchema cascade)
        cfg_q = select(Config.id).where(Config.config_schema_id.is_(None))
        if present_config_uuids:
            cfg_q = cfg_q.where(Config.id.notin_(present_config_uuids))
        cfg_result = await self.db.execute(cfg_q)
        stale_cfg_ids = [row[0] for row in cfg_result.all()]
        if stale_cfg_ids:
            for sid in stale_cfg_ids:
                logger.info(f"Deleting config {sid} — removed from repo")
                entity_changes.append(EntityChange(
                    action="removed",
                    entity_type="configs",
                    name=str(sid),
                ))
            if not dry_run:
                await self.db.execute(
                    sa_delete(Config).where(Config.id.in_(stale_cfg_ids))
                )

        # Tables not in manifest (data preserved — report as "keep")
        table_q = select(Table.id, Table.name)
        if present_table_uuids:
            table_q = table_q.where(Table.id.notin_(present_table_uuids))
        table_result = await self.db.execute(table_q)
        for row in table_result.all():
            logger.info(f"Table {row[0]} ({row[1]}) not in manifest (data preserved)")
            entity_changes.append(EntityChange(
                action="keep",
                entity_type="tables",
                name=row[1] or str(row[0]),
            ))

        # Delete event subscriptions not in manifest
        await _bulk_delete(EventSubscription, [], present_sub_uuids, "event_subscriptions")

        # Delete event sources not in manifest
        await _bulk_delete(EventSource, [], present_event_uuids, "events")

        # Delete forms not in manifest
        await _bulk_delete(
            Form,
            [Form.is_active == True],  # noqa: E712
            present_form_uuids,
            "forms",
        )

        # Delete agents not in manifest
        await _bulk_delete(Agent, [], present_agent_uuids, "agents")

        # Delete apps not in manifest
        await _bulk_delete(Application, [], present_app_uuids, "applications")

        # Soft-delete organizations not in manifest (only when manifest has orgs)
        if present_org_uuids:
            await _bulk_deactivate(
                Organization,
                [Organization.is_active == True],  # noqa: E712
                present_org_uuids,
                "organizations",
            )

        # Delete roles not in manifest (only when manifest has roles)
        if present_role_uuids:
            await _bulk_delete(Role, [], present_role_uuids, "roles")

        return entity_changes

    async def _resolve_integration(self, integ_name: str, minteg, cache: dict | None = None) -> "list[SyncOp]":
        """Resolve an integration from manifest into SyncOps.

        Upserts the integration and directly executes config schema, oauth
        provider, and mapping sub-operations (these are complex sub-object
        syncs without their own resolution pattern).
        Uses prefetch cache for lookups when available.
        """
        from uuid import UUID

        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
        from src.models.orm.oauth import OAuthProvider
        from src.services.sync_ops import SyncOp, Upsert  # noqa: F401

        integ_id = UUID(minteg.id)

        # Check by natural key (name) — use cache if available
        if cache is not None:
            existing_by_name = cache["integ_by_name"].get(integ_name)
        else:
            by_name = await self.db.execute(
                select(Integration.id).where(Integration.name == integ_name)
            )
            existing_by_name = by_name.scalar_one_or_none()

        integ_values: dict = {
            "name": integ_name,
            "entity_id": minteg.entity_id,
            "entity_id_name": minteg.entity_id_name,
            "default_entity_id": minteg.default_entity_id,
            "list_entities_data_provider_id": (
                UUID(minteg.list_entities_data_provider_id)
                if minteg.list_entities_data_provider_id else None
            ),
            "is_deleted": False,
        }

        # Upsert integration row FIRST (must exist before config schema / mapping FKs)
        id_was_rewritten = existing_by_name is not None and existing_by_name != integ_id
        if existing_by_name is not None:
            upsert_op = Upsert(
                model=Integration,
                id=existing_by_name,
                values={"id": integ_id, **integ_values},
                match_on="id",
            )
        else:
            upsert_op = Upsert(
                model=Integration,
                id=integ_id,
                values=integ_values,
                match_on="id",
            )
        await upsert_op.execute(self.db)

        # If the upsert rewrote the integration's PK (cross-env id sync), the
        # FK ON UPDATE CASCADE on integration_config_schema, integration_mappings,
        # and configs migrated those rows from existing_by_name → integ_id in the
        # same statement. The prefetch cache is still keyed on the OLD id, so
        # refresh dependent caches before we read them — otherwise the
        # upsert-by-natural-key path below misses the (now-migrated) rows and
        # tries to INSERT, hitting unique-index violations.
        if id_was_rewritten and cache is not None:
            cs_refresh = await self.db.execute(
                select(IntegrationConfigSchema).where(
                    IntegrationConfigSchema.integration_id == integ_id
                )
            )
            cache["integ_cs"][integ_id] = {
                cs.key: cs for cs in cs_refresh.scalars().all()
            }
            cache["integ_cs"].pop(existing_by_name, None)

            m_refresh = await self.db.execute(
                select(IntegrationMapping).where(
                    IntegrationMapping.integration_id == integ_id
                )
            )
            cache["integ_mappings"][integ_id] = {
                str(m.organization_id) if m.organization_id else None: m
                for m in m_refresh.scalars().all()
            }
            cache["integ_mappings"].pop(existing_by_name, None)

            # _resolve_config runs later and reads cache["config_by_natural"]
            # keyed on (key, integ_id, org_id). After CASCADE the underlying
            # rows live under integ_id; rewrite cache keys to match so the
            # update path is taken instead of an insert that would collide.
            cfg_natural = cache.get("config_by_natural")
            if cfg_natural:
                stale_keys = [k for k in cfg_natural if k[1] == existing_by_name]
                for old_key in stale_keys:
                    cfg_natural[(old_key[0], integ_id, old_key[2])] = cfg_natural.pop(old_key)

        # Sync config schema items: upsert by (integration_id, key) to preserve IDs
        # (Config rows reference schema IDs via FK — deleting schema cascades to configs)
        from sqlalchemy import delete as sa_delete
        if cache is not None:
            existing_cs_by_key = dict(cache["integ_cs"].get(integ_id, {}))
        else:
            existing_cs_result = await self.db.execute(
                select(IntegrationConfigSchema).where(
                    IntegrationConfigSchema.integration_id == integ_id
                )
            )
            existing_cs_by_key = {cs.key: cs for cs in existing_cs_result.scalars().all()}
        manifest_cs_keys = {cs.key for cs in minteg.config_schema}

        for cs in minteg.config_schema:
            if cs.key in existing_cs_by_key:
                existing_cs = existing_cs_by_key[cs.key]
                existing_cs.type = cs.type
                existing_cs.required = cs.required
                existing_cs.description = cs.description
                existing_cs.options = cs.options
                existing_cs.position = cs.position
            else:
                cs_stmt = insert(IntegrationConfigSchema).values(
                    integration_id=integ_id,
                    key=cs.key,
                    type=cs.type,
                    required=cs.required,
                    description=cs.description,
                    options=cs.options,
                    position=cs.position,
                )
                await self.db.execute(cs_stmt)

        removed_keys = set(existing_cs_by_key.keys()) - manifest_cs_keys
        if removed_keys:
            await self.db.execute(
                sa_delete(IntegrationConfigSchema).where(
                    IntegrationConfigSchema.integration_id == integ_id,
                    IntegrationConfigSchema.key.in_(removed_keys),
                )
            )

        # Sync OAuth provider (structure only — client_secret never imported)
        if minteg.oauth_provider:
            op_data = minteg.oauth_provider
            op_stmt = insert(OAuthProvider).values(
                provider_name=op_data.provider_name,
                display_name=op_data.display_name,
                oauth_flow_type=op_data.oauth_flow_type,
                client_id=op_data.client_id,
                encrypted_client_secret=b"",  # placeholder — needs manual setup
                authorization_url=op_data.authorization_url,
                token_url=op_data.token_url,
                token_url_defaults=op_data.token_url_defaults or {},
                scopes=op_data.scopes or [],
                redirect_uri=op_data.redirect_uri,
                integration_id=integ_id,
            ).on_conflict_do_update(
                constraint="uq_oauth_providers_integration_id",
                set_={
                    "display_name": op_data.display_name,
                    "oauth_flow_type": op_data.oauth_flow_type,
                    **(
                        {"client_id": op_data.client_id}
                        if op_data.client_id and op_data.client_id != "__NEEDS_SETUP__"
                        else {}
                    ),
                    "authorization_url": op_data.authorization_url,
                    "token_url": op_data.token_url,
                    "token_url_defaults": op_data.token_url_defaults or {},
                    "scopes": op_data.scopes or [],
                    "redirect_uri": op_data.redirect_uri,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await self.db.execute(op_stmt)

        # Sync mappings: upsert by (integration_id, organization_id) to preserve oauth_token_id
        if cache is not None:
            existing_m_by_org: dict[str | None, IntegrationMapping] = dict(cache["integ_mappings"].get(integ_id, {}))
        else:
            existing_m_result = await self.db.execute(
                select(IntegrationMapping).where(
                    IntegrationMapping.integration_id == integ_id
                )
            )
            existing_m_by_org = {
                str(m.organization_id) if m.organization_id else None: m
                for m in existing_m_result.scalars().all()
            }
        manifest_org_ids = {mapping.organization_id for mapping in minteg.mappings}

        for mapping in minteg.mappings:
            org_key = mapping.organization_id  # str or None
            if org_key in existing_m_by_org:
                existing_m = existing_m_by_org[org_key]
                existing_m.entity_id = mapping.entity_id
                existing_m.entity_name = mapping.entity_name
                if mapping.oauth_token_id is not None:
                    existing_m.oauth_token_id = UUID(mapping.oauth_token_id)
            else:
                m_stmt = insert(IntegrationMapping).values(
                    integration_id=integ_id,
                    organization_id=UUID(mapping.organization_id) if mapping.organization_id else None,
                    entity_id=mapping.entity_id,
                    entity_name=mapping.entity_name,
                    oauth_token_id=UUID(mapping.oauth_token_id) if mapping.oauth_token_id else None,
                )
                await self.db.execute(m_stmt)

        for org_key, existing_m in existing_m_by_org.items():
            if org_key not in manifest_org_ids:
                await self.db.execute(
                    sa_delete(IntegrationMapping).where(
                        IntegrationMapping.id == existing_m.id
                    )
                )

        # Return empty list — all operations executed directly above
        return []

    def _resolve_config(self, mcfg, cache: dict) -> "list[SyncOp]":
        """Resolve a config entry from manifest into SyncOps.

        Uses prefetch cache for lookup. Skips writing value if type=SECRET
        and existing value is non-null. Returns ops list.
        """
        from uuid import UUID

        from src.models.orm.config import Config
        from src.services.sync_ops import SyncOp, Upsert  # noqa: F401

        cfg_id = UUID(mcfg.id)
        integ_id = UUID(mcfg.integration_id) if mcfg.integration_id else None
        org_id = UUID(mcfg.organization_id) if mcfg.organization_id else None

        # Check prefetch cache for existing config by natural key
        cache_hit = cache["config_by_natural"].get((mcfg.key, integ_id, org_id))

        # Convert string config_type to enum for proper DB storage
        from src.models.enums import ConfigType
        ct = ConfigType(mcfg.config_type) if isinstance(mcfg.config_type, str) else mcfg.config_type
        is_secret = ct == ConfigType.SECRET

        if cache_hit is not None:
            existing_id, existing_value, _config_schema_id = cache_hit

            # Secret with existing value — don't overwrite
            if is_secret and existing_value is not None:
                return []

            # Update existing row (including ID if it changed)
            update_values: dict = {
                "id": cfg_id,
                "key": mcfg.key,
                "config_type": ct,
                "description": mcfg.description,
                "integration_id": integ_id,
                "organization_id": org_id,
                "updated_by": "git-sync",
            }
            if not is_secret:
                update_values["value"] = mcfg.value if mcfg.value is not None else {}

            return [Upsert(
                model=Config,
                id=existing_id,
                values=update_values,
                match_on="id",
            )]
        else:
            # New config — return Upsert op (uses ON CONFLICT)
            insert_values: dict = {
                "key": mcfg.key,
                "config_type": ct,
                "description": mcfg.description,
                "integration_id": integ_id,
                "organization_id": org_id,
                "value": mcfg.value if mcfg.value is not None else {},
                "updated_by": "git-sync",
            }
            return [Upsert(
                model=Config,
                id=cfg_id,
                values=insert_values,
                match_on="id",
            )]

    def _resolve_app(self, mapp, cache: dict) -> "list[SyncOp]":
        """Resolve an app from manifest into SyncOps (metadata only).
        Uses prefetch cache for slug lookup.
        """
        from pathlib import PurePosixPath
        from uuid import UUID

        from src.models.orm.app_roles import AppRole
        from src.models.orm.applications import Application
        from src.services.sync_ops import SyncOp, SyncRoles, Upsert  # noqa: F401

        # repo_path is now the directory directly (no /app.yaml to strip)
        repo_path = mapp.path.rstrip("/") if mapp.path else None

        # Slug from manifest entry, or derive from repo_path leaf
        slug = mapp.slug or (PurePosixPath(repo_path).name if repo_path else None)
        if not slug:
            logger.warning(f"App {mapp.id} has no slug or path, skipping")
            return []

        if not repo_path:
            repo_path = f"apps/{slug}"

        app_id = UUID(mapp.id)
        org_id = UUID(mapp.organization_id) if mapp.organization_id else None

        # Check prefetch cache for existing app by slug
        existing_id = cache["app_by_slug"].get(slug)

        app_values = {
            "name": mapp.name or "",
            "description": mapp.description,
            "slug": slug,
            "repo_path": repo_path,
            "organization_id": org_id,
            "dependencies": mapp.dependencies or None,
        }
        if mapp.access_level is not None:
            app_values["access_level"] = mapp.access_level

        ops: list[SyncOp] = []

        if existing_id is not None:
            ops.append(Upsert(
                model=Application,
                id=existing_id,
                values={"id": app_id, **app_values},
                match_on="id",
            ))
        else:
            ops.append(Upsert(
                model=Application,
                id=app_id,
                values=app_values,
                match_on="id",
            ))

        # Role sync op
        if hasattr(mapp, "roles") and mapp.roles:
            role_ids = {UUID(r) for r in mapp.roles}
            ops.append(SyncRoles(
                junction_model=AppRole,
                entity_fk="app_id",
                entity_id=app_id,
                role_ids=role_ids,
            ))

        return ops

    async def _resolve_table(self, table_name: str, mtable, cache: dict | None = None) -> "list[SyncOp]":
        """Resolve a table definition from manifest into SyncOps (schema only, no data).

        Uses prefetch cache for lookups when available.
        Two-pass natural-key lookup (mirrors _resolve_workflow):
        1. Match by (name, organization_id) — if found, update including ID realignment
        2. Match by ID — if found, update name/schema
        3. Otherwise insert new

        ID realignment ensures the DB row ID matches the manifest UUID so that
        _resolve_deletions can correctly identify which tables are present.
        Documents are preserved in all cases (cascade is on the table row, and
        we never delete the row here).
        """
        from uuid import UUID

        from sqlalchemy import update
        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.tables import Table
        from src.services.sync_ops import SyncOp  # noqa: F401

        table_id = UUID(mtable.id)
        org_id = UUID(mtable.organization_id) if mtable.organization_id else None
        app_id = UUID(mtable.application_id) if mtable.application_id else None
        now = datetime.now(timezone.utc)

        # 1. Look up by natural key (name + org) — use cache if available
        if cache is not None:
            existing_by_natural = cache["table_by_natural"].get((table_name, org_id))
        else:
            natural_q = select(Table.id).where(
                Table.name == table_name,
                Table.organization_id == org_id,
            )
            existing_by_natural = (await self.db.execute(natural_q)).scalar_one_or_none()

        if existing_by_natural is not None:
            if existing_by_natural != table_id:
                # ID mismatch (cross-env) — realign the DB row's ID to the manifest ID.
                # Documents have ON UPDATE CASCADE on table_id so they follow along.
                logger.info(
                    f"Realigning table {table_name!r}: DB id={existing_by_natural} → manifest id={table_id}"
                )
            await self.db.execute(
                update(Table)
                .where(Table.id == existing_by_natural)
                .values(
                    id=table_id,
                    description=mtable.description,
                    application_id=app_id,
                    schema=mtable.table_schema,
                    updated_at=now,
                )
            )
            return []

        # 2. Look up by ID (name changed, same ID) — use cache if available
        if cache is not None:
            existing_by_id = table_id if table_id in cache["table_ids"] else None
        else:
            existing_by_id = (
                await self.db.execute(select(Table.id).where(Table.id == table_id))
            ).scalar_one_or_none()

        if existing_by_id is not None:
            await self.db.execute(
                update(Table)
                .where(Table.id == table_id)
                .values(
                    name=table_name,
                    description=mtable.description,
                    application_id=app_id,
                    schema=mtable.table_schema,
                    updated_at=now,
                )
            )
            return []

        # 3. New table — insert
        stmt = insert(Table).values(
            id=table_id,
            name=table_name,
            description=mtable.description,
            organization_id=org_id,
            application_id=app_id,
            schema=mtable.table_schema,
            created_by="git-sync",
        ).on_conflict_do_nothing()
        await self.db.execute(stmt)

        return []

    async def _resolve_event_source(self, es_name: str, mes, imported_wf_ids: set[str] | None = None) -> "list[SyncOp]":
        """Resolve an event source + subscriptions from manifest into SyncOps.

        Event sources use PostgreSQL ON CONFLICT upserts (PostgreSQL-specific
        constructs); executed directly here, returning empty ops list.

        imported_wf_ids: set of workflow UUIDs (as strings) that were actually
        imported (file existed on disk). Subscriptions referencing workflows
        not in this set are skipped to avoid FK violations.
        """
        from uuid import UUID

        from sqlalchemy.dialects.postgresql import insert

        from src.models.orm.events import EventSource, EventSubscription, ScheduleSource, WebhookSource
        from src.services.sync_ops import SyncOp  # noqa: F401

        es_id = UUID(mes.id)

        # Upsert event source
        stmt = insert(EventSource).values(
            id=es_id,
            name=es_name,
            source_type=mes.source_type,
            organization_id=UUID(mes.organization_id) if mes.organization_id else None,
            is_active=mes.is_active,
            created_by="git-sync",
        ).on_conflict_do_update(
            index_elements=["id"],
            set_={
                "name": es_name,
                "source_type": mes.source_type,
                "organization_id": UUID(mes.organization_id) if mes.organization_id else None,
                "is_active": mes.is_active,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await self.db.execute(stmt)

        # Upsert schedule source if applicable
        if mes.source_type == "schedule" and mes.cron_expression:
            overlap_policy = mes.overlap_policy or "skip"
            sched_stmt = insert(ScheduleSource).values(
                event_source_id=es_id,
                cron_expression=mes.cron_expression,
                timezone=mes.timezone or "UTC",
                enabled=mes.schedule_enabled if mes.schedule_enabled is not None else True,
                overlap_policy=overlap_policy,
            ).on_conflict_do_update(
                index_elements=["event_source_id"],
                set_={
                    "cron_expression": mes.cron_expression,
                    "timezone": mes.timezone or "UTC",
                    "enabled": mes.schedule_enabled if mes.schedule_enabled is not None else True,
                    "overlap_policy": overlap_policy,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await self.db.execute(sched_stmt)

        # Upsert webhook source if applicable (external state left empty)
        if mes.source_type == "webhook":
            wh_stmt = insert(WebhookSource).values(
                event_source_id=es_id,
                adapter_name=mes.adapter_name,
                integration_id=UUID(mes.webhook_integration_id) if mes.webhook_integration_id else None,
                config=mes.webhook_config or {},
                rate_limit_per_minute=mes.rate_limit_per_minute,
                rate_limit_window_seconds=mes.rate_limit_window_seconds,
                rate_limit_enabled=mes.rate_limit_enabled,
            ).on_conflict_do_update(
                index_elements=["event_source_id"],
                set_={
                    "adapter_name": mes.adapter_name,
                    "integration_id": UUID(mes.webhook_integration_id) if mes.webhook_integration_id else None,
                    "config": mes.webhook_config or {},
                    "rate_limit_per_minute": mes.rate_limit_per_minute,
                    "rate_limit_window_seconds": mes.rate_limit_window_seconds,
                    "rate_limit_enabled": mes.rate_limit_enabled,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await self.db.execute(wh_stmt)

        # Sync subscriptions: upsert each
        # workflow_id may be a UUID string, a path::function_name portable ref, or a name
        for msub in mes.subscriptions:
            target_type = getattr(msub, "target_type", "workflow") or "workflow"

            wf_id: UUID | None = None
            agent_id: UUID | None = None

            if target_type == "agent":
                # Agent-targeted subscription
                if msub.agent_id:
                    try:
                        agent_id = UUID(msub.agent_id)
                    except ValueError:
                        logger.warning(
                            f"Event subscription {msub.id}: invalid agent_id "
                            f"'{msub.agent_id}', skipping"
                        )
                        continue
                else:
                    logger.warning(
                        f"Event subscription {msub.id}: target_type='agent' but "
                        f"no agent_id, skipping"
                    )
                    continue
            else:
                # Workflow-targeted subscription
                try:
                    wf_id = UUID(msub.workflow_id) if msub.workflow_id else None
                except (ValueError, AttributeError) as e:
                    # Non-UUID workflow_id (portable ref) — falls through to path::func resolution below
                    logger.debug(f"workflow_id {msub.workflow_id!r} is not a UUID, will resolve as portable ref: {e}")

                # For UUID workflow refs: skip if that workflow wasn't imported
                if wf_id is not None and imported_wf_ids is not None and msub.workflow_id not in imported_wf_ids:
                    logger.warning(
                        f"Event subscription {msub.id}: workflow {msub.workflow_id} "
                        f"not imported (file missing?), skipping"
                    )
                    continue

                if wf_id is None and msub.workflow_id:
                    # Try path::function_name or name resolution
                    resolved = await self._resolve_workflow_ref(msub.workflow_id)
                    if resolved is None:
                        logger.warning(
                            f"Event subscription {msub.id}: could not resolve workflow ref "
                            f"'{msub.workflow_id}', skipping"
                        )
                        continue
                    wf_id = resolved

                if wf_id is None:
                    logger.warning(
                        f"Event subscription {msub.id}: target_type='workflow' but "
                        f"no workflow_id, skipping"
                    )
                    continue

            sub_stmt = insert(EventSubscription).values(
                id=UUID(msub.id),
                event_source_id=es_id,
                target_type=target_type,
                workflow_id=wf_id,
                agent_id=agent_id,
                event_type=msub.event_type,
                filter_expression=msub.filter_expression,
                input_mapping=msub.input_mapping,
                is_active=msub.is_active,
                created_by="git-sync",
            ).on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "event_source_id": es_id,
                    "target_type": target_type,
                    "workflow_id": wf_id,
                    "agent_id": agent_id,
                    "event_type": msub.event_type,
                    "filter_expression": msub.filter_expression,
                    "input_mapping": msub.input_mapping,
                    "is_active": msub.is_active,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await self.db.execute(sub_stmt)

        return []

    def _resolve_form(self, mform, content: bytes) -> "list[SyncOp]":
        """Resolve form metadata from manifest into SyncOps.

        The FormIndexer call (content parsing) is a side-effect performed in
        _import_all_entities, not here. This method only handles metadata ops.
        """
        from uuid import UUID

        from src.models.orm.forms import Form, FormRole
        from src.services.sync_ops import SyncOp, SyncRoles, Upsert  # noqa: F401

        data = yaml.safe_load(content.decode("utf-8"))
        if not data:
            return []

        org_id = UUID(mform.organization_id) if mform.organization_id else None
        form_id = UUID(mform.id)
        ops: list[SyncOp] = []

        if org_id:
            form_values: dict = {
                "name": data.get("name", ""),
                "is_active": True,
                "created_by": "git-sync",
                "organization_id": org_id,
            }
            if mform.access_level is not None:
                form_values["access_level"] = mform.access_level
            ops.append(Upsert(
                model=Form,
                id=form_id,
                values=form_values,
                match_on="id",
            ))

        # Role sync op (FormRole.assigned_by is NOT NULL — pass via extra_fields)
        if hasattr(mform, "roles") and mform.roles:
            role_ids = {UUID(r) for r in mform.roles}
            ops.append(SyncRoles(
                junction_model=FormRole,
                entity_fk="form_id",
                entity_id=form_id,
                role_ids=role_ids,
                extra_fields={"assigned_by": "git-sync"},
            ))

        return ops

    def _resolve_agent(self, magent, content: bytes) -> "list[SyncOp]":
        """Resolve agent metadata from manifest into SyncOps.

        The AgentIndexer call (content parsing) is a side-effect performed in
        _import_all_entities, not here. This method only handles metadata ops.
        """
        from uuid import UUID

        from src.models.orm.agents import Agent, AgentRole
        from src.services.sync_ops import SyncOp, SyncRoles, Upsert  # noqa: F401

        data = yaml.safe_load(content.decode("utf-8"))
        if not data:
            return []

        org_id = UUID(magent.organization_id) if magent.organization_id else None
        agent_id = UUID(magent.id)
        ops: list[SyncOp] = []

        if org_id:
            agent_values: dict = {
                "name": data.get("name", ""),
                "system_prompt": data.get("system_prompt", ""),
                "is_active": True,
                "created_by": "git-sync",
                "organization_id": org_id,
                "max_iterations": data.get("max_iterations"),
                "max_token_budget": data.get("max_token_budget"),
            }
            if magent.access_level is not None:
                agent_values["access_level"] = magent.access_level
            ops.append(Upsert(
                model=Agent,
                id=agent_id,
                values=agent_values,
                match_on="id",
            ))

        # Role sync op (AgentRole.assigned_by is NOT NULL — pass via extra_fields)
        if hasattr(magent, "roles") and magent.roles:
            role_ids = {UUID(r) for r in magent.roles}
            ops.append(SyncRoles(
                junction_model=AgentRole,
                entity_fk="agent_id",
                entity_id=agent_id,
                role_ids=role_ids,
                extra_fields={"assigned_by": "git-sync"},
            ))

        return ops
