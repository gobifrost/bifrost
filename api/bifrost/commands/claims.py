"""CLI commands for managing custom claims."""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.contracts import CustomClaimCreate, CustomClaimUpdate
from bifrost.dto_flags import (
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    assemble_body,
    build_cli_flags,
)
from bifrost.org_target import org_option, resolve_org_target
from bifrost.refs import RefResolver

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

claims_group = entity_group("claims", "Manage custom claims.")


_CREATE_FLAGS = build_cli_flags(
    CustomClaimCreate,
    exclude=DTO_EXCLUDES.get("CustomClaimCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("CustomClaimCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    CustomClaimUpdate,
    exclude=DTO_EXCLUDES.get("CustomClaimUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("CustomClaimUpdate", {}),
)


async def _scope_params(
    org: str | None, is_global: bool, resolver: RefResolver
) -> dict[str, str]:
    """Resolve the standard --org target to the claims `scope` query param.

    Custom Claims are ALWAYS org-scoped (the server has no global loose-claim
    path), so global targeting is rejected here with a clear message. HOME (omit)
    sends no `scope` — the server uses the caller's org.
    """
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set and target.organization_id is None:
        raise click.ClickException(
            "Custom claims are always org-scoped — global (--global / --org "
            "none|global) is not supported. Target an org with --org <uuid|name>."
        )
    if not target.is_set:
        return {}
    return {"scope": target.organization_id}  # type: ignore[dict-item]


@claims_group.command("list")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def list_claims(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List custom claims (superusers see all orgs by default)."""
    params = await _scope_params(org, is_global, resolver)
    response = await client.get("/api/claims", params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("get")
@click.argument("name")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def get_claim(
    ctx: click.Context,
    name: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a custom claim by name."""
    params = await _scope_params(org, is_global, resolver)
    response = await client.get(f"/api/claims/{name}", params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_claim(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a custom claim."""
    params = await _scope_params(org, is_global, resolver)
    body = await assemble_body(CustomClaimCreate, fields, resolver=resolver)
    response = await client.post("/api/claims", json=body, params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("update")
@click.argument("name")
@_apply_flags(_UPDATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def update_claim(
    ctx: click.Context,
    name: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a custom claim by name."""
    params = await _scope_params(org, is_global, resolver)
    body = await assemble_body(CustomClaimUpdate, fields, resolver=resolver)
    response = await client.patch(f"/api/claims/{name}", json=body, params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("delete")
@click.argument("name")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def delete_claim(
    ctx: click.Context,
    name: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete a custom claim by name."""
    params = await _scope_params(org, is_global, resolver)
    response = await client.delete(f"/api/claims/{name}", params=params)
    response.raise_for_status()
    output_result({"deleted": name}, ctx=ctx)


__all__ = ["claims_group"]
