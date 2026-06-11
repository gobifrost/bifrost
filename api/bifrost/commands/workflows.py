"""CLI commands for managing workflows.

Implements Task 5c of the CLI mutation surface plan:

* ``bifrost workflows list`` → ``GET /api/workflows``
* ``bifrost workflows get <ref>`` — list-and-filter (the server does not
  expose ``GET /api/workflows/{uuid}``; the resolver is used to derive the
  UUID, then the row is located in the list payload).
* ``bifrost workflows register`` → ``POST /api/workflows/register`` (registers
  a decorated function from an existing workspace ``.py`` file).
* ``bifrost workflows execute <ref>`` → ``POST /api/workflows/execute`` plus
  WebSocket tail of ``/ws/execution/{id}`` so logs stream as the workflow
  runs and the command exits when the execution reaches a terminal status.
* ``bifrost workflows update <ref>`` → ``PATCH /api/workflows/{uuid}`` (body
  from :class:`WorkflowUpdateRequest`).
* ``bifrost workflows delete <ref>`` → ``DELETE /api/workflows/{uuid}``
  (forwards ``--force`` as ``force_deactivation`` on the request body).
* ``bifrost workflows grant-role <ref> <role-ref>`` →
  ``POST /api/workflows/{uuid}/roles`` with a single-element role_ids list.
* ``bifrost workflows revoke-role <ref> <role-ref>`` →
  ``DELETE /api/workflows/{uuid}/roles/{role_uuid}``.

Ref resolution uses :class:`RefResolver`:
- ``workflow`` refs accept UUID, name, or ``path::func``.
- ``role`` refs accept UUID or name.

DTO-driven flags for ``update`` are generated from
:class:`WorkflowUpdateRequest` via :func:`build_cli_flags` with the exclude
list in :data:`DTO_EXCLUDES` (UI / code-defined fields that intentionally do
not surface on the CLI — see the plan's cross-cutting exclusion rationale).

Partial-failure handling (plan open Q #3): ``grant-role`` and ``revoke-role``
each act on a single role per invocation. Failures surface via the standard
HTTP error path (exit 1 with the server body on stderr). A future
``update --roles foo,bar,baz`` diff-and-apply command can reuse this
contract by iterating role refs and aggregating per-role outcomes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.dto_flags import (
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    assemble_body,
    build_cli_flags,
)
from bifrost.refs import RefResolver
from bifrost.contracts import WorkflowUpdateRequest

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async


_TERMINAL_STATUSES = frozenset(
    {"Success", "Failed", "CompletedWithErrors", "Timeout", "Cancelled"}
)

workflows_group = entity_group("workflows", "Manage workflows.")


_UPDATE_FLAGS = build_cli_flags(
    WorkflowUpdateRequest,
    exclude=DTO_EXCLUDES.get("WorkflowUpdateRequest", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("WorkflowUpdateRequest", {}),
)


@workflows_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_workflows(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all workflows visible to the caller."""
    response = await client.get("/api/workflows")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@workflows_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_workflow(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single workflow by UUID, name, or ``path::func`` ref.

    The server does not expose a per-record GET endpoint for workflows, so
    this resolves the ref via :class:`RefResolver` and locates the entry in
    the ``GET /api/workflows`` list payload.
    """
    workflow_uuid = await resolver.resolve("workflow", ref)
    list_response = await client.get("/api/workflows")
    list_response.raise_for_status()
    items = list_response.json()
    for item in items:
        if str(item.get("id")) == workflow_uuid:
            output_result(item, ctx=ctx)
            return
    raise click.ClickException(
        f"workflow {ref!r} resolved to {workflow_uuid} but is not in the accessible list"
    )


@workflows_group.command("register")
@click.option(
    "--path",
    "path",
    required=True,
    type=str,
    help="Workspace-relative path to the .py file containing the decorated function.",
)
@click.option(
    "--function-name",
    "function_name",
    required=True,
    type=str,
    help="Name of the decorated function to register.",
)
@click.option(
    "--org",
    "organization_id",
    type=str,
    default=None,
    help="Organization ref (UUID or name) to scope the workflow to; omit for global.",
)
@click.option(
    "--access-level",
    "access_level",
    type=click.Choice(["authenticated", "everyone", "role_based"]),
    default=None,
    help="Access level for the workflow. Omit to leave at default.",
)
@click.option(
    "--role-ids",
    "role_ids",
    type=str,
    multiple=True,
    help=(
        "Role refs (UUID or name) for role_based access. Repeat the flag for "
        "multiple, or pass a comma-separated list."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def register_workflow(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    path: str,
    function_name: str,
    organization_id: str | None,
    access_level: str | None,
    role_ids: tuple[str, ...],
) -> None:
    """Register a decorated function from an existing workspace ``.py`` file.

    The file must already exist in the workspace (written via ``bifrost push``
    or the file editor). This command indexes a ``@workflow`` / ``@tool`` /
    ``@data_provider`` function so it becomes executable via the API.

    ``--access-level`` and ``--role-ids`` set the workflow's access controls at
    registration time, mirroring the create-time surface for forms and apps.
    Role refs accept names or UUIDs and are resolved before the request.
    """
    body: dict[str, Any] = {"path": path, "function_name": function_name}
    if organization_id is not None:
        body["organization_id"] = await resolver.resolve("org", organization_id)
    if access_level is not None:
        body["access_level"] = access_level

    # Flatten comma-separated role refs and resolve names → UUIDs.
    flattened: list[str] = []
    for raw in role_ids:
        for piece in raw.split(","):
            piece = piece.strip()
            if piece:
                flattened.append(piece)
    if flattened:
        body["role_ids"] = [await resolver.resolve("role", r) for r in flattened]

    response = await client.post("/api/workflows/register", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@workflows_group.command("execute")
@click.argument("ref")
@click.option(
    "--params",
    "params",
    type=str,
    default=None,
    help="JSON object of input parameters (e.g. --params '{\"name\":\"World\"}').",
)
@click.option(
    "--params-file",
    "params_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help="Path to a JSON file with input parameters. Mutually exclusive with --params.",
)
@click.option(
    "--org",
    "org_ref",
    type=str,
    default=None,
    help=(
        "Override execution org context (UUID or name). Requires platform "
        "admin. Omit to use the caller's default org."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def execute_workflow(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    params: str | None,
    params_file: str | None,
    org_ref: str | None,
) -> None:
    """Execute a registered workflow remotely and stream logs as it runs.

    ``REF`` is a workflow UUID, name, or ``path::func`` locator. The command:

    1. Resolves ``REF`` and posts to ``/api/workflows/execute`` (async — does
       not block the platform).
    2. Connects to ``/ws/execution/{id}`` and prints log lines as they arrive.
    3. Exits when the execution reaches a terminal status, after a final GET
       to backfill any logs that emitted before the WebSocket connected and
       to fetch the final result.

    Use ``bifrost run <file> --workflow <name>`` for local-file iteration —
    ``execute`` targets workflows already registered on the platform.
    """
    if params and params_file:
        raise click.UsageError("--params and --params-file are mutually exclusive")

    input_data: dict[str, Any] = {}
    if params:
        try:
            input_data = json.loads(params)
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"--params is not valid JSON: {exc}")
    elif params_file:
        with open(params_file, "r", encoding="utf-8") as fh:
            input_data = json.load(fh)
    if not isinstance(input_data, dict):
        raise click.UsageError("Input parameters must be a JSON object")

    workflow_uuid = await resolver.resolve("workflow", ref)

    body: dict[str, Any] = {
        "workflow_id": workflow_uuid,
        "input_data": input_data,
        "sync": False,
    }
    if org_ref:
        body["org_id"] = await resolver.resolve("org", org_ref)

    post_response = await client.post("/api/workflows/execute", json=body)
    post_response.raise_for_status()
    initial = post_response.json()
    execution_id = initial.get("execution_id")
    if not execution_id:
        raise click.ClickException(
            f"Execution response missing execution_id: {initial!r}"
        )

    if initial.get("status") in _TERMINAL_STATUSES:
        # Server already finished (e.g., data provider, or sync override) —
        # nothing to stream. Print the result and exit.
        output_result(initial, ctx=ctx)
        sys.exit(0 if initial.get("status") == "Success" else 1)

    click.echo(f"execution_id: {execution_id}", err=True)

    final_status = await _stream_execution_logs(client, execution_id)
    final = await _fetch_final_execution(client, execution_id)
    output_result(final, ctx=ctx)
    sys.exit(0 if final_status == "Success" else 1)


async def _stream_execution_logs(
    client: BifrostClient, execution_id: str
) -> str:
    """Connect to /ws/execution/{id} and print log lines until terminal status.

    Returns the terminal status string. The websockets dependency is imported
    lazily so the rest of the CLI stays usable when websockets is missing
    from a partial install.
    """
    try:
        from websockets.asyncio.client import connect as ws_connect
        from websockets.exceptions import ConnectionClosed
    except ImportError as exc:
        raise click.ClickException(
            "websockets is required for `bifrost workflows execute` "
            "but is not installed. Install with: pip install websockets"
        ) from exc

    ws_scheme = "wss" if client.api_url.startswith("https") else "ws"
    host = client.api_url.split("://", 1)[1].rstrip("/")
    ws_url = f"{ws_scheme}://{host}/ws/execution/{execution_id}"
    headers = {"Authorization": f"Bearer {client._access_token}"}

    final_status: str = "Failed"
    try:
        async with ws_connect(ws_url, additional_headers=headers) as websocket:
            try:
                while True:
                    raw = await websocket.recv()
                    msg = json.loads(raw)
                    msg_type = msg.get("type")

                    if msg_type == "execution_log":
                        level = (msg.get("level") or "info").upper()
                        message = msg.get("message") or ""
                        click.echo(f"[{level}] {message}")
                    elif msg_type == "execution_update":
                        status_val = msg.get("status")
                        if status_val in _TERMINAL_STATUSES:
                            final_status = status_val
                            break
                    # Ignore connected / pong / unrelated message types.
            except ConnectionClosed:
                # Server closes the WS after the terminal execution_update
                # — expected end of stream, not an error. The final GET below
                # picks up the result.
                pass
    except OSError as exc:
        raise click.ClickException(
            f"WebSocket connection to {ws_url} failed: {exc}"
        ) from exc

    return final_status


async def _fetch_final_execution(
    client: BifrostClient, execution_id: str
) -> dict[str, Any]:
    """Fetch the final execution record (logs + result) once it has terminated.

    Backfills any logs that emitted between POST and WS connect, and surfaces
    the final result/error/duration that the websocket update doesn't carry.
    Retries briefly to absorb the small lag between the worker writing the
    terminal status and the API serving it.
    """
    record: dict[str, Any] = {}
    for delay in (0.0, 0.25, 0.5, 1.0):
        if delay:
            await asyncio.sleep(delay)
        response = await client.get(f"/api/executions/{execution_id}")
        response.raise_for_status()
        record = response.json()
        if record.get("status") in _TERMINAL_STATUSES:
            return record
    return record


@workflows_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def update_workflow(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a workflow's editable properties.

    ``REF`` is a UUID, workflow name, or ``path::func`` locator. See
    :mod:`bifrost.refs` for resolution rules.
    """
    workflow_uuid = await resolver.resolve("workflow", ref)
    body = await assemble_body(WorkflowUpdateRequest, fields, resolver=resolver)
    response = await client.patch(
        f"/api/workflows/{workflow_uuid}", json=body
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@workflows_group.command("remap")
@click.argument("source_ref")
@click.option(
    "--to",
    "target_ref",
    required=True,
    type=str,
    help="Active workflow ref (UUID, name, or path::func) to receive references.",
)
@click.pass_context
@pass_resolver
@run_async
async def remap_workflow(
    ctx: click.Context,
    source_ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    target_ref: str,
) -> None:
    """Move references from one workflow ID to another active workflow ID."""
    source_uuid = await resolver.resolve("workflow", source_ref)
    target_uuid = await resolver.resolve("workflow", target_ref)
    response = await client.post(
        f"/api/workflows/{source_uuid}/remap",
        json={"target_workflow_id": target_uuid},
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@workflows_group.command("delete")
@click.argument("ref")
@click.option(
    "--force/--no-force",
    "force",
    default=False,
    help=(
        "Skip the deactivation protection check and delete the workflow even "
        "if it has dependent forms/apps/agents."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def delete_workflow(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    force: bool,
) -> None:
    """Delete a workflow by removing its function from the source file.

    ``REF`` is a UUID, workflow name, or ``path::func`` locator. Without
    ``--force``, the API performs a deactivation-protection pre-check and
    returns 409 if the workflow has dependents; pass ``--force`` to bypass.
    """
    workflow_uuid = await resolver.resolve("workflow", ref)
    if force:
        # httpx's AsyncClient.delete does not accept a json body; use the
        # generic request() helper so force_deactivation can reach the server.
        response = await client.request(
            "DELETE",
            f"/api/workflows/{workflow_uuid}",
            json={"force_deactivation": True},
        )
    else:
        response = await client.delete(f"/api/workflows/{workflow_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@workflows_group.command("grant-role")
@click.argument("ref")
@click.argument("role_ref")
@click.pass_context
@pass_resolver
@run_async
async def grant_role(
    ctx: click.Context,
    ref: str,
    role_ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Grant a role access to a workflow.

    ``REF`` is a workflow UUID / name / ``path::func``. ``ROLE_REF`` is a
    role UUID or role name. The underlying endpoint accepts a batch of
    role IDs — this command sends a single-element list for simplicity.
    """
    workflow_uuid = await resolver.resolve("workflow", ref)
    role_uuid = await resolver.resolve("role", role_ref)
    response = await client.post(
        f"/api/workflows/{workflow_uuid}/roles",
        json={"role_ids": [role_uuid]},
    )
    response.raise_for_status()
    output_result(
        {"workflow_id": workflow_uuid, "role_id": role_uuid, "granted": True},
        ctx=ctx,
    )


@workflows_group.command("revoke-role")
@click.argument("ref")
@click.argument("role_ref")
@click.pass_context
@pass_resolver
@run_async
async def revoke_role(
    ctx: click.Context,
    ref: str,
    role_ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Revoke a role's access from a workflow.

    ``REF`` is a workflow UUID / name / ``path::func``. ``ROLE_REF`` is a
    role UUID or role name.
    """
    workflow_uuid = await resolver.resolve("workflow", ref)
    role_uuid = await resolver.resolve("role", role_ref)
    response = await client.delete(
        f"/api/workflows/{workflow_uuid}/roles/{role_uuid}",
    )
    response.raise_for_status()
    output_result(
        {"workflow_id": workflow_uuid, "role_id": role_uuid, "revoked": True},
        ctx=ctx,
    )


@workflows_group.command("list-orphaned")
@click.pass_context
@pass_resolver
@run_async
async def list_orphaned_workflows(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001
) -> None:
    """List all orphaned workflows (backing file deleted or function removed).

    Orphaned workflows are workflows whose source file no longer exists or no
    longer contains the decorated function. They can be repointed with
    ``bifrost workflows replace``.
    """
    response = await client.get("/api/workflows/orphaned")
    response.raise_for_status()
    # Server returns {"workflows": [...]}; unwrap to a list for consistent CLI output.
    payload = response.json()
    output_result(payload.get("workflows", payload), ctx=ctx)


@workflows_group.command("replace")
@click.argument("ref")
@click.option(
    "--path",
    "source_path",
    required=True,
    type=str,
    help="Workspace-relative path to the .py file containing the decorated function.",
)
@click.option(
    "--function-name",
    "function_name",
    required=True,
    type=str,
    help="Name of the decorated function to point this workflow at.",
)
@click.option(
    "--allow-type-change",
    "allow_type_change",
    is_flag=True,
    default=False,
    help=(
        "Allow the decorator type to change (e.g. @workflow → @data_provider). "
        "Off by default to prevent silently breaking form bindings."
    ),
)
@click.pass_context
@pass_resolver
@run_async
async def replace_workflow(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    source_path: str,
    function_name: str,
    allow_type_change: bool,
) -> None:
    """Repoint an orphaned workflow to a new file location.

    ``REF`` is a UUID or workflow name (use ``bifrost workflows list-orphaned``
    to find orphaned UUIDs). The target file must exist in the workspace and
    contain a ``@workflow``, ``@tool``, or ``@data_provider`` decorated function
    with the given name. The workflow UUID is preserved so form/agent references
    remain intact.
    """
    workflow_uuid = await resolver.resolve("workflow", ref)
    body: dict[str, Any] = {
        "source_path": source_path,
        "function_name": function_name,
        "allow_type_change": allow_type_change,
    }
    response = await client.post(
        f"/api/workflows/{workflow_uuid}/replace", json=body
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["workflows_group"]
