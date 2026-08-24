"""CLI commands for managing roles.

Implements Task 5b of the CLI mutation surface plan:

* ``bifrost roles list`` → ``GET /api/roles``
* ``bifrost roles get <ref>`` → ``GET /api/roles/{uuid}``
* ``bifrost roles create`` → ``POST /api/roles`` (body from
  :class:`RoleCreate`)
* ``bifrost roles update <ref>`` → ``PATCH /api/roles/{uuid}`` (body from
  :class:`RoleUpdate`; unset flags omitted by :func:`assemble_body`)
* ``bifrost roles delete <ref>`` → ``DELETE /api/roles/{uuid}``

Flags are generated from the DTOs via :func:`build_cli_flags`. Role capability
keys use the repeatable ``--capabilities`` flag, matching the API's canonical
capability vocabulary.
"""

from __future__ import annotations

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
from bifrost.contracts import (
    RoleCreate,
    RoleUpdate,
)

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

roles_group = entity_group("roles", "Manage roles.")


_CREATE_FLAGS = build_cli_flags(
    RoleCreate,
    exclude=DTO_EXCLUDES.get("RoleCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("RoleCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    RoleUpdate,
    exclude=DTO_EXCLUDES.get("RoleUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("RoleUpdate", {}),
)


@roles_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_roles(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all roles."""
    response = await client.get("/api/roles")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@roles_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_role(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single role by UUID or name."""
    role_uuid = await resolver.resolve("role", ref)
    response = await client.get(f"/api/roles/{role_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@roles_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def create_role(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new role."""
    body = await assemble_body(RoleCreate, fields, resolver=resolver)
    response = await client.post("/api/roles", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@roles_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def update_role(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a role.

    ``REF`` is a UUID or role name. Names are resolved via
    :class:`RefResolver`; ambiguous names fail loudly with the candidate list.
    """
    role_uuid = await resolver.resolve("role", ref)
    body = await assemble_body(RoleUpdate, fields, resolver=resolver)
    response = await client.patch(f"/api/roles/{role_uuid}", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@roles_group.command("delete")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def delete_role(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete a role.

    ``REF`` is a UUID or role name. CASCADE removes all role assignments.
    """
    role_uuid = await resolver.resolve("role", ref)
    response = await client.delete(f"/api/roles/{role_uuid}")
    response.raise_for_status()
    output_result({"deleted": role_uuid}, ctx=ctx)


__all__ = ["roles_group"]
