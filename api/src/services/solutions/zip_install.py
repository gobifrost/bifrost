"""Drag-and-drop ZIP install for Solutions (success-criteria §3, Tasks 11+12).

A "zip" is a compressed Solution *workspace* — the same shape ``bifrost export``
produces: a ``bifrost.solution.yaml`` descriptor + ``.bifrost/*.yaml`` manifests
+ ``apps/`` and ``workflows/`` source. The server unzips it and runs the EXISTING
deploy pipeline (:class:`SolutionDeployer`) — it does NOT reinvent deploy.

Two phases:

* :func:`preview_zip` — unzip to a temp dir, PARSE manifests only (no build, no
  DB write, no S3). Returns what the install would create + its declared configs.
* :func:`install_zip` — unzip, resolve-or-create the install at the chosen scope,
  run the proven lock → deploy → commit → finalize_s3 section, and IN THE SAME
  LOCKED SECTION after finalize, apply any provided config VALUES. Atomic: the
  install never exists without its just-entered secrets.

The workspace parsers are the CLI collectors in ``bifrost.commands.solution`` —
imported and reused server-side (the ``bifrost`` package is on the api path; the
git-sync module already imports these collectors). Reuse, not replication.
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ConfigType
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer


@dataclass
class PreviewResult:
    """What a zip would create — parse-only, nothing persisted."""

    slug: str | None = None
    name: str | None = None
    scope: str | None = None
    workflows: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    apps: list[dict[str, Any]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    config_schemas: list[dict[str, Any]] = field(default_factory=list)


def _safe_extract(data: bytes, dest: str) -> None:
    """Extract ``data`` (zip bytes) into ``dest``, rejecting zip-slip members.

    A member whose resolved path escapes ``dest`` (``../evil``, an absolute path,
    a symlink-style traversal) raises ``ValueError`` BEFORE anything is written —
    so a malicious zip can never plant a file outside the temp root.
    """
    dest_real = os.path.realpath(dest)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.namelist():
            target = os.path.realpath(os.path.join(dest, member))
            if not (target == dest_real or target.startswith(dest_real + os.sep)):
                raise ValueError(f"unsafe path in zip: {member}")
        z.extractall(dest)


def _parse_workspace(workspace: Path) -> PreviewResult:
    """Parse a Solution workspace dir into a :class:`PreviewResult` (no DB/S3)."""
    # Imported lazily so a malformed/zip-slip zip fails before any CLI import.
    from bifrost.commands.solution import (
        _collect_agents,
        _collect_apps,
        _collect_config_schemas,
        _collect_forms,
        _collect_tables,
        _collect_workflows,
    )
    from bifrost.solution_descriptor import is_solution_workspace, load_descriptor

    slug: str | None = None
    name: str | None = None
    scope: str | None = None
    if is_solution_workspace(workspace):
        descriptor = load_descriptor(workspace)
        slug, name, scope = descriptor.slug, descriptor.name, descriptor.scope

    return PreviewResult(
        slug=slug,
        name=name,
        scope=scope,
        workflows=_collect_workflows(workspace),
        tables=_collect_tables(workspace),
        apps=_collect_apps(workspace),
        forms=_collect_forms(workspace),
        agents=_collect_agents(workspace),
        config_schemas=_collect_config_schemas(workspace),
    )


def preview_zip(data: bytes) -> PreviewResult:
    """Parse a Solution workspace zip — no DB write, no S3, no build.

    Raises ``ValueError`` on a zip-slip member, ``zipfile.BadZipFile`` on
    non-zip bytes (the endpoint maps both to 422).
    """
    with tempfile.TemporaryDirectory(prefix="bifrost-zip-preview-") as tmp:
        _safe_extract(data, tmp)
        return _parse_workspace(Path(tmp))


def _build_bundle(solution: Solution, preview: PreviewResult, workspace: Path) -> SolutionBundle:
    """Build the full deploy bundle from a parsed workspace.

    ``preview`` already holds the manifest entities; only the Python source has
    to be read here (it is not part of the parse-only preview shape)."""
    from bifrost.commands.solution import _collect_python_files

    return SolutionBundle(
        solution=solution,
        python_files=_collect_python_files(workspace),
        workflows=preview.workflows,
        tables=preview.tables,
        apps=preview.apps,
        forms=preview.forms,
        agents=preview.agents,
        config_schemas=preview.config_schemas,
    )


async def _resolve_or_create_solution(
    db: AsyncSession, *, slug: str, name: str, organization_id: UUID | None
) -> Solution:
    """Find the install for ``(slug, organization_id)`` or create a fresh one.

    Exact-match resolve-or-create (a simplification of the CLI's
    ``_resolve_target_install``, which also guards cross-org ambiguity): each
    org's install of a slug is independent (criterion 9), so we match within the
    requested scope only and create when none exists.
    """
    if organization_id is not None:
        q = select(Solution).where(
            Solution.slug == slug, Solution.organization_id == organization_id
        )
    else:
        q = select(Solution).where(
            Solution.slug == slug, Solution.organization_id.is_(None)
        )
    existing = (await db.execute(q)).scalars().first()
    if existing is not None:
        return existing

    row = Solution(slug=slug, name=name, organization_id=organization_id)
    db.add(row)
    await db.flush()
    return row


async def install_zip(
    db: AsyncSession,
    data: bytes,
    *,
    organization_id: UUID | None,
    config_values: dict[str, Any],
    deployer_email: str,
) -> Solution:
    """Atomically install a Solution zip: deploy the bundle, then apply config
    VALUES — all under the per-install write lock.

    Mirrors the proven ``deploy_solution`` shape: lock → deploy → commit →
    finalize_s3 (S3 only after the DB is durable; still inside the lock). The
    provided config values are written AFTER finalize but BEFORE the lock is
    released, so the install never exists without its just-entered secrets.
    Re-raises the deploy exceptions for the endpoint to map.
    """
    from src.services.solutions.write_lock import solution_write_lock

    with tempfile.TemporaryDirectory(prefix="bifrost-zip-install-") as tmp:
        _safe_extract(data, tmp)
        workspace = Path(tmp)
        preview = _parse_workspace(workspace)
        if not preview.slug or not preview.name:
            raise ValueError(
                "zip is not a Solution workspace (missing bifrost.solution.yaml slug/name)"
            )

        solution = await _resolve_or_create_solution(
            db, slug=preview.slug, name=preview.name, organization_id=organization_id
        )

        # Build the bundle while the temp dir still exists (it reads Python +
        # app source fully into memory, so finalize_s3 is safe after teardown).
        bundle = _build_bundle(solution, preview, workspace)

        async with solution_write_lock(solution.id):
            deployer = SolutionDeployer(db)
            result = await deployer.deploy(bundle)
            await db.commit()
            # S3 only after the DB is durable; still inside the lock so finalize
            # can't race another writer.
            await result.finalize_s3()

            # STILL INSIDE THE LOCK, after finalize: apply provided config
            # values atomically with the deploy. A missing required value does
            # NOT block (warn-not-block) — we only set what was provided.
            if config_values:
                await _apply_config_values(
                    db,
                    solution=solution,
                    config_values=config_values,
                    deployer_email=deployer_email,
                )
                await db.commit()

    await db.refresh(solution)
    return solution


async def _apply_config_values(
    db: AsyncSession,
    *,
    solution: Solution,
    config_values: dict[str, Any],
    deployer_email: str,
) -> None:
    """Set instance Config values for ``solution``'s scope, typed from the just-
    deployed config DECLARATIONS (so a ``secret`` declaration is encrypted)."""
    from src.models.contracts.config import SetConfigRequest
    from src.repositories.config import ConfigRepository

    # Declaration type per key → the right ConfigType (secret → encrypted).
    decls = (
        await db.execute(
            select(SolutionConfigSchema.key, SolutionConfigSchema.type).where(
                SolutionConfigSchema.solution_id == solution.id
            )
        )
    ).all()
    type_by_key = {key: _config_type(type_) for key, type_ in decls}

    repo = ConfigRepository(db, org_id=solution.organization_id, is_superuser=True)
    for key, value in config_values.items():
        await repo.set_config(
            SetConfigRequest(
                key=key,
                value=str(value),
                type=type_by_key.get(key, ConfigType.STRING),
                organization_id=solution.organization_id,
            ),
            updated_by=deployer_email,
        )


def _config_type(raw: str | None) -> ConfigType:
    """Map a declaration's stored type string to a :class:`ConfigType`."""
    try:
        return ConfigType((raw or "string").lower())
    except ValueError:
        return ConfigType.STRING
