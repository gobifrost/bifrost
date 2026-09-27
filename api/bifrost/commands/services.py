"""CLI commands for managing supervised services.

Implements the Services CLI follow-up (Slice 2 left ``bifrost services ...``
out; the REST surface below is e2e-proven and platform-admin only):

* ``bifrost services list`` → ``GET /api/services`` (``--limit``/``--offset``)
* ``bifrost services get <ref>`` → ``GET /api/services/{uuid}``
* ``bifrost services update <ref>`` → ``PATCH /api/services/{uuid}`` (body from
  :class:`ServicePolicyUpdate`; unset flags omitted by :func:`assemble_body`)
* ``bifrost services start|stop|restart|enable|disable <ref>`` →
  ``POST /api/services/{uuid}/{action}``
* ``bifrost services attempts <ref>`` → ``GET /api/services/{uuid}/attempts``
  (``--limit``/``--offset``)
* ``bifrost services logs <ref>`` → ``GET /api/services/{uuid}/logs``
  (``--attempt-id``, repeatable ``--level``, ``--start-date``/``--end-date``,
  ``--limit``, ``--continuation-token`` keyset cursor, ``--order``)

``REF`` is a service definition UUID or a source workflow name, resolved via
:class:`RefResolver` (kind ``"service"``); ambiguous workflow names fail
loudly with the candidate list. There is no ``create``/``delete`` verb —
service definitions are derived from ``@service`` workflows, not created
ad-hoc.

Control actions execute directly with no confirmation prompt, matching the
entity-delete precedent (``tables delete``, ``events delete-source`` run
without ``--force``/``--yes``; only secret-config deletion carries a
``--confirm`` guard). The UI already confirms Stop/Restart/Disable; the CLI
is the scripted surface and stays one-shot with the server's error body on
failure.
"""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.contracts import ServicePolicyUpdate
from bifrost.dto_flags import (
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    assemble_body,
    build_cli_flags,
)
from bifrost.refs import RefResolver

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

services_group = entity_group("services", "Manage supervised services.")


_UPDATE_FLAGS = build_cli_flags(
    ServicePolicyUpdate,
    exclude=DTO_EXCLUDES.get("ServicePolicyUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("ServicePolicyUpdate", {}),
)

_ACTION_ENDPOINTS: dict[str, str] = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "enable": "enable",
    "disable": "disable",
}


@services_group.command("list")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Max results (server default 100, max 1000).",
)
@click.option(
    "--offset",
    type=int,
    default=None,
    help="Skip results.",
)
@click.pass_context
@pass_resolver
@run_async
async def list_services(
    ctx: click.Context,
    limit: int | None,
    offset: int | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List service definitions (wrapped ``{items, total}`` payload)."""
    params: dict[str, Any] = {}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    response = await client.get("/api/services", params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@services_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_service(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single service by definition UUID or workflow name."""
    service_uuid = await resolver.resolve("service", ref)
    response = await client.get(f"/api/services/{service_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@services_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def update_service(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a service's lifecycle policy.

    ``REF`` is a definition UUID or workflow name. Unset flags are omitted
    from the payload so only the supplied fields are patched.
    """
    service_uuid = await resolver.resolve("service", ref)
    body = await assemble_body(ServicePolicyUpdate, fields, resolver=resolver)
    response = await client.patch(f"/api/services/{service_uuid}", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


async def _run_service_action(
    ctx: click.Context,
    ref: str,
    action: str,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Resolve ``ref`` and POST one service control action."""
    service_uuid = await resolver.resolve("service", ref)
    response = await client.post(f"/api/services/{service_uuid}/{action}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@services_group.command("start")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def start_service(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Request running: clears suppression so the claim loop picks it up."""
    await _run_service_action(ctx, ref, _ACTION_ENDPOINTS["start"], client, resolver)


@services_group.command("stop")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def stop_service(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Request stopped: durable desire is stored before termination."""
    await _run_service_action(ctx, ref, _ACTION_ENDPOINTS["stop"], client, resolver)


@services_group.command("restart")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def restart_service(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Rolling restart: stays desired-running, stops the live attempt if any."""
    await _run_service_action(
        ctx, ref, _ACTION_ENDPOINTS["restart"], client, resolver
    )


@services_group.command("enable")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def enable_service(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Enable a service (distinct from start: no desired-state change)."""
    await _run_service_action(
        ctx, ref, _ACTION_ENDPOINTS["enable"], client, resolver
    )


@services_group.command("disable")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def disable_service(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Disable a service: stops the live attempt, blocks future claims."""
    await _run_service_action(
        ctx, ref, _ACTION_ENDPOINTS["disable"], client, resolver
    )


@services_group.command("attempts")
@click.argument("ref")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Max results (server default 100, max 1000).",
)
@click.option(
    "--offset",
    type=int,
    default=None,
    help="Skip results.",
)
@click.pass_context
@pass_resolver
@run_async
async def list_service_attempts(
    ctx: click.Context,
    ref: str,
    limit: int | None,
    offset: int | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List attempt history newest-first for a service.

    ``REF`` is a definition UUID or workflow name.
    """
    service_uuid = await resolver.resolve("service", ref)
    params: dict[str, Any] = {}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    response = await client.get(
        f"/api/services/{service_uuid}/attempts", params=params
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@services_group.command("logs")
@click.argument("ref")
@click.option(
    "--attempt-id",
    "attempt_id",
    type=str,
    default=None,
    help="Scope to one attempt UUID.",
)
@click.option(
    "--level",
    "levels",
    multiple=True,
    help="Level allowlist, e.g. INFO. Repeat for multiple values.",
)
@click.option(
    "--start-date",
    "start_date",
    type=str,
    default=None,
    help="ISO timestamp lower bound.",
)
@click.option(
    "--end-date",
    "end_date",
    type=str,
    default=None,
    help="ISO timestamp upper bound.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Max lines per page (server default 200, max 1000).",
)
@click.option(
    "--continuation-token",
    "continuation_token",
    type=str,
    default=None,
    help="Keyset cursor from the previous page (load-older paging).",
)
@click.option(
    "--order",
    type=click.Choice(["chronological", "newest_first"]),
    default=None,
    help="Log order (server default chronological).",
)
@click.pass_context
@pass_resolver
@run_async
async def list_service_logs(
    ctx: click.Context,
    ref: str,
    attempt_id: str | None,
    levels: tuple[str, ...],
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
    continuation_token: str | None,
    order: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List trailing persisted logs for a service.

    ``REF`` is a definition UUID or workflow name. Page through older lines
    by passing the previous response's ``continuation_token`` back via
    ``--continuation-token``.
    """
    service_uuid = await resolver.resolve("service", ref)
    params: dict[str, Any] = {}
    if attempt_id is not None:
        params["attempt_id"] = attempt_id
    if levels:
        params["levels"] = list(levels)
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    if limit is not None:
        params["limit"] = limit
    if continuation_token is not None:
        params["continuation_token"] = continuation_token
    if order is not None:
        params["order"] = order
    response = await client.get(f"/api/services/{service_uuid}/logs", params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["services_group"]
