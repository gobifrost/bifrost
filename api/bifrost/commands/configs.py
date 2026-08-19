"""CLI commands for managing configuration key-value pairs.

Implements Task 5h of the CLI mutation surface plan:

* ``bifrost configs list`` → ``GET /api/config``
* ``bifrost configs get <ref>`` → ``GET /api/config/{uuid}`` (the ref is
  resolved to a UUID first, so a config key works too)
* ``bifrost configs create`` → ``POST /api/config`` (flags from
  :class:`ConfigCreate`; ``config_type`` aliases to ``type`` on the wire.
  ``value`` is a string for every config type — non-string types travel
  serialized and are coerced on read.)
* ``bifrost configs update <ref>`` → ``PUT /api/config/{uuid}`` (flags from
  :class:`ConfigUpdate`; omitting ``--value`` preserves the stored value via
  server-side omit-unset behaviour)
* ``bifrost configs delete <ref>`` → ``DELETE /api/config/{uuid}`` with a
  ``--confirm`` guard for ``secret``-type configs
* ``bifrost configs set <key> --value X --organization <ref>`` — upsert
  wrapper (catalog open question #2 decision): GET the config list, route to
  PUT if a matching ``(key, organization_id)`` row exists, otherwise POST

Refs resolve via :class:`RefResolver`: configs use ``key`` (not ``name``) as
the natural identifier, so ``configs update foo`` resolves against the stored
``key`` column. The ``set`` command's key is taken verbatim because it may not
exist yet.
"""

from __future__ import annotations

import sys
from typing import Any
from uuid import UUID

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
from bifrost.contracts import ConfigCreate, ConfigType, ConfigUpdate

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

configs_group = entity_group("configs", "Manage configuration values.")

# Enum choices for the ``--type`` flag on ``set``. Mirrors the ``type`` field on
# ``SetConfigRequest`` (and ``ConfigCreate.config_type`` after aliasing).
_CONFIG_TYPE_CHOICES = [member.value for member in ConfigType]


_CREATE_FLAGS = build_cli_flags(
    ConfigCreate,
    exclude=DTO_EXCLUDES.get("ConfigCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("ConfigCreate", {}),
)

_UPDATE_FLAGS = build_cli_flags(
    ConfigUpdate,
    exclude=DTO_EXCLUDES.get("ConfigUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("ConfigUpdate", {}),
)


@configs_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_configs(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all configuration values."""
    response = await client.get("/api/config")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@configs_group.command("get")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_config(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single configuration value by UUID or key.

    ``REF`` is a UUID or config key; keys resolve via :class:`RefResolver`.
    Secret values come back masked as ``[SECRET]``.
    """
    config_uuid = await resolver.resolve("config", ref)
    response = await client.get(f"/api/config/{config_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


async def _build_create_body(
    resolver: RefResolver,
    *,
    key: Any,
    value: Any,
    config_type: Any,
    description: Any,
    org: str | None,
    is_global: bool,
) -> dict[str, Any]:
    """Build a POST /api/config body for ``create`` / ``set``.

    Delegates to :func:`assemble_body`, which applies the ``config_type`` →
    ``type`` wire alias. ``type`` is defaulted explicitly here because
    ``assemble_body`` drops unset fields and ``SetConfigRequest.type`` is
    required.

    Org targeting follows the unified ``--org`` standard: HOME (omit) sends no
    ``organization_id`` (the server uses the caller's org), GLOBAL sends an
    explicit null, and ``--org <id|name>`` sends the resolved UUID.
    """
    if not key:
        raise click.UsageError("--key is required")
    if value is None:
        raise click.UsageError("--value is required")
    body = await assemble_body(
        ConfigCreate,
        {"key": key, "value": value, "config_type": config_type, "description": description},
        resolver=resolver,
    )
    body.setdefault("type", ConfigType.STRING.value)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id
    return body


@configs_group.command("create")
@_apply_flags(_CREATE_FLAGS)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_config(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new configuration value."""
    body = await _build_create_body(
        resolver,
        key=fields.get("key"),
        value=fields.get("value"),
        config_type=fields.get("config_type"),
        description=fields.get("description"),
        org=org,
        is_global=is_global,
    )
    response = await client.post("/api/config", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@configs_group.command("update")
@click.argument("ref")
@_apply_flags(_UPDATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def update_config(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update a configuration value.

    ``REF`` is a UUID or config key. Names are resolved via
    :class:`RefResolver`; ambiguous keys fail loudly with the candidate list.

    Omitting ``--value`` preserves the stored value (server-side omit-unset
    behaviour — particularly important for ``secret``-type configs, where
    the plaintext value is never returned and cannot be round-tripped).
    """
    config_uuid = await resolver.resolve("config", ref)
    body = await assemble_body(ConfigUpdate, fields, resolver=resolver)
    response = await client.put(f"/api/config/{config_uuid}", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@configs_group.command("delete")
@click.argument("ref")
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Required when deleting a secret-type config (safety guard).",
)
@click.pass_context
@pass_resolver
@run_async
async def delete_config(
    ctx: click.Context,
    ref: str,
    confirm: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete a configuration value by UUID or key.

    Secret-type configs require ``--confirm`` so a typo'd ``bifrost configs
    delete`` doesn't silently wipe an encrypted value that cannot be
    recovered from the server.
    """
    config_uuid = await resolver.resolve("config", ref)

    # Read the row to check its type before deleting. A 404 here is left to
    # the DELETE below so the server owns the not-found response.
    get_response = await client.get(f"/api/config/{config_uuid}")
    existing = (
        get_response.json() if get_response.status_code == 200 else None
    )

    if existing is not None and existing.get("type") == ConfigType.SECRET.value and not confirm:
        click.echo(
            f"Refusing to delete secret config {existing.get('key')!r} without "
            "--confirm. Secrets cannot be recovered once deleted.",
            err=True,
        )
        sys.exit(1)

    response = await client.delete(f"/api/config/{config_uuid}")
    response.raise_for_status()
    output_result({"deleted": config_uuid}, ctx=ctx)


@configs_group.command("set")
@click.argument("key")
@click.option("--value", required=True, help="Config value (plain string).")
@org_option
@click.option(
    "--type",
    "config_type",
    type=click.Choice(_CONFIG_TYPE_CHOICES),
    default=None,
    help=(
        "Config type (enum). On create, defaults to 'string'. On update, "
        "omit to preserve the existing type."
    ),
)
@click.option(
    "--description",
    default=None,
    help="Optional description of this config entry.",
)
@click.pass_context
@pass_resolver
@run_async
async def set_config(
    ctx: click.Context,
    key: str,
    value: str,
    org: str | None,
    is_global: bool,
    config_type: str | None,
    description: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Upsert a configuration value (catalog open question #2: yes).

    Looks up an existing config matching ``(key, organization_id)`` by
    listing ``/api/config`` client-side — the endpoint does not accept a
    ``key`` query parameter, so filtering happens in the CLI. PUTs the
    existing row if found; POSTs otherwise. The result is idempotent from
    the caller's perspective.

    Org targeting follows the unified ``--org`` standard: HOME (omit) targets
    the caller's org, ``--global`` targets global, ``--org <id|name>`` targets
    that org. The scope filter only narrows the upsert to an explicit scope
    when one was given (GLOBAL or ORG); HOME falls back to key-only matching.
    """
    target = await resolve_org_target(org, is_global, resolver)
    # Explicit scope (GLOBAL or ORG) narrows the upsert match by organization_id.
    # HOME (unset) matches by key alone and lets the server resolve the scope.
    org_uuid = target.organization_id if target.is_set else None

    list_response = await client.get("/api/config")
    list_response.raise_for_status()
    existing = _find_config_by_key(
        list_response.json(),
        key,
        org_uuid,
        scope_filter=target.is_set,
    )

    if existing is not None:
        update_body: dict[str, Any] = {"value": value}
        if config_type is not None:
            update_body["type"] = config_type
        if description is not None:
            update_body["description"] = description
        response = await client.put(f"/api/config/{existing['id']}", json=update_body)
    else:
        # POST requires ``type`` (SetConfigRequest.type is non-optional);
        # default to 'string' when the caller did not specify one.
        create_body: dict[str, Any] = {
            "key": key,
            "value": value,
            "type": config_type or ConfigType.STRING.value,
        }
        if description is not None:
            create_body["description"] = description
        if target.is_set:
            create_body["organization_id"] = target.organization_id
        response = await client.post("/api/config", json=create_body)

    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


def _find_config_by_key(
    items: list[dict[str, Any]],
    key: str,
    org_uuid: str | None,
    *,
    scope_filter: bool,
) -> dict[str, Any] | None:
    """Return the config dict matching ``key`` in the requested scope.

    When ``scope_filter`` is True (user passed ``--organization``), match
    ``(key, organization_id)`` exactly so ``set`` never accidentally PUTs a
    global row when the user asked for an org-specific one.

    When ``scope_filter`` is False (no ``--organization`` flag), match by
    ``key`` alone — the server will resolve the scope the same way on POST
    (defaults to the caller's org). Multiple matches (same key in global +
    org, for example) are treated as "no unique target" and return ``None``
    so the command falls through to POST and surfaces the server-side
    conflict rather than silently PUTting the wrong row.
    """
    target_org = _coerce_uuid(org_uuid) if org_uuid is not None else None
    candidates = [item for item in items if item.get("key") == key]
    if scope_filter:
        for item in candidates:
            item_org = _coerce_uuid(item.get("org_id"))
            if item_org == target_org:
                return item
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = ["configs_group"]
