"""CLI commands for managing applications.

Implements Task 5f of the CLI mutation surface plan plus the discovery
parity follow-up:

* ``bifrost apps list`` → ``GET /api/applications``
* ``bifrost apps get <ref>`` → ``GET /api/applications/{slug}``
  (the public GET endpoint is keyed by slug; UUID/name refs are resolved
  to a slug via :class:`RefResolver` then passed to the slug endpoint).
* ``bifrost apps create`` → ``POST /api/applications`` (body from
  :class:`ApplicationCreate`) with optional ``--deps @package.json`` triggering
  a follow-up ``PUT /api/applications/{id}/dependencies``.
* ``bifrost apps update <ref>`` → ``PATCH /api/applications/{uuid}`` (body from
  :class:`ApplicationUpdate`; unset flags omitted by :func:`assemble_body`).
  This is patch-without-draft per the audit — metadata is applied to the
  live application without a staging step.
* ``bifrost apps set-deps <ref>`` → ``PUT /api/applications/{uuid}/dependencies``
  with ``--deps @package.json`` (or a JSON literal).
* ``bifrost apps delete <ref>`` → ``DELETE /api/applications/{uuid}``.

``REF`` resolution supports slug, UUID, and name, handled by
:meth:`RefResolver.resolve` with kind ``"app"`` (slug is tried first via
``GET /api/applications/{slug}``, then falls back to name lookup).

The ``roles`` ↔ ``role_ids`` rename noted in the audit is a no-op here — the
DTO already names the field ``role_ids`` and the REST payload key matches, so
no :data:`DTO_FIELD_ALIASES` entry is required.

Two-call orchestration for ``apps create --deps``:

1. ``POST /api/applications`` with the :class:`ApplicationCreate` body.
2. If ``--deps`` was passed, ``PUT /api/applications/{id}/dependencies``
   with the parsed dependency dict.
3. On deps failure after create succeeded: print both the created app and
   the deps error, exit non-zero, and leave the app created (no rollback).
"""

from __future__ import annotations

from typing import Any

import click
import httpx

from bifrost.client import BifrostClient
from bifrost.dto_flags import (
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    assemble_body,
    build_cli_flags,
    load_dict_value,
)
from bifrost.refs import RefResolver
from bifrost.contracts import (
    ApplicationCreate,
    ApplicationUpdate,
)

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

apps_group = entity_group("apps", "Manage applications.")


_CREATE_FLAGS = build_cli_flags(
    ApplicationCreate,
    exclude=DTO_EXCLUDES.get("ApplicationCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("ApplicationCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    ApplicationUpdate,
    exclude=DTO_EXCLUDES.get("ApplicationUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("ApplicationUpdate", {}),
)


def _parse_deps(raw: str) -> dict[str, str]:
    """Parse ``--deps`` input into a ``{package: version}`` dict.

    Accepts:

    * ``@path/to/package.json`` — a package.json with a ``dependencies`` key,
      or a plain ``{name: version}`` object. When ``dependencies`` is present
      it is used; otherwise the top-level object is used as-is.
    * A JSON literal ``{"react": "^18.0.0"}``.

    All values are coerced to strings so the REST endpoint's
    ``dict[str, str]`` validator accepts them.
    """
    loaded = load_dict_value(raw)
    if loaded is None:
        raise click.BadParameter("--deps value cannot be empty")
    # package.json shape: {"dependencies": {...}, ...}
    nested = loaded.get("dependencies")
    if isinstance(nested, dict):
        return {str(k): str(v) for k, v in nested.items()}
    return {str(k): str(v) for k, v in loaded.items()}


@apps_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_apps(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all applications (wrapped ``{applications, total}`` payload)."""
    response = await client.get("/api/applications")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@apps_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_app(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single application by slug, UUID, or name.

    The public per-record endpoint is keyed by slug. For slug refs we hit
    ``GET /api/applications/{slug}`` directly. For UUID / name refs we
    resolve to a UUID then locate the matching record from the list payload
    so this command works with any ref shape :class:`RefResolver` accepts.
    """
    from uuid import UUID

    try:
        UUID(ref)
        is_uuid = True
    except (TypeError, ValueError):
        is_uuid = False

    if not is_uuid:
        # Try the slug endpoint first — it's a single round-trip and works
        # for the majority case where the user pastes a slug.
        slug_response = await client.get(f"/api/applications/{ref}")
        if slug_response.status_code == 200:
            output_result(slug_response.json(), ctx=ctx)
            return
        if slug_response.status_code not in (403, 404):
            slug_response.raise_for_status()

    # Fall through: resolve via name/UUID then locate in the list payload
    # since the per-record endpoint does not accept UUIDs.
    app_uuid = await resolver.resolve("app", ref)
    list_response = await client.get("/api/applications")
    list_response.raise_for_status()
    data = list_response.json()
    items = data.get("applications", []) if isinstance(data, dict) else data
    for item in items:
        if str(item.get("id")) == app_uuid:
            output_result(item, ctx=ctx)
            return
    raise click.ClickException(
        f"application {ref!r} resolved to {app_uuid} but is not in the accessible list"
    )


@apps_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@click.option(
    "--deps",
    "deps_raw",
    type=str,
    default=None,
    help=(
        "Dependencies as a JSON literal or @path to a package.json / "
        "{name: version} file. Triggers a follow-up PUT to /dependencies "
        "after the app is created."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def create_app(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    deps_raw: str | None,
    **fields: Any,
) -> None:
    """Create a new application, optionally seeding npm dependencies.

    ``--organization`` accepts a UUID or org name. ``--role-ids`` accepts
    repeated values or a comma-separated list; entries may be role names
    or UUIDs.

    When ``--deps`` is passed this runs as a two-call orchestration: the
    app is created first, then a ``PUT /dependencies`` applies the parsed
    dependency dict. If the deps call fails after the create succeeded,
    the command prints both the created app and the deps error, exits
    non-zero, and leaves the app in place — there is no rollback.
    """
    body = await assemble_body(ApplicationCreate, fields, resolver=resolver)
    response = await client.post("/api/applications", json=body)
    response.raise_for_status()
    created = response.json()

    if deps_raw is None:
        output_result(created, ctx=ctx)
        return

    deps = _parse_deps(deps_raw)
    app_id = created["id"]
    deps_response = await client.put(
        f"/api/applications/{app_id}/dependencies", json=deps
    )
    try:
        deps_response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Surface both outcomes. The app is created; don't roll back.
        error_body: Any
        try:
            error_body = deps_response.json()
        except ValueError:
            error_body = deps_response.text
        output_result(
            {
                "application": created,
                "dependencies_error": {
                    "status_code": deps_response.status_code,
                    "body": error_body,
                },
            },
            ctx=ctx,
        )
        raise exc

    output_result(
        {"application": created, "dependencies": deps_response.json()},
        ctx=ctx,
    )


@apps_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@click.option(
    "--deps",
    "deps_raw",
    type=str,
    default=None,
    help=(
        "Dependencies as a JSON literal or @path to a package.json / "
        "{name: version} file. Triggers a follow-up PUT to /dependencies "
        "after the metadata patch. Mirrors `apps create --deps`."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def update_app(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    deps_raw: str | None,
    **fields: Any,
) -> None:
    """Update application metadata (patch-without-draft).

    ``REF`` is a slug, UUID, or application name. Unset flags are omitted
    from the payload so the server only applies the fields the user
    explicitly passed. Per the audit this is PATCH directly on the live
    application — there's no draft-staging step.

    When ``--deps`` is passed this runs the same two-call orchestration
    as ``apps create --deps``: the metadata PATCH first, then a
    ``PUT /dependencies`` applies the parsed dict. If the deps call
    fails after the patch succeeded, the command prints both outcomes,
    exits non-zero, and leaves the metadata change in place — there is
    no rollback.
    """
    app_uuid = await resolver.resolve("app", ref)
    body = await assemble_body(ApplicationUpdate, fields, resolver=resolver)
    response = await client.patch(f"/api/applications/{app_uuid}", json=body)
    response.raise_for_status()
    updated = response.json()

    if deps_raw is None:
        output_result(updated, ctx=ctx)
        return

    deps = _parse_deps(deps_raw)
    deps_response = await client.put(
        f"/api/applications/{app_uuid}/dependencies", json=deps
    )
    try:
        deps_response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        error_body: Any
        try:
            error_body = deps_response.json()
        except ValueError:
            error_body = deps_response.text
        output_result(
            {
                "application": updated,
                "dependencies_error": {
                    "status_code": deps_response.status_code,
                    "body": error_body,
                },
            },
            ctx=ctx,
        )
        raise exc

    output_result(
        {"application": updated, "dependencies": deps_response.json()},
        ctx=ctx,
    )


@apps_group.command("set-deps")
@click.argument("ref")
@click.option(
    "--deps",
    "deps_raw",
    type=str,
    required=True,
    help=(
        "Dependencies as a JSON literal or @path to a package.json / "
        "{name: version} file."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def set_deps(
    ctx: click.Context,
    ref: str,
    deps_raw: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Replace an application's npm dependencies.

    ``REF`` is a slug, UUID, or application name. The ``--deps`` value is
    either a JSON object literal or ``@path/to/package.json``; package.json's
    ``dependencies`` key is extracted automatically.
    """
    app_uuid = await resolver.resolve("app", ref)
    deps = _parse_deps(deps_raw)
    response = await client.put(
        f"/api/applications/{app_uuid}/dependencies", json=deps
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@apps_group.command("delete")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def delete_app(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete an application.

    ``REF`` is a slug, UUID, or application name.
    """
    app_uuid = await resolver.resolve("app", ref)
    response = await client.delete(f"/api/applications/{app_uuid}")
    response.raise_for_status()
    output_result({"deleted": app_uuid}, ctx=ctx)


@apps_group.command("replace")
@click.argument("ref")
@click.option(
    "--repo-path",
    "repo_path",
    required=True,
    type=str,
    help="Workspace-relative path to the new source directory (e.g. apps/my-app-v2).",
)
@click.option(
    "--force",
    "force",
    is_flag=True,
    default=False,
    help=(
        "Bypass the uniqueness, nesting, and source-exists checks. "
        "Use when repointing before files are pushed."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def replace_app(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    repo_path: str,
    force: bool,
) -> None:
    """Repoint an application's source directory.

    ``REF`` is a slug, UUID, or application name. ``--repo-path`` must be
    the workspace-relative path to the new source directory. By default the
    path must already contain files; ``--force`` bypasses that check (and
    uniqueness / nesting checks) for repointing ahead of a push.
    """
    app_uuid = await resolver.resolve("app", ref)
    body: dict[str, Any] = {"repo_path": repo_path, "force": force}
    response = await client.post(
        f"/api/applications/{app_uuid}/replace", json=body
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["apps_group"]
