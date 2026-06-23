"""CLI commands for managing named, reusable policy rules.

* ``bifrost policy-rule create`` → ``POST /api/policy-rules``
* ``bifrost policy-rule list`` → ``GET /api/policy-rules``
* ``bifrost policy-rule get <domain> <name>`` → find in list
* ``bifrost policy-rule update <domain> <name>`` → ``PUT /api/policy-rules/{domain}/{name}``
* ``bifrost policy-rule delete <domain> <name>`` → ``DELETE /api/policy-rules/{domain}/{name}``
* ``bifrost policy-rule usages <domain> <name>`` → ``GET /api/policy-rules/{domain}/{name}/usages``

All endpoints are admin-gated (CurrentSuperuser / platform-admin-or-provider-org bypass).

The ``body`` flag accepts a JSON literal or ``@path/to/file.yaml`` reference via the
standard :func:`load_dict_value` loader. For inline inline policy bodies the caller
passes a JSON object: ``'{"actions": ["read"], "when": null}'``.
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
from bifrost.org_target import org_option, resolve_org_target
from bifrost.refs import RefResolver
from bifrost.contracts import PolicyRuleCreate, PolicyRuleUpdate

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

policy_rule_group = entity_group("policy-rule", "Manage named, reusable policy rules.")


_CREATE_FLAGS = build_cli_flags(
    PolicyRuleCreate,
    exclude=DTO_EXCLUDES.get("PolicyRuleCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("PolicyRuleCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    PolicyRuleUpdate,
    exclude=DTO_EXCLUDES.get("PolicyRuleUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("PolicyRuleUpdate", {}),
)


@policy_rule_group.command("list")
@click.option(
    "--domain",
    type=click.Choice(["file", "table"]),
    default=None,
    help="Filter by domain ('file' or 'table').",
)
@click.option(
    "--scope",
    default=None,
    help="Organization UUID to filter by scope.",
)
@click.pass_context
@pass_resolver
@run_async
async def list_policy_rules(
    ctx: click.Context,
    domain: str | None,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List named policy rules."""
    params: dict[str, str] = {}
    if domain is not None:
        params["domain"] = domain
    if scope is not None:
        params["organization_id"] = scope
    response = await client.get("/api/policy-rules", params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@policy_rule_group.command("get")
@click.argument("domain", type=click.Choice(["file", "table"]))
@click.argument("name")
@click.option(
    "--scope",
    default=None,
    help="Organization UUID for org-scoped rules.",
)
@click.pass_context
@pass_resolver
@run_async
async def get_policy_rule(
    ctx: click.Context,
    domain: str,
    name: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Get a single policy rule by domain and name.

    Uses the usages endpoint (which 404s when not found) to confirm the rule
    exists, then fetches the full record from the list.
    """
    params: dict[str, str] = {}
    if scope is not None:
        params["organization_id"] = scope
    # Fetch via list + filter — there is no single-GET by domain+name.
    list_params: dict[str, str] = {"domain": domain}
    if scope is not None:
        list_params["organization_id"] = scope
    response = await client.get("/api/policy-rules", params=list_params)
    response.raise_for_status()
    items = response.json()
    match = next((item for item in items if item.get("name") == name), None)
    if match is None:
        raise click.ClickException(
            f"policy rule '{name}' (domain={domain}) not found"
        )
    output_result(match, ctx=ctx)


@policy_rule_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_policy_rule(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a named policy rule.

    ``--body`` accepts a JSON literal or ``@path/to/file.yaml``.

    Example:

    \\b
        bifrost policy-rule create --name read_all --domain file \\
            --body '{"actions": ["read"], "when": null}'

    Org targeting follows the unified ``--org`` standard.
    """
    body = await assemble_body(PolicyRuleCreate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id
    response = await client.post("/api/policy-rules", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@policy_rule_group.command("update")
@click.argument("domain", type=click.Choice(["file", "table"]))
@click.argument("name")
@_apply_flags(_UPDATE_FLAGS)
@click.option(
    "--scope",
    default=None,
    help="Organization UUID for org-scoped rules.",
)
@click.pass_context
@pass_resolver
@run_async
async def update_policy_rule(
    ctx: click.Context,
    domain: str,
    name: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a named policy rule.

    ``DOMAIN`` is 'file' or 'table'. ``NAME`` is the rule's name.
    Unset flags are omitted; the server preserves existing values.
    """
    body = await assemble_body(PolicyRuleUpdate, fields, resolver=resolver)
    params: dict[str, str] = {}
    if scope is not None:
        params["organization_id"] = scope
    response = await client.put(
        f"/api/policy-rules/{domain}/{name}",
        params=params,
        json=body,
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@policy_rule_group.command("delete")
@click.argument("domain", type=click.Choice(["file", "table"]))
@click.argument("name")
@click.option(
    "--scope",
    default=None,
    help="Organization UUID for org-scoped rules.",
)
@click.pass_context
@pass_resolver
@run_async
async def delete_policy_rule(
    ctx: click.Context,
    domain: str,
    name: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Delete a named policy rule.

    ``DOMAIN`` is 'file' or 'table'. ``NAME`` is the rule's name.
    Fails with 409 if the rule is read-only (built-in) or in use.
    """
    params: dict[str, str] = {}
    if scope is not None:
        params["organization_id"] = scope
    response = await client.delete(
        f"/api/policy-rules/{domain}/{name}",
        params=params,
    )
    response.raise_for_status()
    output_result({"deleted": name, "domain": domain}, ctx=ctx)


@policy_rule_group.command("usages")
@click.argument("domain", type=click.Choice(["file", "table"]))
@click.argument("name")
@click.option(
    "--scope",
    default=None,
    help="Organization UUID for org-scoped rules.",
)
@click.pass_context
@pass_resolver
@run_async
async def get_policy_rule_usages(
    ctx: click.Context,
    domain: str,
    name: str,
    scope: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Show all file-policies and tables that reference a rule."""
    params: dict[str, str] = {}
    if scope is not None:
        params["organization_id"] = scope
    response = await client.get(
        f"/api/policy-rules/{domain}/{name}/usages",
        params=params,
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["policy_rule_group"]
