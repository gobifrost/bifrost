"""CLI commands for managing forms.

Implements Task 5d of the CLI mutation surface plan plus the discovery
parity follow-up:

* ``bifrost forms list`` → ``GET /api/forms``
* ``bifrost forms get <ref>`` → ``GET /api/forms/{uuid}``
* ``bifrost forms create`` → ``POST /api/forms`` (body from
  :class:`FormCreate`)
* ``bifrost forms update <ref>`` → ``PATCH /api/forms/{uuid}`` (body from
  :class:`FormUpdate`; unset flags omitted by :func:`assemble_body`)
* ``bifrost forms delete <ref>`` → ``DELETE /api/forms/{uuid}``

Ref-lookup fields surface as user-friendly flags:

* ``--workflow`` (resolved via :class:`RefResolver` to the workflow UUID)
* ``--launch-workflow`` (same; disambiguated by the field stem)
* ``--organization`` (org ref)

``--form-schema`` accepts either a JSON literal or a ``@path/to/schema.yaml``
reference — the dict loader in :func:`load_dict_value` handles both shapes.
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
from bifrost.contracts import FormCreate, FormUpdate

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

forms_group = entity_group("forms", "Manage forms.")


_CREATE_FLAGS = build_cli_flags(
    FormCreate,
    exclude=DTO_EXCLUDES.get("FormCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("FormCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    FormUpdate,
    exclude=DTO_EXCLUDES.get("FormUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("FormUpdate", {}),
)


@forms_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_forms(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all forms."""
    response = await client.get("/api/forms")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@forms_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_form(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single form by UUID or name."""
    form_uuid = await resolver.resolve("form", ref)
    response = await client.get(f"/api/forms/{form_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@forms_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_form(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new form.

    ``--workflow`` / ``--launch-workflow`` accept a UUID, name, or
    ``path::func`` ref. ``--form-schema`` accepts a JSON literal or
    ``@path/to/schema.yaml`` — the file is loaded and embedded as a dict.

    Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
    form to the caller's org, ``--global`` makes it global, ``--org
    <id|name>`` scopes it to that org.
    """
    body = await assemble_body(FormCreate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id
    response = await client.post("/api/forms", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@forms_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def update_form(
    ctx: click.Context,
    ref: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a form.

    ``REF`` is a UUID or form name. Names are resolved via
    :class:`RefResolver`; ambiguous names fail loudly with the candidate
    list. Unset flags are omitted from the payload so only the supplied
    fields are patched.

    Passing ``--org``/``--global`` re-scopes the form (HOME leaves the scope
    unchanged, since omitting org sends no ``organization_id``).
    """
    form_uuid = await resolver.resolve("form", ref)
    body = await assemble_body(FormUpdate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id
    response = await client.patch(f"/api/forms/{form_uuid}", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@forms_group.command("delete")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def delete_form(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete (soft-delete) a form.

    ``REF`` is a UUID or form name. Matches the API default (is_active=False);
    use the REST endpoint directly with ``?purge=true`` to hard-delete.
    """
    form_uuid = await resolver.resolve("form", ref)
    response = await client.delete(f"/api/forms/{form_uuid}")
    response.raise_for_status()
    output_result({"deleted": form_uuid}, ctx=ctx)


__all__ = ["forms_group"]
