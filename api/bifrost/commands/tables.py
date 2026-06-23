"""CLI commands for managing tables.

Implements Task 5i of the CLI mutation surface plan:

* ``bifrost tables list`` → ``GET /api/tables``
* ``bifrost tables get <ref>`` → ``GET /api/tables/{uuid}``
* ``bifrost tables create`` → ``POST /api/tables`` (body from
  :class:`TableCreate`)
* ``bifrost tables update <ref>`` → ``PATCH /api/tables/{uuid}`` (body from
  :class:`TableUpdate`; unset flags omitted by :func:`assemble_body`)
* ``bifrost tables delete <ref>`` → ``DELETE /api/tables/{uuid}``

``--schema`` and ``--policies`` accept either a JSON literal or a
``@path/to/file.yaml`` reference — the dict loader in
:func:`load_dict_value` handles both shapes and is used for both
``create`` and ``update``.

Ref-lookup fields surface as user-friendly flags:

* ``--organization`` (org ref) — ``TableCreate``

Rename safety: ``update`` fetches the current table first and, if ``--name``
changes it, emits a prominent warning to stderr telling the user to grep
their workspace for the old name before committing (workflow SDK calls
reference tables by name and will break on rename).
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
from bifrost.contracts import TableCreate, TableUpdate

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

tables_group = entity_group("tables", "Manage tables.")
table_policies_group = click.Group("policies", help="Manage table access policies.")


_CREATE_FLAGS = build_cli_flags(
    TableCreate,
    exclude=DTO_EXCLUDES.get("TableCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("TableCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    TableUpdate,
    exclude=DTO_EXCLUDES.get("TableUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("TableUpdate", {}),
)


@tables_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_tables(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all tables (wrapped ``{tables, total}`` payload from the API)."""
    response = await client.get("/api/tables")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@tables_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_table(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single table by UUID or name."""
    table_uuid = await resolver.resolve("table", ref)
    response = await client.get(f"/api/tables/{table_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@tables_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_table(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new table.

    ``--schema`` accepts a JSON literal or ``@path/to/schema.yaml`` — the
    file is loaded and embedded as the table schema dict. ``--policies``
    accepts the same shape and embeds row-level access policies; see
    ``docs/superpowers/specs/2026-04-30-table-policies-design.md``.

    Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
    table to the caller's org, ``--global`` makes it global, ``--org
    <id|name>`` scopes it to that org.
    """
    body = await assemble_body(TableCreate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id
    response = await client.post("/api/tables", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@tables_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def update_table(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a table.

    ``REF`` is a UUID or table name. Names are resolved via
    :class:`RefResolver`; ambiguous names fail loudly with the candidate
    list. Unset flags are omitted from the payload so only the supplied
    fields are patched.

    If ``--name`` changes the table's current name, a prominent warning is
    printed to stderr — workflow SDK code that looks up tables by name will
    break on rename. No confirmation is required; the warning just nudges
    the caller to grep their workspace before committing.
    """
    table_uuid = await resolver.resolve("table", ref)

    # Fetch current name so we can compare against --name before PATCHing.
    current_resp = await client.get(f"/api/tables/{table_uuid}")
    current_resp.raise_for_status()
    current_name = current_resp.json().get("name")

    body = await assemble_body(TableUpdate, fields, resolver=resolver)

    new_name = body.get("name")
    if new_name is not None and current_name is not None and new_name != current_name:
        click.echo(
            f"\u26a0  Renaming this table will break any workflow SDK call that "
            f"references it by name. Search your workspace: "
            f"rg -n '\\b{current_name}\\b' apps/ workflows/ before running.",
            err=True,
        )

    response = await client.patch(f"/api/tables/{table_uuid}", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@tables_group.command("delete")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def delete_table(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete a table and all its documents.

    ``REF`` is a UUID or table name. Cascade deletes the table's documents
    at the DB level — irreversible.
    """
    table_uuid = await resolver.resolve("table", ref)
    response = await client.delete(f"/api/tables/{table_uuid}")
    response.raise_for_status()
    output_result({"deleted": table_uuid}, ctx=ctx)


# ---------------------------------------------------------------------------
# tables policies subgroup — get/set table access policies (including $ref)
# ---------------------------------------------------------------------------


@table_policies_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_table_policy(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get the access policies for a table.

    ``REF`` is a UUID or table name. Returns the ``policies`` field from the
    table record, which may contain inline policy objects and/or
    ``{"$ref": "rule-name"}`` references to named policy rules.
    """
    table_uuid = await resolver.resolve("table", ref)
    response = await client.get(f"/api/tables/{table_uuid}")
    response.raise_for_status()
    data = response.json()
    output_result(data.get("policies"), ctx=ctx)


@table_policies_group.command("set")
@click.argument("ref")
@click.option(
    "--file",
    "policy_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON/YAML policy document to store. May contain ``{\"$ref\": \"rule-name\"}`` entries.",
)
@click.pass_context
@pass_resolver
@run_async
async def set_table_policy(
    ctx: click.Context,
    ref: str,
    policy_file: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Set the access policies for a table.

    ``REF`` is a UUID or table name. ``--file`` must be a JSON or YAML document
    whose ``policies`` key (or top-level list) contains policy objects and/or
    ``{"$ref": "rule-name"}`` references to named policy rules. The document is
    round-tripped unchanged — ``$ref`` entries are stored and later resolved at
    query time.
    """
    table_uuid = await resolver.resolve("table", ref)
    from pathlib import Path as _Path
    import yaml as _yaml
    raw_text = _Path(policy_file).read_text(encoding="utf-8")
    loaded = _yaml.safe_load(raw_text)
    # Normalise: accept either a bare list or a wrapper object.
    # The TablePolicies wire shape is {"policies": [...]}, so we always
    # normalise to that wrapper regardless of input format.
    if isinstance(loaded, list):
        policies_payload = {"policies": loaded}
    elif isinstance(loaded, dict) and "policies" in loaded:
        policies_payload = {"policies": loaded["policies"]}
    else:
        raise click.BadParameter(
            "policy file must contain a policies list or an object with a policies key"
        )
    response = await client.patch(
        f"/api/tables/{table_uuid}",
        json={"policies": policies_payload},
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


tables_group.add_command(table_policies_group)


__all__ = ["tables_group"]
