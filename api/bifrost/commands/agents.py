"""CLI commands for managing agents.

Implements Task 5e of the CLI mutation surface plan plus the discovery
parity follow-up:

* ``bifrost agents list`` → ``GET /api/agents``
* ``bifrost agents get <ref>`` → ``GET /api/agents/{uuid}``
* ``bifrost agents create`` → ``POST /api/agents``
* ``bifrost agents update <ref>`` → ``PUT /api/agents/{uuid}``
  (the audit correction — the server exposes PUT, not PATCH, on this route).
* ``bifrost agents delete <ref>`` → ``DELETE /api/agents/{uuid}``

Flags are generated from :class:`AgentCreate` / :class:`AgentUpdate` via
:func:`build_cli_flags`. Three agent-specific behaviours layer on top of the
generic DTO-driven surface:

* ``--system-prompt`` accepts ``@path`` to load a multi-line prompt from a
  file (handled locally via :func:`_load_str_file` because the shared
  :func:`load_dict_value` only handles ``dict`` fields).
* ``--tool-ids`` / ``--delegated-agent-ids`` accept comma-separated refs;
  each entry is resolved to a UUID via :class:`RefResolver` (``workflow`` for
  tools, ``agent`` for delegations). These are deliberately **not** wired
  through :data:`DTO_REF_LOOKUPS` — that map is scalar-only, and adding them
  there would collapse list values to a single ``str(value)`` resolve call.
* ``--clear-roles`` falls out of the generator automatically because the
  field lives on ``AgentUpdate`` as a plain ``bool``; tri-state flag handling
  makes ``--clear-roles`` / omitted the idiomatic usage.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.dto_flags import (
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    assemble_body,
    build_cli_flags,
)
from bifrost.org_target import org_option, resolve_org_target
from bifrost.refs import RefResolver
from bifrost.contracts import AgentCreate, AgentUpdate

from .base import (
    _apply_flags,
    _json_requested,
    entity_group,
    output_result,
    pass_resolver,
    run_async,
)

agents_group = entity_group("agents", "Manage agents.")


def _load_str_file(value: str | None) -> str | None:
    """Resolve ``@path`` string flags to the file contents.

    Returns ``value`` unchanged when it does not start with ``@``. Used by
    ``--system-prompt`` so multi-line agent prompts can live on disk
    (``.md`` files check into the repo cleanly).
    """
    if value is None:
        return None
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


async def _resolve_ref_list(
    resolver: RefResolver,
    kind: str,
    values: list[str] | None,
) -> list[str] | None:
    """Resolve each entry in ``values`` via ``resolver.resolve(kind, entry)``.

    Returns ``None`` unchanged so callers can distinguish "not provided"
    (leave field off the body) from "empty list" (clear the field).
    """
    if values is None:
        return None
    resolved: list[str] = []
    for value in values:
        resolved.append(await resolver.resolve(kind, str(value)))  # type: ignore[arg-type]
    return resolved


def _normalize_id_set(values: list[Any] | None) -> set[str]:
    """Normalize ID values from request/response bodies for set comparison."""
    return {str(value) for value in values or []}


async def _verify_tool_ids_persisted(
    *,
    client: BifrostClient,
    agent_uuid: str,
    requested_tool_ids: list[str],
) -> dict[str, Any]:
    """Read back agent tools after an update and fail loudly on drift."""
    response = await client.get(f"/api/agents/{agent_uuid}")
    response.raise_for_status()
    agent = response.json()
    persisted_tool_ids = agent.get("tool_ids")
    if not isinstance(persisted_tool_ids, list):
        click.echo(
            "Agent update response did not include a tool_ids list after read-back.",
            err=True,
        )
        raise SystemExit(1)

    requested = _normalize_id_set(requested_tool_ids)
    persisted = _normalize_id_set(persisted_tool_ids)
    if requested != persisted:
        click.echo(
            "Agent tool_ids did not persist after update.\n"
            f"requested: {sorted(requested)}\n"
            f"persisted: {sorted(persisted)}",
            err=True,
        )
        raise SystemExit(1)

    return agent


_CREATE_FLAGS = build_cli_flags(
    AgentCreate,
    exclude=DTO_EXCLUDES.get("AgentCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("AgentCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    AgentUpdate,
    exclude=DTO_EXCLUDES.get("AgentUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("AgentUpdate", {}),
)


@agents_group.command("list")
@click.option(
    "--include-inactive",
    is_flag=True,
    default=False,
    help="Include inactive agents.",
)
@click.pass_context
@pass_resolver
@run_async
async def list_agents(
    ctx: click.Context,
    include_inactive: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List active agents by default."""
    params = {"active_only": False} if include_inactive else None
    response = await client.get("/api/agents", params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agents_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_agent(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single agent by UUID or name."""
    agent_uuid = await resolver.resolve("agent", ref)
    response = await client.get(f"/api/agents/{agent_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agents_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_agent(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new agent.

    ``--system-prompt @file.md`` loads the prompt from disk. ``--tool-ids``
    and ``--delegated-agent-ids`` resolve each entry via the ref resolver
    before the body is sent.

    Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
    agent to the caller's org, ``--global`` makes it global, ``--org
    <id|name>`` scopes it to that org. (Non-admins may only create private
    agents in their own org regardless.)
    """
    # Load @file prompt before DTO assembly so validation sees the real text.
    if "system_prompt" in fields:
        fields["system_prompt"] = _load_str_file(fields.get("system_prompt"))

    body = await assemble_body(AgentCreate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id

    tool_ids = body.get("tool_ids")
    if isinstance(tool_ids, list):
        body["tool_ids"] = await _resolve_ref_list(resolver, "workflow", tool_ids)

    delegated = body.get("delegated_agent_ids")
    if isinstance(delegated, list):
        body["delegated_agent_ids"] = await _resolve_ref_list(
            resolver, "agent", delegated
        )

    response = await client.post("/api/agents", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agents_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def update_agent(
    ctx: click.Context,
    ref: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update an agent.

    ``REF`` is a UUID or agent name. Names are resolved via
    :class:`RefResolver`; ambiguous names fail loudly with the candidate
    list. The verb is **PUT** per the cli-mutation-surface audit correction.

    Passing ``--org``/``--global`` re-scopes the agent (HOME leaves the scope
    unchanged, since omitting org sends no ``organization_id``).
    """
    agent_uuid = await resolver.resolve("agent", ref)

    if "system_prompt" in fields:
        fields["system_prompt"] = _load_str_file(fields.get("system_prompt"))

    body = await assemble_body(AgentUpdate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id

    tool_ids = body.get("tool_ids")
    if isinstance(tool_ids, list):
        body["tool_ids"] = await _resolve_ref_list(resolver, "workflow", tool_ids)

    delegated = body.get("delegated_agent_ids")
    if isinstance(delegated, list):
        body["delegated_agent_ids"] = await _resolve_ref_list(
            resolver, "agent", delegated
        )

    response = await client.put(f"/api/agents/{agent_uuid}", json=body)
    response.raise_for_status()
    result = response.json()
    if isinstance(body.get("tool_ids"), list):
        result = await _verify_tool_ids_persisted(
            client=client,
            agent_uuid=agent_uuid,
            requested_tool_ids=body["tool_ids"],
        )
    output_result(result, ctx=ctx)


@agents_group.command("delete")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def delete_agent(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Permanently delete an agent.

    ``REF`` is a UUID or agent name. The server returns ``204 No Content``
    on success; the CLI reports the resolved UUID.
    """
    agent_uuid = await resolver.resolve("agent", ref)
    response = await client.delete(f"/api/agents/{agent_uuid}")
    response.raise_for_status()
    output_result({"deleted": agent_uuid}, ctx=ctx)


__all__ = ["agents_group"]


# -----------------------------------------------------------------------------
# Agent run debugger (read-only inspection over the durable journal)
# -----------------------------------------------------------------------------

_TERMINAL_RUN_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "timeout",
        "budget_exceeded",
        "contract_failed",
        "paused",
    }
)


def _render_tree_node(node: dict[str, Any], depth: int = 0) -> list[str]:
    """Render one tree node (and its children) as indented human lines."""
    indent = "  " * depth
    run_id = str(node.get("run_id", "?"))[:8]
    agent = node.get("agent_name") or "unknown-agent"
    status = node.get("status", "?")
    lines = [f"{indent}[{status}] {run_id} {agent}"]
    diagnostic = node.get("diagnostic")
    if diagnostic:
        lines.append(f"{indent}  ! {diagnostic}")
    for child in node.get("children", []):
        lines.extend(_render_tree_node(child, depth + 1))
    return lines


def _render_timeline_entry(entry: dict[str, Any]) -> str:
    """One human line per timeline entry; full detail stays in --json."""
    seq = entry.get("sequence", "?")
    kind = entry.get("kind", "?")
    summary = entry.get("summary", "")
    line = f"#{seq} [{kind}] {summary}"
    detail = entry.get("detail") or {}
    state = detail.get("invocation_state")
    if state in ("uncertain", "failed"):
        line += f" (tool state: {state})"
    return line


@agents_group.command("run-tree")
@click.argument("run_id")
@click.pass_context
@pass_resolver
@run_async
async def run_tree(
    ctx: click.Context,
    run_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Show the delegation tree for RUN_ID (visible runs only)."""
    response = await client.get(f"/api/agent-runs/{run_id}/tree")
    response.raise_for_status()
    body = response.json()
    if _json_requested(ctx):
        output_result(body, ctx=ctx)
        return
    click.echo(f"root: {body.get('root_run_id')} (total {body.get('total_runs')})")
    for line in _render_tree_node(body.get("root", {})):
        click.echo(line)
    if body.get("truncated"):
        click.echo("tree truncated; use run-timeline for the full journal")


@agents_group.command("run-timeline")
@click.argument("run_id")
@click.option("--kind", default=None, help="Filter to one journal event kind.")
@click.option("--attempt", type=int, default=None, help="Filter to one attempt.")
@click.option(
    "--include-descendants",
    is_flag=True,
    default=False,
    help="Merge visible descendant runs into one ordered timeline.",
)
@click.option("--limit", type=int, default=50, help="Entries per page.")
@click.option("--cursor", default=None, help="Resume from a previous cursor.")
@click.option(
    "--follow",
    is_flag=True,
    default=False,
    help="Poll for new entries until the run reaches a terminal state.",
)
@click.option(
    "--poll-interval",
    type=float,
    default=2.0,
    help="Seconds between follow polls.",
)
@click.option(
    "--max-polls",
    type=int,
    default=60,
    help="Maximum follow polls before exiting.",
)
@click.pass_context
@pass_resolver
@run_async
async def run_timeline(
    ctx: click.Context,
    run_id: str,
    kind: str | None,
    attempt: int | None,
    include_descendants: bool,
    limit: int,
    cursor: str | None,
    follow: bool,
    poll_interval: float,
    max_polls: int,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Show the journal timeline for RUN_ID. Read-only; never mutates runs."""
    as_json = _json_requested(ctx)

    async def _fetch_page(after: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["cursor"] = after
        if kind:
            params["kind"] = kind
        if attempt is not None:
            params["attempt"] = attempt
        if include_descendants:
            params["include_descendants"] = True
        page = await client.get(
            f"/api/agent-runs/{run_id}/timeline", params=params
        )
        page.raise_for_status()
        return page.json()

    if not follow:
        body = await _fetch_page(cursor)
        if as_json:
            output_result(body, ctx=ctx)
            return
        for entry in body.get("entries", []):
            click.echo(_render_timeline_entry(entry))
        if body.get("next_cursor"):
            click.echo(f"next cursor: {body['next_cursor']}")
        return

    # --follow: bounded short polling; exits on terminal state or Ctrl-C.
    seen: set[tuple[str, int]] = set()
    after = cursor
    try:
        for _ in range(max(1, max_polls)):
            body = await _fetch_page(after)
            for entry in body.get("entries", []):
                key = (str(entry.get("run_id")), int(entry.get("sequence", -1)))
                if key in seen:
                    continue
                seen.add(key)
                if as_json:
                    click.echo(json.dumps(entry, default=str))
                else:
                    click.echo(_render_timeline_entry(entry))
            after = body.get("next_cursor") or after
            snapshot = await client.get(f"/api/agent-runs/{run_id}/snapshot")
            snapshot.raise_for_status()
            status = snapshot.json().get("status")
            if status in _TERMINAL_RUN_STATUSES:
                if not as_json:
                    click.echo(f"run {status}")
                return
            await asyncio.sleep(max(0.0, poll_interval))
    except KeyboardInterrupt:
        if not as_json:
            click.echo("follow interrupted")
        return
    if not as_json:
        click.echo("follow poll budget exhausted; re-run with --cursor to resume")


@agents_group.command("run-snapshot")
@click.argument("run_id")
@click.pass_context
@pass_resolver
@run_async
async def run_snapshot(
    ctx: click.Context,
    run_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Show the execution snapshot and live state for RUN_ID."""
    response = await client.get(f"/api/agent-runs/{run_id}/snapshot")
    response.raise_for_status()
    body = response.json()
    if _json_requested(ctx):
        output_result(body, ctx=ctx)
        return
    model = body.get("model") or {}
    lease = body.get("lease") or {}
    contract = body.get("contract") or {}
    completion = body.get("completion_event") or {}
    usage = body.get("usage") or {}
    click.echo(f"run: {body.get('run_id')}")
    click.echo(f"agent: {body.get('agent_name')} ({body.get('agent_id')})")
    click.echo(
        f"status: {body.get('status')} "
        f"(attempt {body.get('attempt')}, "
        f"checkpoint {body.get('checkpoint_sequence')})"
    )
    click.echo(
        f"model: {model.get('provider')}/{model.get('model')} "
        f"(profile {model.get('profile_id')})"
    )
    click.echo(
        f"tools: {len(body.get('tool_names', []))} "
        f"delegated_agents: {len(body.get('delegated_agents', []))} "
        f"system_tools: {', '.join(body.get('system_tools', [])) or 'none'}"
    )
    click.echo(f"limits: {json.dumps(body.get('limits', {}), default=str)}")
    if body.get("wake_at"):
        click.echo(f"wake_at: {body['wake_at']}")
    if lease.get("owner") or lease.get("expires_at"):
        click.echo(
            f"lease: owner={lease.get('owner')} "
            f"expires_at={lease.get('expires_at')} "
            f"last_progress_at={lease.get('last_progress_at')}"
        )
    click.echo(
        f"usage: iterations={usage.get('iterations_used')} "
        f"tokens={usage.get('tokens_used')} "
        f"duration_ms={usage.get('duration_ms')}"
    )
    if contract.get("valid") is False:
        click.echo(f"contract_failed: {contract.get('errors')}")
    if completion.get("emitted_at"):
        click.echo(f"completion_event: emitted at {completion['emitted_at']}")
    elif completion.get("pending_at"):
        click.echo(
            f"completion_event: pending "
            f"(attempts {completion.get('attempts')})"
        )


@agents_group.command("run-checkpoints")
@click.argument("run_id")
@click.option("--limit", type=int, default=50, help="Checkpoints per page.")
@click.option("--cursor", default=None, help="Resume from a previous cursor.")
@click.pass_context
@pass_resolver
@run_async
async def run_checkpoints(
    ctx: click.Context,
    run_id: str,
    limit: int,
    cursor: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List checkpoint summaries for RUN_ID (metadata only)."""
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    response = await client.get(
        f"/api/agent-runs/{run_id}/checkpoints", params=params
    )
    response.raise_for_status()
    body = response.json()
    if _json_requested(ctx):
        output_result(body, ctx=ctx)
        return
    for checkpoint in body.get("checkpoints", []):
        flags = []
        if checkpoint.get("has_pending_tool_calls"):
            flags.append("pending_tools")
        if checkpoint.get("has_pending_join"):
            flags.append("pending_join")
        if checkpoint.get("has_pending_timer"):
            flags.append("pending_timer")
        suffix = f" {'+'.join(flags)}" if flags else ""
        click.echo(
            f"#{checkpoint.get('sequence')} "
            f"{checkpoint.get('created_at')} "
            f"messages={checkpoint.get('message_count')}{suffix}"
        )
    if body.get("next_cursor"):
        click.echo(f"next cursor: {body['next_cursor']}")
