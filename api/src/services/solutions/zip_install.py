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
import logging
import os
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from src.services.solutions.secrets_blob import SolutionContent

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solutions import (
    SolutionConfigSchemaChange,
    SolutionConfigSchemaState,
    SolutionEntityDiff,
    SolutionUpgradeDiff,
)
from src.models.enums import ConfigType
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    solution_entity_id,
)

logger = logging.getLogger(__name__)


class GitConnectedInstallError(Exception):
    """Zip-install targeted an install whose only writer is git auto-pull.

    A git-connected install has exactly one writer (auto-pull from its repo); a
    zip install would full-replace it out of band and violate that invariant.
    Mapped to 409 by the endpoint, mirroring ``deploy_solution``'s refusal."""


class BadExportPassword(ValueError):
    """Wrong or missing password for a full-backup zip that carries secrets.enc.

    Mapped to 422 by the endpoint: the caller must supply the correct password
    before the import can proceed. Nothing is written on this path."""


class ContentCollision(ValueError):
    """Import would overwrite existing config values or table data.

    Raised when a full-backup zip contains values for keys that already have
    a Config row in the target org and the caller has not set replace_secrets
    (for config values) or replace_data (for table data).  Named so the caller
    can report exactly which keys collide.
    """

    def __init__(self, keys: list[str], tables: list[str] | None = None) -> None:
        self.keys = keys
        self.tables = tables or []
        parts: list[str] = []
        if keys:
            parts.append("config values: " + ", ".join(sorted(keys)))
        if self.tables:
            parts.append("table data: " + ", ".join(sorted(self.tables)))
        super().__init__(
            "Import would overwrite existing "
            + "; ".join(parts)
            + ". Re-run with replace to overwrite."
        )


@dataclass
class PreviewResult:
    """What a zip would create — parse-only, nothing persisted."""

    slug: str | None = None
    name: str | None = None
    scope: str | None = None
    version: str | None = None
    # Descriptor ``logo:`` path (workspace-relative); read by _build_bundle.
    logo: str | None = None
    workflows: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    apps: list[dict[str, Any]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
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
        _collect_claims,
        _collect_config_schemas,
        _collect_forms,
        _collect_tables,
        _collect_workflows,
    )
    from bifrost.solution_descriptor import is_solution_workspace, load_descriptor

    slug: str | None = None
    name: str | None = None
    scope: str | None = None
    version: str | None = None
    logo: str | None = None
    if is_solution_workspace(workspace):
        descriptor = load_descriptor(workspace)
        slug, name, scope = descriptor.slug, descriptor.name, descriptor.scope
        version = descriptor.version
        logo = descriptor.logo

    return PreviewResult(
        slug=slug,
        name=name,
        scope=scope,
        version=version,
        logo=logo,
        workflows=_collect_workflows(workspace),
        tables=_collect_tables(workspace),
        apps=_collect_apps(workspace),
        forms=_collect_forms(workspace),
        agents=_collect_agents(workspace),
        claims=_collect_claims(workspace),
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


# Preview entity types ↔ SolutionUpgradeDiff sections (same attribute names).
_DIFF_ENTITY_TYPES = ("workflows", "tables", "forms", "agents", "claims", "apps")


def compute_upgrade_diff(
    preview: PreviewResult,
    *,
    install_id: UUID,
    installed: Mapping[str, Sequence[tuple[UUID, str]]],
    installed_config_schemas: Sequence[tuple[str, str, bool]],
) -> SolutionUpgradeDiff:
    """Diff a parsed zip against an existing install's solution-owned rows.

    Pure function (no DB/S3) so it is unit-testable. Identity matching mirrors
    what deploy will actually do — the deployer rewrites every manifest id to
    ``uuid5(install_id, manifest_id)`` (:func:`solution_entity_id`), so:

    * manifest entry whose remapped id exists on the install → kept (unlisted)
    * manifest entry whose remapped id is absent → ``added``
    * install row whose id matches no remapped manifest id → ``removed``

    Reported by display name, falling back to the id. ``installed`` maps entity
    type (``workflows``/``tables``/``forms``/``agents``/``apps``) to the
    install's current ``(id, name)`` rows; ``installed_config_schemas`` is the
    install's ``(key, type, required)`` declarations, compared by key.
    """
    diff = SolutionUpgradeDiff()
    for etype in _DIFF_ENTITY_TYPES:
        entries: list[dict[str, Any]] = getattr(preview, etype)
        rows = installed.get(etype, ())
        remapped_names: dict[UUID, str] = {}
        for entry in entries:
            manifest_id = UUID(str(entry["id"]))
            name = str(entry.get("name") or entry["id"])
            remapped_names[solution_entity_id(install_id, manifest_id)] = name
        row_ids = {row_id for row_id, _ in rows}
        section: SolutionEntityDiff = getattr(diff, etype)
        section.added = [
            name for rid, name in remapped_names.items() if rid not in row_ids
        ]
        section.removed = [
            name or str(row_id)
            for row_id, name in rows
            if row_id not in remapped_names
        ]

    bundle_decls = {
        str(entry["key"]): SolutionConfigSchemaState(
            type=str(entry.get("type") or "string"),
            required=bool(entry.get("required", False)),
        )
        for entry in preview.config_schemas
    }
    installed_decls = {
        key: SolutionConfigSchemaState(type=type_, required=required)
        for key, type_, required in installed_config_schemas
    }
    cfg = diff.config_schemas
    cfg.added = [k for k in bundle_decls if k not in installed_decls]
    cfg.removed = [k for k in installed_decls if k not in bundle_decls]
    cfg.changed = [
        SolutionConfigSchemaChange(key=k, from_=installed_decls[k], to=bundle_decls[k])
        for k in bundle_decls
        if k in installed_decls and bundle_decls[k] != installed_decls[k]
    ]
    return diff


def _build_bundle(solution: Solution, preview: PreviewResult, workspace: Path) -> SolutionBundle:
    """Build the full deploy bundle from a parsed workspace.

    ``preview`` already holds the manifest entities; only the Python source has
    to be read here (it is not part of the parse-only preview shape)."""
    import base64

    from bifrost.commands.solution import _LOGO_CONTENT_TYPES, _collect_python_files

    logo_b64: str | None = None
    logo_content_type: str | None = None
    if preview.logo:
        logo_file = workspace / preview.logo
        if not logo_file.is_file():
            raise ValueError(f"solution logo file not found in zip: {preview.logo}")
        logo_b64 = base64.b64encode(logo_file.read_bytes()).decode("ascii")
        logo_content_type = _LOGO_CONTENT_TYPES.get(logo_file.suffix.lower())

    return SolutionBundle(
        solution=solution,
        python_files=_collect_python_files(workspace),
        workflows=preview.workflows,
        tables=preview.tables,
        apps=preview.apps,
        forms=preview.forms,
        agents=preview.agents,
        claims=preview.claims,
        config_schemas=preview.config_schemas,
        version=preview.version,
        logo_b64=logo_b64,
        logo_content_type=logo_content_type,
    )


async def find_install(
    db: AsyncSession, *, slug: str, organization_id: UUID | None
) -> Solution | None:
    """Find the install for ``(slug, organization_id)`` — the EXACT match rule
    ``_resolve_or_create_solution`` uses (each org's install of a slug is
    independent, criterion 9; ``None`` org == global NULL scope). Read-only."""
    if organization_id is not None:
        q = select(Solution).where(
            Solution.slug == slug, Solution.organization_id == organization_id
        )
    else:
        q = select(Solution).where(
            Solution.slug == slug, Solution.organization_id.is_(None)
        )
    return (await db.execute(q)).scalars().first()


async def _resolve_or_create_solution(
    db: AsyncSession, *, slug: str, name: str, organization_id: UUID | None
) -> Solution:
    """Find the install for ``(slug, organization_id)`` or create a fresh one.

    Exact-match resolve-or-create (a simplification of the CLI's
    ``_resolve_target_install``, which also guards cross-org ambiguity): each
    org's install of a slug is independent (criterion 9), so we match within the
    requested scope only and create when none exists.
    """
    existing = await find_install(db, slug=slug, organization_id=organization_id)
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
    force: bool = False,
    password: str | None = None,
    replace_secrets: bool = False,
    replace_data: bool = False,
) -> Solution:
    """Atomically install a Solution zip: deploy the bundle, then apply config
    VALUES — all under the per-install write lock.

    Mirrors the proven ``deploy_solution`` shape: lock → deploy → commit →
    finalize_s3 (S3 only after the DB is durable; still inside the lock). The
    provided config values are written AFTER finalize but BEFORE the lock is
    released, so the install never exists without its just-entered secrets.

    Full-backup zips carry a ``.bifrost/secrets.enc`` blob.  Decryption and
    collision checking happen INSIDE the write lock, BEFORE deploy, so a bad
    password or collision refuses the import atomically — nothing lands.

    Raises:
        BadExportPassword: wrong/missing password for a secrets blob.
        ContentCollision: blob values collide with existing Config rows and the
            replace flag is not set.  Caller maps this to 409.
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

        # One-writer invariant: a git-connected install is written ONLY by
        # auto-pull (sync). Refuse a zip install into it, exactly as
        # deploy_solution refuses a manual deploy — otherwise the zip would
        # full-replace the connected install out of band.
        if solution.git_connected:
            raise GitConnectedInstallError(
                "This install is git-connected; zip install is disabled "
                "(auto-pull is the only writer)."
            )

        # Build the bundle while the temp dir still exists (it reads Python +
        # app source fully into memory, so finalize_s3 is safe after teardown).
        bundle = _build_bundle(solution, preview, workspace)

        async with solution_write_lock(solution.id):
            # Decrypt + collision-check BEFORE deploy so a bad password or
            # collision refuses the entire import atomically — nothing lands.
            content = None
            secrets_path = workspace / ".bifrost" / "secrets.enc"
            if secrets_path.exists():
                if not password:
                    raise BadExportPassword(
                        "this bundle carries secrets — a password is required"
                    )
                from cryptography.fernet import InvalidToken

                from src.services.solutions.secrets_blob import decode_secrets_blob

                try:
                    content = decode_secrets_blob(
                        secrets_path.read_text(), password=password
                    )
                except InvalidToken as exc:
                    raise BadExportPassword(
                        "wrong password for this bundle"
                    ) from exc

                await _assert_no_unforced_collisions(
                    db,
                    solution=solution,
                    content=content,
                    replace_secrets=replace_secrets,
                    replace_data=replace_data,
                )

            deployer = SolutionDeployer(db)
            result = await deployer.deploy(bundle, force=force)
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

            # Apply the decrypted content from the secrets blob (config values
            # only — table data apply is Phase 4).
            if content is not None:
                await _apply_content(
                    db,
                    solution=solution,
                    content=content,
                    replace_secrets=replace_secrets,
                    replace_data=replace_data,
                    deployer_email=deployer_email,
                )
                await db.commit()

            # Recompute and persist setup_complete after every install so the
            # column reflects whether all required configs have values — even
            # when no config_values were provided (empty install of a solution
            # with required declarations must be marked incomplete).
            from src.services.solutions.setup_status import compute_setup_status

            status_now = await compute_setup_status(db, solution)
            solution.setup_complete = status_now.setup_complete
            await db.commit()

    await db.refresh(solution)
    return solution


async def _assert_no_unforced_collisions(
    db: AsyncSession,
    *,
    solution: Solution,
    content: SolutionContent,
    replace_secrets: bool,
    replace_data: bool,
) -> None:
    """Pure collision check — no writes.

    For config values: a key that ALREADY has a Config row in the solution's
    org scope is a collision.  If any collide and replace_secrets is False,
    raise ContentCollision naming all of them so the caller can report them.

    Table data: Phase 4 — threaded through but not implemented here.
    """
    from src.models.orm.config import Config

    colliding_keys: list[str] = []

    if content.config_values and not replace_secrets:
        org_pred = (
            Config.organization_id == solution.organization_id
            if solution.organization_id is not None
            else Config.organization_id.is_(None)
        )
        existing_q = (
            select(Config.key)
            .where(org_pred)
            .where(Config.key.in_(list(content.config_values.keys())))
            # Only consider non-orphaned rows; orphaned rows are reattached,
            # not counted as collisions.
            .where(Config.orphaned_at.is_(None))
        )
        existing_keys = set((await db.execute(existing_q)).scalars().all())
        colliding_keys = [k for k in content.config_values if k in existing_keys]

    # Table data collision: Phase 4 — skip for now, replace_data threaded.
    # TODO(Phase 4): check table_data collisions when replace_data is False.

    if colliding_keys:
        raise ContentCollision(keys=colliding_keys)


async def _apply_content(
    db: AsyncSession,
    *,
    solution: Solution,
    content: SolutionContent,
    replace_secrets: bool,
    replace_data: bool,
    deployer_email: str,
) -> None:
    """Apply decrypted content (config values) from a full-backup zip.

    Config values arrive as DECRYPTED plaintext from the blob; _apply_config_values
    will re-encrypt secrets at rest when the declaration type is SECRET.

    Collision contract: _assert_no_unforced_collisions already ran and passed.
    With replace_secrets=True, existing values are overwritten (set_config upserts).
    With replace_secrets=False, only empty slots are filled — but that check
    already passed in _assert_no_unforced_collisions, so all keys are safe.

    Table data: Phase 4 — not implemented here.
    """
    if content.config_values:
        # All values in content.config_values are either new (no existing row)
        # or allowed to overwrite (replace_secrets=True).  _apply_config_values
        # upserts unconditionally, which is correct for both cases.
        await _apply_config_values(
            db,
            solution=solution,
            config_values=dict(content.config_values),
            deployer_email=deployer_email,
        )

    # Table data: Phase 4 — threaded through, not implemented.
    if content.table_data and replace_data:
        # TODO(Phase 4): write table rows when replace_data is True.
        pass


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
    type_by_key = {key: _config_type(type_, key=key) for key, type_ in decls}

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


def _config_type(raw: str | None, *, key: str) -> ConfigType:
    """Map a declaration's stored type string to a :class:`ConfigType`.

    An absent type defaults to STRING silently. An UNRECOGNIZED non-empty type
    is also downgraded to STRING — but logged, because a mistyped ``secret``
    would otherwise store its value as PLAINTEXT with no signal."""
    if not raw:
        return ConfigType.STRING
    try:
        return ConfigType(raw.lower())
    except ValueError:
        logger.warning(
            "Config declaration %r has unrecognized type %r; storing its value "
            "as STRING (a mistyped 'secret' would NOT be encrypted).",
            key,
            raw,
        )
        return ConfigType.STRING
