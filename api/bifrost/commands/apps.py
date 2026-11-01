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
* ``bifrost apps publish <ref>`` → enqueue a durable rebuild/publish operation
  and poll its short status requests to a terminal result.
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

import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

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
from bifrost.platform_jobs import poll_platform_job
from bifrost.refs import RefResolver
from bifrost.contracts import (
    ApplicationCreate,
    ApplicationUpdate,
)

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

apps_group = entity_group("apps", "Manage applications.")
APPLICATION_PUBLISH_TIMEOUT_SECONDS = 20 * 60


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


def _select_bound_app(
    items: list[dict[str, Any]], ref: str, solution_id: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Pick the bound install's own app for ``ref`` (slug, or name
    case-insensitively), plus any OTHER apps the ref also matches — so the
    caller can warn that a generic ref ("portal") is ambiguous across
    scopes instead of silently resolving an unrelated global app."""

    def _matches(item: dict[str, Any]) -> bool:
        return item.get("slug") == ref or (
            str(item.get("name") or "").lower() == ref.lower()
        )

    matches = [i for i in items if _matches(i)]
    own = [i for i in matches if str(i.get("solution_id") or "") == solution_id]
    foreign = [i for i in matches if i.get("solution_id") is None]
    return (own[0] if own else None), foreign


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

    Inside a BOUND solution workspace (BIFROST_SOLUTION_ID set), the
    install's OWN apps are preferred: a generic ref like "portal" must not
    silently resolve an unrelated global app when the workspace's app
    matches by slug or name.
    """
    import os
    from uuid import UUID

    try:
        UUID(ref)
        is_uuid = True
    except (TypeError, ValueError):
        is_uuid = False

    bound_solution = os.getenv("BIFROST_SOLUTION_ID")
    if not is_uuid and bound_solution:
        list_response = await client.get("/api/applications")
        list_response.raise_for_status()
        data = list_response.json()
        items = data.get("applications", []) if isinstance(data, dict) else data
        own, foreign = _select_bound_app(items, ref, bound_solution)
        if own is not None:
            for other in foreign:
                click.echo(
                    f"Warning: {ref!r} also matches app {other.get('slug')!r} "
                    f"({other.get('id')}) outside this solution — returning this "
                    "workspace's own app. Use the UUID to target the other one.",
                    err=True,
                )
            output_result(own, ctx=ctx)
            return

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
    response = await client.put(f"/api/applications/{app_uuid}/dependencies", json=deps)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@apps_group.command("publish")
@click.argument("ref")
@click.option(
    "--message",
    type=str,
    default=None,
    help="Optional publish message (maximum 500 characters).",
)
@click.pass_context
@pass_resolver
@run_async
async def publish_app(
    ctx: click.Context,
    ref: str,
    message: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Rebuild and publish an application, polling durable progress.

    ``REF`` is a slug, UUID, or application name. The enqueue request returns
    quickly; this command then polls short status requests, so a multi-minute
    build cannot hit the client's per-request 30-second timeout.
    """
    app_uuid = await resolver.resolve("app", ref)
    try:
        response = await client.post(
            f"/api/applications/{app_uuid}/publish",
            json={"message": message} if message else {},
        )
    except httpx.TimeoutException as exc:
        raise click.ClickException(
            "Timed out while queueing the application publish. The request may "
            "have been accepted; retrying is safe and will follow the existing "
            "active publish job."
        ) from exc
    response.raise_for_status()
    enqueued = response.json()
    job_id = str(enqueued["job_id"])
    reused = bool(enqueued.get("reused"))
    click.echo(
        f"{'Following existing' if reused else 'Queued'} publish job {job_id}",
        err=True,
    )
    completed = await poll_platform_job(
        client,
        job_id,
        label="Publish",
        failure_label="Application publish",
        timeout_seconds=APPLICATION_PUBLISH_TIMEOUT_SECONDS,
        timeout_operation="application publish",
    )
    output_result(completed, ctx=ctx)


def _app_sdk_status(app: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": app.get("id"),
        "slug": app.get("slug"),
        "name": app.get("name"),
        "app_model": app.get("app_model"),
        "sdk_package_version": app.get("sdk_package_version"),
        "sdk_fingerprint": app.get("sdk_fingerprint"),
        "sdk_contract_version": app.get("sdk_contract_version"),
        "sdk_binding_api_major": app.get("sdk_binding_api_major"),
        "sdk_built_at": app.get("sdk_built_at"),
        "sdk_status": app.get("sdk_status"),
        "sdk_source_available": app.get("sdk_source_available"),
    }


async def _load_app_by_ref(
    client: BifrostClient, resolver: RefResolver, ref: str
) -> dict[str, Any]:
    bound_solution = os.getenv("BIFROST_SOLUTION_ID")
    try:
        UUID(ref)
    except (TypeError, ValueError):
        is_uuid = False
    else:
        is_uuid = True

    if bound_solution and not is_uuid:
        list_response = await client.get("/api/applications")
        list_response.raise_for_status()
        data = list_response.json()
        items = data.get("applications", []) if isinstance(data, dict) else data
        own, foreign = _select_bound_app(items, ref, bound_solution)
        if own is not None:
            for other in foreign:
                click.echo(
                    f"Warning: {ref!r} also matches app {other.get('slug')!r} "
                    f"({other.get('id')}) outside this solution — targeting this "
                    "workspace's own app. Use the UUID to target the other one.",
                    err=True,
                )
            return own
        if foreign:
            raise click.ClickException(
                f"{ref!r} matches an app outside this bound Solution, but no "
                "app in this Solution. Use the UUID outside a bound Solution "
                "workspace if you intend to target that app."
            )

    app_uuid = await resolver.resolve("app", ref)
    response = await client.get("/api/applications")
    response.raise_for_status()
    data = response.json()
    items = data.get("applications", []) if isinstance(data, dict) else data
    for item in items:
        if str(item.get("id")) == app_uuid:
            return item
    raise click.ClickException(
        f"application {ref!r} resolved to {app_uuid} but is not in the accessible list"
    )


@apps_group.group("sdk")
def sdk_group() -> None:
    """Inspect or update deployed App SDK bundles."""


@sdk_group.command("status")
@click.argument("ref", required=False)
@click.pass_context
@pass_resolver
@run_async
async def sdk_status(
    ctx: click.Context,
    ref: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Show SDK provenance/status for one deployed App, or all visible Apps."""
    if ref:
        app = await _load_app_by_ref(client, resolver, ref)
        output_result(_app_sdk_status(app), ctx=ctx)
        return

    response = await client.get("/api/applications")
    response.raise_for_status()
    data = response.json()
    items = data.get("applications", []) if isinstance(data, dict) else data
    output_result(
        {"applications": [_app_sdk_status(app) for app in items], "total": len(items)},
        ctx=ctx,
    )


@sdk_group.command("update")
@click.argument("ref", required=False)
@click.option(
    "--all", "all_apps", is_flag=True, help="Update every actionable visible App."
)
@click.pass_context
@pass_resolver
@run_async
async def sdk_update(
    ctx: click.Context,
    ref: str | None,
    all_apps: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Queue deployed App SDK rebuild/update jobs and follow their progress."""
    if bool(ref) == all_apps:
        raise click.UsageError("Pass either REF or --all.")

    if ref:
        app = await _load_app_by_ref(client, resolver, ref)
        app_uuid = str(app["id"])
        response = await client.post(f"/api/applications/{app_uuid}/sdk/update")
        response.raise_for_status()
        accepted = response.json()
        job_id = str(accepted["job_id"])
        click.echo(f"Queued App SDK update job {job_id}", err=True)
        completed = await poll_platform_job(
            client,
            job_id,
            label="SDK update",
            failure_label="App SDK update",
        )
        output_result({"accepted": accepted, "job": completed}, ctx=ctx)
        return

    response = await client.post(
        "/api/applications/sdk/update", json={"application_ids": None}
    )
    response.raise_for_status()
    body = response.json()
    accepted_jobs = body.get("accepted", [])
    skipped = body.get("skipped", [])
    for item in skipped:
        click.echo(
            f"Skipped {item.get('application_id')}: {item.get('reason')}",
            err=True,
        )

    completed_jobs: list[dict[str, Any]] = []
    failed_jobs: list[str] = []
    for item in accepted_jobs:
        job_id = str(item["job_id"])
        click.echo(f"Queued App SDK update job {job_id}", err=True)
        try:
            completed_jobs.append(
                await poll_platform_job(
                    client,
                    job_id,
                    label="SDK update",
                    failure_label="App SDK update",
                )
            )
        except click.ClickException as exc:
            failed_jobs.append(f"{job_id}: {exc.message}")

    output_result(
        {"accepted": accepted_jobs, "skipped": skipped, "jobs": completed_jobs},
        ctx=ctx,
    )
    if failed_jobs:
        raise click.ClickException(
            f"{len(failed_jobs)} App SDK update job(s) failed: "
            + "; ".join(failed_jobs)
        )


@apps_group.group("source")
def source_group() -> None:
    """Download retained deployed App source artifacts."""


@source_group.command("export")
@click.argument("ref")
@click.argument("destination", type=click.Path(dir_okay=False, path_type=Path))
@click.pass_context
@pass_resolver
@run_async
async def source_export(
    ctx: click.Context,
    ref: str,
    destination: Path,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Write the retained deployed source zip for an independent App."""
    if destination.exists():
        if destination.is_dir():
            raise click.ClickException(f"Destination is a directory: {destination}")
        if destination.stat().st_size > 0:
            raise click.ClickException(
                f"Refusing to overwrite non-empty destination: {destination}"
            )

    app = await _load_app_by_ref(client, resolver, ref)
    app_uuid = str(app["id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    bytes_written = 0
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            async with client.stream(
                "GET", f"/api/applications/{app_uuid}/source"
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    tmp_file.write(chunk)
                    bytes_written += len(chunk)
        tmp_path.replace(destination)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    output_result({"path": str(destination), "bytes": bytes_written}, ctx=ctx)


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
    response = await client.post(f"/api/applications/{app_uuid}/replace", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["apps_group"]
