"""CLI command ``bifrost import`` — apply a bundle to the current env.

Implements Task 15 of the CLI mutation surface plan. Thin wrapper over
``POST /api/files/manifest/import`` that:

1. Validates the bundle: reads ``bundle.meta.yaml`` when present and
   enumerates ``.bifrost/*.yaml`` files.
2. Uploads workflow / app source files via ``POST /api/files/write`` first
   so FK references resolve before the manifest import runs.
3. POSTs the ``.bifrost/`` contents to the manifest-import endpoint with
   ``target_organization_id`` / ``role_resolution`` / ``dry_run`` /
   ``delete_removed_entities`` forwarded from flags.
4. Prints the server-side diff (adds / updates / deletes / warnings).

The filename is ``import_cmd.py`` to avoid colliding with the Python
keyword; :data:`handle_import` is wired into :func:`bifrost.cli.main`.
"""

from __future__ import annotations

import base64
import pathlib
import sys
from typing import Any
from uuid import UUID

import click
import yaml

from bifrost.client import BifrostClient

from .base import pass_resolver, run_async

# Top-level directories inside a bundle whose contents are pushed to the
# workspace before the manifest is imported. Mirrors ``_CODE_DIRS`` in
# :mod:`bifrost.commands.export` — a bundle is manifest + the code files
# the manifest references, nothing else.
_CODE_DIRS: tuple[str, ...] = ("workflows", "apps")


def _validate_bundle_dir(bundle_dir: pathlib.Path) -> pathlib.Path:
    """Assert ``bundle_dir`` exists and contains a ``.bifrost/`` tree.

    Returns the resolved path to the ``.bifrost/`` directory. A bundle
    without any manifest YAML files is treated as invalid — the whole
    point of ``bifrost import`` is to post the manifest.
    """
    if not bundle_dir.exists():
        raise click.ClickException(f"bundle directory does not exist: {bundle_dir}")
    if bundle_dir.is_symlink():
        raise click.ClickException(f"bundle directory must not be a symlink: {bundle_dir}")
    if not bundle_dir.is_dir():
        raise click.ClickException(f"bundle path is not a directory: {bundle_dir}")

    bifrost_dir = bundle_dir / ".bifrost"
    if bifrost_dir.is_symlink():
        raise click.ClickException(f"bundle .bifrost/ must not be a symlink: {bifrost_dir}")
    if not bifrost_dir.is_dir():
        raise click.ClickException(
            f"bundle directory missing .bifrost/ subdirectory: {bundle_dir}"
        )
    if not any(bifrost_dir.glob("*.yaml")):
        raise click.ClickException(
            f"bundle .bifrost/ contains no manifest YAML files: {bifrost_dir}"
        )
    return bifrost_dir


def _log_bundle_meta(bundle_dir: pathlib.Path) -> None:
    """Surface ``bundle.meta.yaml`` fields as an informational banner.

    A missing meta file is fine — bundles created by older tooling may not
    carry one. A version mismatch between the exporting CLI and the
    current CLI is logged but never fatal: the server accepts whatever
    manifest shape it understands and rejects the rest with a 422.
    """
    meta_path = bundle_dir / "bundle.meta.yaml"
    if not meta_path.is_file():
        click.echo("note: bundle.meta.yaml not present — assuming raw bundle", err=True)
        return

    try:
        meta = yaml.safe_load(meta_path.read_text()) or {}
    except Exception as exc:
        click.echo(f"warning: could not parse bundle.meta.yaml: {exc}", err=True)
        return

    if not isinstance(meta, dict):
        return

    source_env = meta.get("source_env") or "unknown"
    bifrost_version = meta.get("bifrost_version") or "unknown"
    portable = bool(meta.get("portable"))
    click.echo(
        f"bundle: source={source_env} bifrost-version={bifrost_version} "
        f"portable={'yes' if portable else 'no'}",
        err=True,
    )


#: Manifest files that carry cross-env-ambiguous seed data (the source
#: environment's org list and role list). Dropped from the uploaded
#: payload whenever the import is rebinding into a specific target org —
#: the server rejects ``target_organization_id`` alongside a non-empty
#: organizations section, and passing roles.yaml across environments
#: would try to upsert role rows by ID (mismatched between envs).
_CROSS_ENV_DROPPED_FILES: frozenset[str] = frozenset({
    "organizations.yaml",
    "roles.yaml",
})


def _read_manifest_files(
    bifrost_dir: pathlib.Path, *, drop_cross_env_seeds: bool
) -> dict[str, str]:
    """Build the ``{relative-path: base64-content}`` map for the manifest.

    Paths are relative to the bundle root (i.e. ``.bifrost/integrations.yaml``)
    so the server can strip the prefix and route them into its canonical
    S3 layout. When ``drop_cross_env_seeds`` is True, the organizations and
    roles files are skipped entirely — they carry source-env seed data
    that doesn't round-trip into a different target environment.
    """
    out: dict[str, str] = {}
    for yaml_path in sorted(bifrost_dir.glob("*.yaml")):
        if yaml_path.is_symlink():
            raise click.ClickException(f"bundle manifest file must not be a symlink: {yaml_path}")
        if drop_cross_env_seeds and yaml_path.name in _CROSS_ENV_DROPPED_FILES:
            continue
        raw = yaml_path.read_bytes()
        key = f".bifrost/{yaml_path.name}"
        out[key] = base64.b64encode(raw).decode("ascii")
    return out


def _collect_code_files(bundle_dir: pathlib.Path) -> dict[str, str]:
    """Walk ``workflows/`` and ``apps/`` in the bundle for pre-manifest upload.

    Excludes the same housekeeping patterns as :func:`_copy_code_tree` in
    the export command — ``__pycache__``, ``.pyc``, ``node_modules``,
    ``.venv``, and ``.git``. Returns ``{repo-relative-path: base64}``.
    """
    files: dict[str, str] = {}
    skip_parts = {"__pycache__", "node_modules", ".venv", ".git"}
    for top in _CODE_DIRS:
        root = bundle_dir / top
        if root.is_symlink():
            raise click.ClickException(f"bundle code directory must not be a symlink: {root}")
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise click.ClickException(f"bundle code path must not be a symlink: {path}")
            if path.is_dir():
                continue
            if any(part in skip_parts for part in path.parts):
                continue
            if path.suffix == ".pyc":
                continue
            rel = path.relative_to(bundle_dir).as_posix()
            raw = path.read_bytes()
            files[rel] = base64.b64encode(raw).decode("ascii")
    return files


async def _upload_code_files(
    client: BifrostClient, files: dict[str, str]
) -> list[str]:
    """POST each workflow / app source file via ``POST /api/files/write``.

    Returns the list of paths that failed so callers can decide whether to
    proceed (manifest import may still resolve if the missing files were
    already on the server) or abort.
    """
    errors: list[str] = []
    for repo_path, content in files.items():
        resp = await client.post(
            "/api/files/write",
            json={
                "path": repo_path,
                "content": content,
                "mode": "cloud",
                "location": "workspace",
                "binary": True,
            },
        )
        if resp.status_code != 204:
            errors.append(f"{repo_path}: HTTP {resp.status_code} {resp.text[:200]}")
    return errors


def _print_entity_changes(entity_changes: list[dict[str, Any]]) -> None:
    """Print the server's per-entity change list grouped by action.

    Falls back to "no entity changes" when the list is empty — which is
    the expected shape for a no-op round-trip import.
    """
    if not entity_changes:
        click.echo("  (no entity changes)")
        return

    by_action: dict[str, list[dict[str, Any]]] = {}
    for change in entity_changes:
        action = change.get("action", "unknown")
        by_action.setdefault(action, []).append(change)

    # Deterministic order: adds first, then updates, then deletes, then anything else.
    preferred = ["add", "update", "delete", "keep"]
    actions = [a for a in preferred if a in by_action] + sorted(
        a for a in by_action if a not in preferred
    )

    for action in actions:
        entries = by_action[action]
        click.echo(f"  {action} ({len(entries)}):")
        for entry in entries:
            entity_type = entry.get("entity_type", "?")
            name = entry.get("name", "?")
            org = entry.get("organization")
            suffix = f"  [{org}]" if org else ""
            click.echo(f"    - {entity_type}: {name}{suffix}")


def _print_response(response_body: dict[str, Any], *, dry_run: bool) -> None:
    """Print the server's ``ManifestImportResponse`` shape."""
    applied = bool(response_body.get("applied"))
    server_dry_run = bool(response_body.get("dry_run"))
    warnings = response_body.get("warnings") or []
    deleted = response_body.get("deleted_entities") or []
    entity_changes = response_body.get("entity_changes") or []

    if dry_run or server_dry_run:
        click.echo("Dry run — nothing written.")
    elif applied:
        click.echo("Manifest applied.")
    else:
        click.echo("Manifest import completed (no changes).")

    click.echo("Entity changes:")
    _print_entity_changes(entity_changes)

    if deleted:
        click.echo(f"Deleted entities ({len(deleted)}):")
        for did in deleted:
            click.echo(f"  - {did}")

    if warnings:
        click.echo(f"Warnings ({len(warnings)}):")
        for w in warnings:
            click.echo(f"  - {w}", err=True)


async def _post_import(
    client: BifrostClient,
    *,
    manifest_files: dict[str, str],
    dry_run: bool,
    delete_removed: bool,
    role_mode: str,
    target_org: UUID | None,
    entity_ids: set[str] | None = None,
) -> dict[str, Any]:
    """POST /api/files/manifest/import and return the parsed body.

    Raises click.ClickException on non-200. Shared between dry-run (diff)
    and apply (write) phases of the import pipeline.
    """
    payload: dict[str, Any] = {
        "files": manifest_files,
        "dry_run": dry_run,
        "delete_removed_entities": delete_removed,
        "role_resolution": role_mode,
    }
    if target_org is not None:
        payload["target_organization_id"] = str(target_org)
    if entity_ids is not None:
        payload["entity_ids"] = sorted(entity_ids)

    resp = await client.post("/api/files/manifest/import", json=payload)
    if resp.status_code != 200:
        # Surface the server body verbatim — 422 carries the specific rebinding
        # precondition that failed (orgs+target clash, unknown role, etc.).
        click.echo(f"HTTP {resp.status_code}", err=True)
        click.echo(resp.text, err=True)
        raise click.ClickException("manifest import failed")

    body = resp.json()
    if not isinstance(body, dict):
        raise click.ClickException(
            f"unexpected manifest/import response type: {type(body).__name__}"
        )
    return body


def _entity_changes_to_sync_items(entity_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build TUI sync-items from a server dry-run entity_changes list.

    Each item defaults to `push` (apply the change) and allows `skip` as the
    only alternative — import is inherently one-direction (bundle → env), so
    pull/delete don't apply. `keep` entries are filtered out since there's
    nothing to review.
    """
    items: list[dict[str, Any]] = []
    for change in entity_changes:
        action = change.get("action", "keep")
        if action == "keep":
            continue
        eid = change.get("id", "")
        entity_type = change.get("entity_type", "")
        name = change.get("name", "")
        org = change.get("organization", "Global")
        display = f"{entity_type}: {name}"
        if org and org != "Global":
            display += f" ({org})"

        why = {"add": "new", "update": "changed", "delete": "removed"}.get(action, action)
        items.append({
            "name": display,
            "why": why,
            "modified": "",
            "author": "",
            "default_action": "push",
            "valid_actions": ["push", "skip"],
            "section": "entities",
            "entity_id": eid,
            "entity_action": action,
        })
    return items


async def _import_impl(
    *,
    client: BifrostClient,
    bundle_dir: pathlib.Path,
    target_org: UUID | None,
    role_mode: str,
    dry_run: bool,
    delete_removed: bool,
    force: bool = False,
) -> dict[str, Any]:
    """Shared import pipeline usable from the Click callback and from tests.

    When stdin+stdout are a TTY and neither --dry-run nor --force is set,
    runs a server-side dry-run first, shows the diff in the sync TUI for
    per-entity push/skip selection, then applies only the selected entity
    IDs. --dry-run prints the diff and exits; --force or non-TTY applies
    the full diff without a review step.

    Returns the parsed server response body on success; raises
    :class:`click.ClickException` on any error path.
    """
    bifrost_dir = _validate_bundle_dir(bundle_dir)
    _log_bundle_meta(bundle_dir)

    code_files = _collect_code_files(bundle_dir)
    if code_files:
        click.echo(f"Uploading {len(code_files)} code file(s)…")
        errors = await _upload_code_files(client, code_files)
        if errors:
            for err in errors:
                click.echo(f"  error: {err}", err=True)
            raise click.ClickException("one or more code uploads failed")

    drop_cross_env_seeds = target_org is not None
    manifest_files = _read_manifest_files(
        bifrost_dir, drop_cross_env_seeds=drop_cross_env_seeds
    )
    click.echo(f"Importing manifest ({len(manifest_files)} file(s))…")

    interactive = (
        not dry_run
        and not force
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )

    # Non-interactive path: single call (dry-run preview OR force-apply-all).
    if not interactive:
        body = await _post_import(
            client,
            manifest_files=manifest_files,
            dry_run=dry_run,
            delete_removed=delete_removed,
            role_mode=role_mode,
            target_org=target_org,
        )
        _print_response(body, dry_run=dry_run)
        return body

    # Interactive path: dry-run → TUI cherry-pick → apply selected.
    preview = await _post_import(
        client,
        manifest_files=manifest_files,
        dry_run=True,
        delete_removed=delete_removed,
        role_mode=role_mode,
        target_org=target_org,
    )
    entity_changes = preview.get("entity_changes") or []
    sync_items = _entity_changes_to_sync_items(entity_changes)

    if not sync_items:
        click.echo("Already up to date.")
        return preview

    from bifrost.tui.sync_app import interactive_sync
    sync_result = await interactive_sync(
        sync_items,
        entity_count=len(sync_items),
        subtitle=f"Bundle: {bundle_dir.name}",
        title="bifrost import",
    )
    if sync_result is None:
        click.echo("Import cancelled.")
        return preview

    selected_ids = {i["entity_id"] for i in sync_result.push if i.get("entity_id")}
    if not selected_ids:
        click.echo("Nothing selected.")
        return preview

    body = await _post_import(
        client,
        manifest_files=manifest_files,
        dry_run=False,
        delete_removed=delete_removed,
        role_mode=role_mode,
        target_org=target_org,
        entity_ids=selected_ids,
    )
    _print_response(body, dry_run=False)
    return body


@click.group(name="import", help="Apply a bundle to the current environment.")
def import_group() -> None:
    """Top-level ``bifrost import`` group.

    Registered from :mod:`bifrost.cli` alongside ``export`` rather than
    through ``ENTITY_GROUPS`` — ``import`` is a workspace-level operation.
    """


@import_group.command("apply")
@click.argument(
    "bundle_dir",
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=pathlib.Path),
)
@click.option(
    "--org",
    "target_org",
    type=click.UUID,
    default=None,
    help="Target organization UUID. Required when the bundle is portable (role-mode name).",
)
@click.option(
    "--role-mode",
    type=click.Choice(["name", "uuid"], case_sensitive=False),
    default="name",
    help="How to interpret role references in the bundle (default: name).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report entity changes without writing.",
)
@click.option(
    "--delete-removed",
    is_flag=True,
    default=False,
    help="Delete entities present in the target DB but missing from the bundle.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the interactive review TUI and apply every change in the bundle.",
)
@click.pass_context
@pass_resolver
@run_async
async def import_apply(
    ctx: click.Context,  # noqa: ARG001 - required for @pass_resolver plumbing
    bundle_dir: pathlib.Path,
    *,
    target_org: UUID | None,
    role_mode: str,
    dry_run: bool,
    delete_removed: bool,
    force: bool,
    client: BifrostClient,
    resolver: Any,  # noqa: ARG001 - unused but required by pass_resolver
) -> None:
    """Apply ``BUNDLE_DIR`` to the current environment.

    Uploads workflow / app source files first, then POSTs ``.bifrost/``
    manifest contents to ``/api/files/manifest/import`` with the supplied
    rebinding flags. By default, shows an interactive TUI to review and
    cherry-pick the diff before applying; pass ``--force`` to skip review
    and apply everything, or ``--dry-run`` to print the diff and exit.
    Exits 1 with the server body on failure.
    """
    await _import_impl(
        client=client,
        bundle_dir=bundle_dir.resolve(),
        target_org=target_org,
        role_mode=role_mode.lower(),
        dry_run=dry_run,
        delete_removed=delete_removed,
        force=force,
    )


def handle_import(args: list[str]) -> int:
    """Dispatch ``bifrost import`` from :func:`bifrost.cli.main`.

    The top-level CLI calls ``bifrost import <bundle-dir>`` directly —
    shim the Click group by prepending the ``apply`` subcommand when the
    user didn't type it explicitly.
    """
    if not args or args[0] != "apply":
        args = ["apply", *args]
    try:
        import_group.main(
            args=args, standalone_mode=False, prog_name="bifrost import"
        )
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


__all__ = ["import_group", "handle_import"]
