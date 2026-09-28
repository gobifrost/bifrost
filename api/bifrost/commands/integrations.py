"""CLI commands for managing integrations.

Implements Task 5g of the CLI mutation surface plan plus the discovery
parity follow-up:

* ``bifrost integrations list`` → ``GET /api/integrations``
* ``bifrost integrations get <ref>`` → ``GET /api/integrations/{uuid}``
* ``bifrost integrations create`` → ``POST /api/integrations`` (body from
  :class:`IntegrationCreate`).
* ``bifrost integrations update <ref>`` → ``PUT /api/integrations/{uuid}``
  (body from :class:`IntegrationUpdate`). The server refuses with 409 when
  the new ``config_schema`` drops keys that have saved ``Config`` values;
  the command surfaces that error and instructs the caller to pass
  ``--force-remove-keys`` (forwarded as the ``force_remove_keys`` query
  param) to proceed.
* ``bifrost integrations add-mapping <integration-ref>`` →
  ``POST /api/integrations/{id}/mappings`` (body from
  :class:`IntegrationMappingCreate`). ``--organization`` is a ref.
* ``bifrost integrations update-mapping <integration-ref>`` → resolves the
  mapping via ``GET /api/integrations/{id}/mappings/by-org/{org_id}``, then
  ``PUT /api/integrations/{id}/mappings/{mapping_id}``. ``oauth_token_id`` is
  **never** sent unless the opt-in ``--oauth-token-id`` flag is passed — the
  DTO-driven flag set excludes the field (per :data:`DTO_EXCLUDES`) so the
  server's existing token isn't clobbered with ``None`` on unrelated updates.

``config_schema`` is handled specially:

* The DTO declares it as ``list[ConfigSchemaItem] | None``; the generator
  would otherwise surface it as a repeatable string flag. We override that by
  popping the collected value out of ``fields`` and loading it via
  :func:`load_schema_file` (YAML or JSON, top-level list or ``{schema: [...]}``
  dict) before the body is assembled.
* On ``update``, removed-key detection and the affected-value count are
  computed server-side (409 ``integration_schema_removal_requires_confirmation``);
  the command surfaces that response rather than re-deriving the count
  client-side.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.dto_flags import (
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    assemble_body,
    build_cli_flags,
)
from bifrost.refs import RefResolver
from bifrost.contracts import (
    IntegrationCreate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationUpdate,
)

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

integrations_group = entity_group("integrations", "Manage integrations.")


def load_schema_file(raw: str | None) -> list[dict[str, Any]] | None:
    """Resolve a ``--config-schema`` argument to a list of schema items.

    Accepts:

    * ``@path/to/file.yaml`` — loads YAML/JSON; accepts either a top-level
      list of schema items or a ``{schema: [...]}`` / ``{config_schema: [...]}``
      dict.
    * A JSON literal (list or ``{schema: [...]}`` dict).

    Returns ``None`` when ``raw`` is ``None``.
    """
    import json

    if raw is None:
        return None
    if raw.startswith("@"):
        text = Path(raw[1:]).read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
    else:
        loaded = json.loads(raw)

    if isinstance(loaded, list):
        items = loaded
    elif isinstance(loaded, dict):
        items = loaded.get("schema") or loaded.get("config_schema") or loaded.get("items")
        if items is None:
            raise click.BadParameter(
                "config-schema dict must contain a 'schema', 'config_schema', or 'items' list"
            )
    else:
        raise click.BadParameter(
            f"config-schema must be a list or dict, got {type(loaded).__name__}"
        )
    if not isinstance(items, list):
        raise click.BadParameter("config-schema entries must be a list")
    return items


def _extract_config_schema(fields: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Pull ``config_schema`` out of the Click-parsed flags and materialize it.

    The DTO flag generator surfaces ``list[ConfigSchemaItem]`` as a repeatable
    ``--config-schema`` string flag — the user is expected to pass ``@file``
    once. We pop that raw value here and resolve it to the list payload before
    :func:`assemble_body` runs, so the field never hits the generic
    ``_is_list_str`` branch (which would collect strings).
    """
    raw = fields.pop("config_schema", None)
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        items: list[Any] = list(raw)
        if not items:
            return None
        if len(items) > 1:
            raise click.BadParameter(
                "--config-schema accepts a single @file or JSON literal"
            )
        raw = items[0]
    if isinstance(raw, str):
        return load_schema_file(raw)
    raise click.BadParameter(
        f"config-schema must be a string, got {type(raw).__name__}"
    )


_CREATE_FLAGS = build_cli_flags(
    IntegrationCreate,
    exclude=DTO_EXCLUDES.get("IntegrationCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("IntegrationCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    IntegrationUpdate,
    exclude=DTO_EXCLUDES.get("IntegrationUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("IntegrationUpdate", {}),
)

_MAPPING_CREATE_FLAGS = build_cli_flags(
    IntegrationMappingCreate,
    exclude=DTO_EXCLUDES.get("IntegrationMappingCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("IntegrationMappingCreate", {}),
)

_MAPPING_UPDATE_FLAGS = build_cli_flags(
    IntegrationMappingUpdate,
    exclude=DTO_EXCLUDES.get("IntegrationMappingUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("IntegrationMappingUpdate", {}),
)


@integrations_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_integrations(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all integrations (wrapped ``{items, total}`` payload)."""
    response = await client.get("/api/integrations")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@integrations_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_integration(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single integration by UUID or name (with mappings + OAuth config)."""
    integration_uuid = await resolver.resolve("integration", ref)
    response = await client.get(f"/api/integrations/{integration_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@integrations_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def create_integration(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new integration.

    ``--config-schema`` accepts a JSON literal or ``@path/to/schema.yaml``.
    The file's top level may be a list of schema items or a dict with a
    ``schema`` / ``config_schema`` / ``items`` list.
    """
    schema_items = _extract_config_schema(fields)
    body = await assemble_body(IntegrationCreate, fields, resolver=resolver)
    if schema_items is not None:
        body["config_schema"] = schema_items
    response = await client.post("/api/integrations", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@integrations_group.command("update")
@click.argument("ref")
@click.option(
    "--force-remove-keys",
    is_flag=True,
    default=False,
    help=(
        "Proceed even when the new --config-schema drops keys currently "
        "present on the integration (cascade-deletes related configs)."
    ),
)
@_apply_flags(_UPDATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def update_integration(
    ctx: click.Context,
    ref: str,
    force_remove_keys: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update an integration.

    ``REF`` is a UUID or integration name. When ``--config-schema`` drops
    keys that have saved ``Config`` values, the server refuses with 409
    unless ``--force-remove-keys`` is passed — removed keys cascade-delete
    related ``Config`` rows (integration-level defaults and per-org
    overrides).
    """
    integration_uuid = await resolver.resolve("integration", ref)
    schema_items = _extract_config_schema(fields)
    body = await assemble_body(IntegrationUpdate, fields, resolver=resolver)
    if schema_items is not None:
        body["config_schema"] = schema_items

    params = {"force_remove_keys": "true"} if force_remove_keys else {}
    response = await client.put(
        f"/api/integrations/{integration_uuid}", json=body, params=params
    )
    if response.status_code == 409:
        detail = response.json().get("detail", {})
        if isinstance(detail, dict) and detail.get("code") == (
            "integration_schema_removal_requires_confirmation"
        ):
            click.echo(
                f"Refusing to update {ref}: --config-schema drops "
                f"{len(detail.get('removed_keys', []))} key(s) "
                f"({', '.join(detail.get('removed_keys', []))}).",
                err=True,
            )
            click.echo(
                f"This will delete {detail.get('affected_config_values', 0)} "
                "stored Config value(s). Pass --force-remove-keys to proceed.",
                err=True,
            )
            sys.exit(1)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@integrations_group.command("add-mapping")
@click.argument("integration_ref")
@_apply_flags(_MAPPING_CREATE_FLAGS)
@click.option(
    "--oauth-token-id",
    "oauth_token_id_opt",
    default=None,
    type=str,
    help="OAuth token UUID (opt-in; empty means leave unset).",
)
@click.pass_context
@pass_resolver
@run_async
async def add_mapping(
    ctx: click.Context,
    integration_ref: str,
    oauth_token_id_opt: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a mapping between an integration and an organization.

    ``INTEGRATION_REF`` is a UUID or integration name. ``--organization`` is a
    UUID or org name (resolved via :class:`RefResolver`).

    ``--oauth-token-id`` is an opt-in flag outside the DTO-generated flag set —
    the DTO excludes ``oauth_token_id`` to avoid accidentally surfacing the
    UI-managed OAuth handshake data as a writable CLI field.
    """
    integration_uuid = await resolver.resolve("integration", integration_ref)
    body = await assemble_body(IntegrationMappingCreate, fields, resolver=resolver)
    if oauth_token_id_opt is not None:
        body["oauth_token_id"] = oauth_token_id_opt
    response = await client.post(
        f"/api/integrations/{integration_uuid}/mappings", json=body
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@integrations_group.command("update-mapping")
@click.argument("integration_ref")
@click.option(
    "--organization",
    "organization_ref",
    required=True,
    type=str,
    help="organization ref (UUID or name) — identifies the mapping to update.",
)
@_apply_flags(_MAPPING_UPDATE_FLAGS)
@click.option(
    "--oauth-token-id",
    "oauth_token_id_opt",
    default=None,
    type=str,
    help="OAuth token UUID (opt-in; omitted means leave unchanged).",
)
@click.pass_context
@pass_resolver
@run_async
async def update_mapping(
    ctx: click.Context,
    integration_ref: str,
    organization_ref: str,
    oauth_token_id_opt: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update an existing integration mapping.

    Resolves ``INTEGRATION_REF`` + ``--organization`` to the mapping UUID via
    ``GET /api/integrations/{id}/mappings/by-org/{org_id}``, then PUTs the
    update body. ``oauth_token_id`` is only sent when ``--oauth-token-id`` is
    explicitly passed — this preserves the server's existing token on
    unrelated updates (it's set by the OAuth flow, not by CLI users).
    """
    integration_uuid = await resolver.resolve("integration", integration_ref)
    organization_uuid = await resolver.resolve("org", organization_ref)

    lookup = await client.get(
        f"/api/integrations/{integration_uuid}/mappings/by-org/{organization_uuid}"
    )
    lookup.raise_for_status()
    mapping_id = str(lookup.json()["id"])

    body = await assemble_body(IntegrationMappingUpdate, fields, resolver=resolver)
    if oauth_token_id_opt is not None:
        body["oauth_token_id"] = oauth_token_id_opt
    response = await client.put(
        f"/api/integrations/{integration_uuid}/mappings/{mapping_id}", json=body
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["integrations_group"]
