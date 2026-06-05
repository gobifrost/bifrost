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
from bifrost.refs import RefResolver
from bifrost.contracts import AgentCreate, AgentUpdate

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

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
@click.pass_context
@pass_resolver
@run_async
async def list_agents(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all agents."""
    response = await client.get("/api/agents")
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
@click.pass_context
@pass_resolver
@run_async
async def create_agent(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new agent.

    ``--system-prompt @file.md`` loads the prompt from disk. ``--tool-ids``
    and ``--delegated-agent-ids`` resolve each entry via the ref resolver
    before the body is sent.
    """
    # Load @file prompt before DTO assembly so validation sees the real text.
    if "system_prompt" in fields:
        fields["system_prompt"] = _load_str_file(fields.get("system_prompt"))

    body = await assemble_body(AgentCreate, fields, resolver=resolver)

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
@click.pass_context
@pass_resolver
@run_async
async def update_agent(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update an agent.

    ``REF`` is a UUID or agent name. Names are resolved via
    :class:`RefResolver`; ambiguous names fail loudly with the candidate
    list. The verb is **PUT** per the cli-mutation-surface audit correction.
    """
    agent_uuid = await resolver.resolve("agent", ref)

    if "system_prompt" in fields:
        fields["system_prompt"] = _load_str_file(fields.get("system_prompt"))

    body = await assemble_body(AgentUpdate, fields, resolver=resolver)

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
    """Soft-delete an agent.

    ``REF`` is a UUID or agent name. The server returns ``204 No Content``
    on success; the CLI reports the resolved UUID.
    """
    agent_uuid = await resolver.resolve("agent", ref)
    response = await client.delete(f"/api/agents/{agent_uuid}")
    response.raise_for_status()
    output_result({"deleted": agent_uuid}, ctx=ctx)


__all__ = ["agents_group"]
