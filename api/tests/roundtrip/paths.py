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


DELETERS: dict[str, Callable[[AsyncSession, str], Any]] = {
    "workflows": delete_workflow,
}
