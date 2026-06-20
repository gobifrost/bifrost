"""Round-trip path drivers + policies.

A ``RoundTripPath`` names ONE real serialization round trip (e.g. the ``_repo``
git-sync path) and carries: the per-field-class policy that path applies, the
row-pairing strategy, and thin async wrappers that drive the REAL export/import
code — NO reimplementation of any serialization logic.

The ``_repo`` path drives:
  - export (DB -> ``.bifrost/*.yaml``): ``GitHubSyncService._regenerate_manifest_to_dir``
    (the split-file writer the importer reads — NOT bare ``generate_manifest``).
  - import (``.bifrost/*.yaml`` -> DB): ``GitHubSyncService._import_all_entities``
    (the wrapper that runs the Workflow/Form/Agent indexer side-effects).

``_import_all_entities`` is INCREMENTAL — ``_diff_and_collect`` returns early when
no entity id changed, so an export-then-import of the SAME DB state no-ops and
would false-green. The test driver forces a real delta by DELETING the seeded
entity between export and import, then asserts the import actually touched it
(``count > 0``) before checking the field round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.field_classes import FieldClass

Policy = dict[FieldClass, str]  # action per class: keep | scrub | stamp | remap


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

# _repo git-sync is a SAME-ENVIRONMENT round trip: ids and org bindings are
# kept (no remap); only true secrets are scrubbed from the on-disk manifest.
REPO_POLICY: Policy = {
    FieldClass.IDENTITY: "keep",
    FieldClass.CONTENT: "keep",
    FieldClass.ENVIRONMENT: "keep",
    FieldClass.SECRET: "scrub",
    FieldClass.REFERENCE: "keep",
}


@dataclass
class RoundTripPath:
    """A named real serialization round trip + the contract it must obey."""

    name: str
    policy: Policy
    pairing: str  # 'by_id' | 'by_remap' | 'by_match_key'


REPO_SYNC = RoundTripPath(name="_repo", policy=REPO_POLICY, pairing="by_id")


# A Solution install is a CROSS-INSTALL round trip: every entity id is remapped
# per install (uuid5(install_id, manifest_id)), the org binding is STAMPED from
# the target org (not carried), in-bundle references follow the remap, and true
# secrets are scrubbed from the plaintext manifest (they ride the encrypted
# .bifrost/secrets.enc envelope in full mode, or are dropped in shareable mode).
# CONTENT (incl. access_level — deploy preserves it, deploy.py:801/1323) is kept.
SOLUTION_SHAREABLE_POLICY: Policy = {
    FieldClass.IDENTITY: "remap",
    FieldClass.CONTENT: "keep",
    FieldClass.ENVIRONMENT: "stamp",
    FieldClass.SECRET: "scrub",
    FieldClass.REFERENCE: "remap",
}

# Full-backup applies the SAME policy to the plaintext MANIFEST envelope: env is
# stamped from the target, and secrets are STILL scrubbed from the manifest —
# they travel only inside the encrypted secrets.enc blob (asserted separately by
# the secret-envelope check, not by the manifest field round trip).
SOLUTION_FULL_POLICY: Policy = dict(SOLUTION_SHAREABLE_POLICY)


SOLUTION_SHAREABLE = RoundTripPath(
    name="solution_shareable", policy=SOLUTION_SHAREABLE_POLICY, pairing="by_remap"
)
SOLUTION_FULL = RoundTripPath(
    name="solution_full", policy=SOLUTION_FULL_POLICY, pairing="by_remap"
)


# ---------------------------------------------------------------------------
# Per-field transform overrides (Codex round-3 — each cites the code line).
#
# A handful of fields do NOT obey their field-class default action on the
# Solution deploy path.  Without these the generic class-policy assertion would
# raise a FALSE red.  Every entry names the deploy.py line that performs the
# divergent transform, so an override can never be added without a code proof
# (this is also the Task-7 guardrail).  Keyed by (manifest-model-name, field).
#   - "keep_env_ref": the value is an ENV-scoped grant id, NOT remapped through
#     solution_entity_id; assert it survives as-is (presence + value preserved).
#   - "scrub": Solution deploy DROPS the field to None on this path.
# A field NOT in this map uses its field-class policy action.
# ---------------------------------------------------------------------------
#   - "absent": the field is NOT serialized into the solution BUNDLE entry at
#     all — its value is carried by the install SCOPE (organization_id is
#     inherited from the install, not bound per-entity), so it must be missing
#     from both the before and after dict. Asserting it stays None/absent is the
#     correct stamp behaviour for a field the bundle never names.
FIELD_OVERRIDES: dict[tuple[str, str], str] = {
    # organization_id is NEVER part of a solution bundle entry — scope is
    # inherited from the install ("Scope is inherited from the install — no
    # per-entity binding", deploy.py:_upsert_workflows / capture _drop_none drops
    # the None). The install row carries the target org; the entry does not.
    ("ManifestWorkflow", "organization_id"): "absent",
    ("ManifestForm", "organization_id"): "absent",
    ("ManifestAgent", "organization_id"): "absent",
    ("ManifestApp", "organization_id"): "absent",
    ("ManifestTable", "organization_id"): "absent",
    ("ManifestConfig", "organization_id"): "absent",
    ("ManifestCustomClaim", "organization_id"): "absent",
    ("ManifestEventSource", "organization_id"): "absent",
    # Agent.mcp_connection_ids are env-scoped MCPConnection GRANTS, deployed
    # full-replace-from-manifest and NOT remapped via solution_entity_id
    # (deploy.py:1340). The generic REFERENCE remap-id check would false-red.
    ("ManifestAgent", "mcp_connection_ids"): "keep_env_ref",
    # EventSource.webhook_integration_id is reset to None on Solution deploy —
    # the install re-binds its own integration after install (deploy.py:1609).
    ("ManifestEventSource", "webhook_integration_id"): "scrub",
}


# ---------------------------------------------------------------------------
# EXTRA_FIELD_POLICY — the "single model" gap made visible (plan Full-dict §).
#
# Solution capture emits transport keys the ``Manifest*`` model never names.
# A field-class-only oracle is BLIND to them, which is exactly where a Bug-C
# silent drop hides.  The completeness assertion (assertions.py
# ``assert_dict_keys_accounted``) requires every emitted key to be EITHER a
# classified Manifest field OR declared here — an unaccounted key is a hard
# failure.  Keyed by (manifest-model-name, emitted-key) -> a note (the value is
# documentation only; presence is what the completeness check consults).  Each
# cites the capture.py line that emits it.
# ---------------------------------------------------------------------------
EXTRA_FIELD_POLICY: dict[tuple[str, str], str] = {
    # App: capture emits the source-tree payload + decoded-logo transport tier
    # (capture.py:541-553).  These are build/upload inputs, NOT row columns the
    # Manifest names — ``logo`` (a path string) is the portable field; the bytes
    # tiers ride alongside.  Re-capture of an installed app reads them back off
    # the persisted row / source store, so they are path-extras, not drops.
    ("ManifestApp", "repo_path"): "transport: app source dir (capture.py:541)",
    ("ManifestApp", "logo_b64"): "transport: decoded logo bytes (capture.py:548)",
    ("ManifestApp", "logo_content_type"): "transport: logo mime (capture.py:549)",
    ("ManifestApp", "src_files"): "transport: text source files (capture.py:550)",
    ("ManifestApp", "bin_files"): "transport: binary source files (capture.py:551)",
    ("ManifestApp", "dist_files"): "transport: prebuilt dist text (capture.py:552)",
    ("ManifestApp", "bin_dist_files"): "transport: prebuilt dist bin (capture.py:553)",
    # Form: capture emits the workflow ref BY path::func alongside the UUID
    # (capture.py:580-581) so a cross-env install can re-resolve the binding by
    # natural key.  They duplicate workflow_id/launch_workflow_id, are not Form
    # row content drops, and deploy re-resolves the UUIDs from the bundle.
    ("ManifestForm", "workflow_path"): "transport: workflow natural ref (capture.py:580)",
    ("ManifestForm", "workflow_function_name"): "transport: workflow natural ref (capture.py:581)",
    # Agent: max_run_timeout is an Agent ORM column (agents.py:76) capture emits
    # (capture.py:610) that ManifestAgent does not name.  Deploy now stamps it
    # (deploy.py _upsert_agents, mirroring max_iterations) so it round-trips.
    ("ManifestAgent", "max_run_timeout"): "transport: agent column, deploy-stamped (capture.py:610)",
}


# ---------------------------------------------------------------------------
# Solution real-code wrappers (NO reimplementation).
# ---------------------------------------------------------------------------


def expected_solution_id(installed_solution_id: UUID) -> Callable[[dict], str]:
    """Return ``expected_id(before)`` for ``by_remap`` pairing AND the ``remap=``
    callable for in-bundle reference fields.

    The post-install id of a source manifest entity is
    ``solution_entity_id(install_id, manifest_id)`` where ``install_id`` is the
    INSTALLED solution's id (deploy.py:100/112). A ref to an in-bundle entity is
    remapped with the SAME function; a ref to an out-of-bundle id is not in the
    map and passes through (return None so the exact-id check is skipped for it).
    """
    from src.services.solutions.deploy import solution_entity_id

    def _map(before: dict[str, Any]) -> str:
        return str(solution_entity_id(installed_solution_id, UUID(str(before["id"]))))

    return _map


def remap_ref_for(installed_solution_id: UUID, in_bundle_ids: set[str]) -> Callable[[Any], Any]:
    """``remap=`` callable for reference fields: map an in-bundle id to its
    post-install id; return None for an out-of-bundle id (skip the exact check).
    """
    from src.services.solutions.deploy import solution_entity_id

    def _map(ref: Any) -> Any:
        s = str(ref)
        if s not in in_bundle_ids:
            return None
        return str(solution_entity_id(installed_solution_id, UUID(s)))

    return _map


async def solution_export_zip(
    db: AsyncSession,
    solution: Any,
    *,
    password: str | None = None,
    include_values: bool = False,
    include_data: bool = False,
) -> bytes:
    """Real shareable/full export: capture the deployed solution LIVE into a
    bundle and serialize the installable workspace zip.

    Drives ``SolutionCaptureService.bundle_for`` (DB -> bundle) +
    ``build_workspace_zip`` (bundle -> zip) — the exact pair the
    ``GET /api/solutions/{id}/export`` router calls (solutions.py:312/315).
    A password (full mode) encrypts config_values/table_data into secrets.enc;
    shareable mode (no password) never carries the sensitive tier.
    """
    from src.services.solutions.capture import SolutionCaptureService
    from src.services.solutions.export import build_workspace_zip

    bundle = await SolutionCaptureService(db).bundle_for(
        solution, include_values=include_values, include_data=include_data
    )
    return build_workspace_zip(bundle, password=password)


async def solution_install_zip(
    db: AsyncSession,
    zip_bytes: bytes,
    *,
    organization_id: UUID | None,
    password: str | None = None,
    replace_secrets: bool = False,
    replace_data: bool = False,
) -> Any:
    """Real install: ``zip_install.install_zip`` (zip -> SolutionBundle ->
    ``SolutionDeployer.deploy``). Returns the installed ``Solution`` row."""
    from src.services.solutions.zip_install import install_zip

    return await install_zip(
        db,
        zip_bytes,
        organization_id=organization_id,
        config_values={},
        deployer_email="roundtrip@test.local",
        password=password,
        replace_secrets=replace_secrets,
        replace_data=replace_data,
    )


async def solution_bundle_entries(
    db: AsyncSession,
    solution: Any,
    collection: str,
    *,
    include_values: bool = False,
    include_data: bool = False,
) -> list[dict[str, Any]]:
    """Return the serialized manifest-shaped dicts a solution's *collection*
    produces, via the REAL ``bundle_for`` capture serializer.

    Used to read BOTH the source bundle (before) and the installed bundle
    (after) through the identical serializer, so the field round trip compares
    like for like. *collection* is a ``SolutionBundle`` list attr name
    (``"workflows"``, ``"tables"``, ...).
    """
    from src.services.solutions.capture import SolutionCaptureService

    bundle = await SolutionCaptureService(db).bundle_for(
        solution, include_values=include_values, include_data=include_data
    )
    return list(getattr(bundle, collection))


# ---------------------------------------------------------------------------
# Thin real-code wrappers (NO reimplementation)
# ---------------------------------------------------------------------------


def make_repo_sync_service(db: AsyncSession, work_dir: Path) -> Any:
    """Build a real ``GitHubSyncService`` for in-process round trips.

    We drive ``_regenerate_manifest_to_dir`` / ``_import_all_entities`` directly
    against a plain ``work_dir`` (a tmp directory).  Those two methods take a
    ``work_dir`` Path and never touch git or S3, so no remote / checkout is
    needed — the round trip is DB -> files (in work_dir) -> DB.
    """
    from src.services.github_sync import GitHubSyncService

    return GitHubSyncService(db=db, repo_url=f"file://{work_dir}", branch="main")


async def repo_export(db: AsyncSession, work_dir: Path) -> None:
    """Real ``_repo`` export: DB -> split ``.bifrost/*.yaml`` files in *work_dir*.

    Drives ``GitHubSyncService._regenerate_manifest_to_dir`` (the file-writing
    path the importer reads back), NOT bare ``generate_manifest``.
    """
    service = make_repo_sync_service(db, work_dir)
    await service._regenerate_manifest_to_dir(db, work_dir)


async def repo_import(db: AsyncSession, work_dir: Path) -> tuple[int, list]:
    """Real ``_repo`` import: ``.bifrost/*.yaml`` in *work_dir* -> DB.

    Drives ``GitHubSyncService._import_all_entities`` (the wrapper that runs the
    Workflow/Form/Agent indexers — where ``auto_fill`` and friends are dropped).
    Returns ``(count, entity_changes)``; ``count == 0`` means the incremental
    diff found nothing to import (a zero-op import is a test failure).
    """
    service = make_repo_sync_service(db, work_dir)
    return await service._import_all_entities(work_dir)


async def manifest_entry_for(
    db: AsyncSession,
    collection: str,
    entity_id: str,
) -> dict[str, Any] | None:
    """Return the manifest dict for one entity by id, via real ``generate_manifest``.

    *collection* is the ``Manifest`` attribute name (e.g. ``"workflows"``).  The
    returned dict is the serialized ``Manifest*`` model (``model_dump``) — the
    exact shape the field-class assertions compare.
    """
    from src.services.manifest_generator import generate_manifest

    manifest = await generate_manifest(db)
    coll: dict[str, Any] = getattr(manifest, collection)
    entry = coll.get(entity_id)
    return entry.model_dump() if entry is not None else None


def manifest_text(work_dir: Path) -> str:
    """Concatenate all written ``.bifrost/*.yaml`` files (for the secret-leak scan)."""
    bifrost_dir = work_dir / ".bifrost"
    if not bifrost_dir.is_dir():
        return ""
    parts: list[str] = []
    for f in sorted(bifrost_dir.glob("*.yaml")):
        parts.append(f.read_text())
    return "\n".join(parts)


# Map: Manifest collection attr -> a callable that deletes that entity from the
# DB to force a real import delta.  Each deleter removes the row (and its role
# junctions) so ``_diff_and_collect`` sees the manifest entity as a re-add.
async def delete_workflow(db: AsyncSession, entity_id: str) -> None:
    from uuid import UUID

    from sqlalchemy import delete

    from src.models.orm.workflow_roles import WorkflowRole
    from src.models.orm.workflows import Workflow

    wid = UUID(entity_id)
    await db.execute(delete(WorkflowRole).where(WorkflowRole.workflow_id == wid))
    await db.execute(delete(Workflow).where(Workflow.id == wid))
    await db.commit()


async def delete_table(db: AsyncSession, entity_id: str) -> None:
    from sqlalchemy import delete

    from src.models.orm.tables import Table

    await db.execute(delete(Table).where(Table.id == UUID(entity_id)))
    await db.commit()


async def delete_config(db: AsyncSession, entity_id: str) -> None:
    from sqlalchemy import delete

    from src.models.orm.config import Config

    await db.execute(delete(Config).where(Config.id == UUID(entity_id)))
    await db.commit()


async def delete_claim(db: AsyncSession, entity_id: str) -> None:
    from sqlalchemy import delete

    from src.models.orm.custom_claims import CustomClaim

    await db.execute(delete(CustomClaim).where(CustomClaim.id == UUID(entity_id)))
    await db.commit()


async def delete_event_source(db: AsyncSession, entity_id: str) -> None:
    from sqlalchemy import delete

    from src.models.orm.events import EventSource

    # ScheduleSource/WebhookSource/EventSubscription cascade via FK ondelete.
    await db.execute(delete(EventSource).where(EventSource.id == UUID(entity_id)))
    await db.commit()


async def delete_integration(db: AsyncSession, entity_id: str) -> None:
    from sqlalchemy import delete

    from src.models.orm.integrations import Integration

    # IntegrationConfigSchema / OAuthProvider / mappings cascade via FK ondelete.
    await db.execute(delete(Integration).where(Integration.id == UUID(entity_id)))
    await db.commit()


DELETERS: dict[str, Callable[[AsyncSession, str], Any]] = {
    "workflows": delete_workflow,
    "tables": delete_table,
    "configs": delete_config,
    "claims": delete_claim,
    "events": delete_event_source,
    "integrations": delete_integration,
}
