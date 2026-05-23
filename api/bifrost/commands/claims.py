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


_SCOPE_OPT = click.option(
    "--scope",
    default=None,
    help="Target organization scope (org UUID). Defaults to caller's home org.",
)


def _scope_params(scope: str | None) -> dict[str, str]:
    return {"scope": scope} if scope else {}


@claims_group.command("list")
@_SCOPE_OPT
@click.pass_context
@pass_resolver
@run_async
async def list_claims(
    ctx: click.Context,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List custom claims (superusers see all orgs by default)."""
    response = await client.get("/api/claims", params=_scope_params(scope))
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("get")
@click.argument("name")
@_SCOPE_OPT
@click.pass_context
@pass_resolver
@run_async
async def get_claim(
    ctx: click.Context,
    name: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Get a custom claim by name."""
    response = await client.get(f"/api/claims/{name}", params=_scope_params(scope))
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@_SCOPE_OPT
@click.pass_context
@pass_resolver
@run_async
async def create_claim(
    ctx: click.Context,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a custom claim."""
    body = await assemble_body(CustomClaimCreate, fields, resolver=resolver)
    response = await client.post(
        "/api/claims", json=body, params=_scope_params(scope)
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("update")
@click.argument("name")
@_apply_flags(_UPDATE_FLAGS)
@_SCOPE_OPT
@click.pass_context
@pass_resolver
@run_async
async def update_claim(
    ctx: click.Context,
    name: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a custom claim by name."""
    body = await assemble_body(CustomClaimUpdate, fields, resolver=resolver)
    response = await client.patch(
        f"/api/claims/{name}", json=body, params=_scope_params(scope)
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@claims_group.command("delete")
@click.argument("name")
@_SCOPE_OPT
@click.pass_context
@pass_resolver
@run_async
async def delete_claim(
    ctx: click.Context,
    name: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Delete a custom claim by name."""
    response = await client.delete(
        f"/api/claims/{name}", params=_scope_params(scope)
    )
    response.raise_for_status()
    output_result({"deleted": name}, ctx=ctx)


__all__ = ["claims_group"]
