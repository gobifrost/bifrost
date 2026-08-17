"""CLI commands for managing organizations.

Implements Task 5a of the CLI mutation surface plan:

* ``bifrost organizations list`` → ``GET /api/organizations``
* ``bifrost organizations get <ref>`` → ``GET /api/organizations/{uuid}``
* ``bifrost organizations create`` → ``POST /api/organizations`` (body from
  :class:`OrganizationCreate`)
* ``bifrost organizations update <ref>`` → ``PATCH /api/organizations/{uuid}`` (body
  from :class:`OrganizationUpdate`; unset flags omitted by
  :func:`assemble_body`)
* ``bifrost organizations delete <ref>`` → ``DELETE /api/organizations/{uuid}``

Flags are generated from the DTOs via :func:`build_cli_flags` with the
exclude list in :data:`DTO_EXCLUDES` — so ``domain``, ``settings`` and
``is_provider`` are intentionally not surfaced (see the plan's cross-cutting
exclusion rationale).
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
    OrganizationCreate,
    OrganizationUpdate,
)

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

orgs_group = entity_group("organizations", "Manage organizations.")


_CREATE_FLAGS = build_cli_flags(
    OrganizationCreate,
    exclude=DTO_EXCLUDES.get("OrganizationCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("OrganizationCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    OrganizationUpdate,
    exclude=DTO_EXCLUDES.get("OrganizationUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("OrganizationUpdate", {}),
)


@orgs_group.command("list")
@click.option(
    "--include-inactive",
    is_flag=True,
    help="Include disabled Organizations.",
)
@click.pass_context
@pass_resolver
@run_async
async def list_orgs(
    ctx: click.Context,
    include_inactive: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all organizations."""
    response = await client.get(
        "/api/organizations",
        params={"include_inactive": include_inactive},
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@orgs_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_org(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single organization by UUID or name."""
    org_uuid = await resolver.resolve("org", ref)
    response = await client.get(f"/api/organizations/{org_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@orgs_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def create_org(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new organization."""
    body = await assemble_body(OrganizationCreate, fields, resolver=resolver)
    response = await client.post("/api/organizations", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@orgs_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def update_org(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update an organization.

    ``REF`` is a UUID or organization name. Names are resolved via
    :class:`RefResolver`; ambiguous names fail loudly with the candidate list.
    """
    org_uuid = await resolver.resolve("org", ref)
    body = await assemble_body(OrganizationUpdate, fields, resolver=resolver)
    response = await client.patch(f"/api/organizations/{org_uuid}", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@orgs_group.command("delete")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def delete_org(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete (soft-delete) an organization.

    ``REF`` is a UUID or organization name.
    """
    org_uuid = await resolver.resolve("org", ref)
    response = await client.delete(f"/api/organizations/{org_uuid}")
    response.raise_for_status()
    output_result({"deleted": org_uuid}, ctx=ctx)


__all__ = ["orgs_group"]
