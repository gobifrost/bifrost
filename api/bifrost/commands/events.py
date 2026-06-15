"""CLI commands for managing event sources and subscriptions.

Implements Task 5j of the CLI mutation surface plan plus the discovery
parity follow-up:

* ``bifrost events list-sources`` → ``GET /api/events/sources``
* ``bifrost events get-source <ref>`` → ``GET /api/events/sources/{uuid}``
* ``bifrost events list-subscriptions <source-ref>`` →
  ``GET /api/events/sources/{source_uuid}/subscriptions``
* ``bifrost events get-subscription <source-ref> <subscription-id>`` —
  list-and-filter (the server has no per-subscription GET endpoint).
* ``bifrost events create-source`` → ``POST /api/events/sources`` (body from
  :class:`EventSourceCreate`)
* ``bifrost events update-source <ref>`` → ``PATCH /api/events/sources/{uuid}``
  (body from :class:`EventSourceUpdate`; unset flags omitted by
  :func:`assemble_body`)
* ``bifrost events subscribe <source-ref>`` →
  ``POST /api/events/sources/{source_uuid}/subscriptions``
  (body from :class:`EventSubscriptionCreate`)
* ``bifrost events update-subscription <source-ref> <subscription-id>`` →
  ``PATCH /api/events/sources/{source_uuid}/subscriptions/{subscription_id}``
  (body from :class:`EventSubscriptionUpdate`)

Flat-to-nested translation
--------------------------

The :class:`EventSourceCreate` / :class:`EventSourceUpdate` DTOs carry nested
``schedule: ScheduleSourceConfig`` and ``webhook: WebhookSourceConfig``
objects. Surfacing those as raw JSON-blob flags is hostile, so this module
exposes flat top-level flags that the command body collapses back into the
nested payload:

* ``--cron`` / ``--timezone`` / ``--schedule-enabled`` →
  ``schedule: {cron_expression, timezone, enabled}``
* ``--adapter`` / ``--webhook-integration`` (resolved via
  :class:`RefResolver` as an integration ref) / ``--webhook-config @file.yaml``
  → ``webhook: {adapter_name, integration_id, config}``

These flat flags are registered manually because the DTO-level ``schedule`` /
``webhook`` fields are excluded from :func:`build_cli_flags` via
``DTO_EXCLUDES``. The top-level fields (``name``, ``source_type``,
``organization_id``, ``is_active``) still go through the generator.

Subscribe target
----------------

``bifrost events subscribe`` accepts exactly one of ``--workflow`` or
``--agent`` (portable refs; ``path::func`` for workflows, name/UUID for
agents). ``target_type`` is inferred from which flag was supplied.

``bifrost events update-subscription`` refuses to change ``target_type`` /
``workflow_id`` / ``agent_id`` — the delivery history and external
subscription state are tied to the target, so changing the target means
"delete and recreate."
"""

from __future__ import annotations

from typing import Any, Callable

import click

from bifrost.client import BifrostClient
from bifrost.dto_flags import (
    DTO_EXCLUDES,
    DTO_REF_LOOKUPS,
    assemble_body,
    build_cli_flags,
    load_dict_value,
)
from bifrost.org_target import org_option, resolve_org_target
from bifrost.refs import RefResolver
from bifrost.contracts import (
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
)

from .base import _apply_flags, entity_group, output_result, pass_resolver, run_async

events_group = entity_group("events", "Manage event sources and subscriptions.")


_SOURCE_CREATE_FLAGS = build_cli_flags(
    EventSourceCreate,
    exclude=DTO_EXCLUDES.get("EventSourceCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("EventSourceCreate", {}),
)

_SOURCE_UPDATE_FLAGS = build_cli_flags(
    EventSourceUpdate,
    exclude=DTO_EXCLUDES.get("EventSourceUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("EventSourceUpdate", {}),
)

_SUBSCRIPTION_CREATE_FLAGS = build_cli_flags(
    EventSubscriptionCreate,
    exclude=DTO_EXCLUDES.get("EventSubscriptionCreate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("EventSubscriptionCreate", {}),
)

_SUBSCRIPTION_UPDATE_FLAGS = build_cli_flags(
    EventSubscriptionUpdate,
    exclude=DTO_EXCLUDES.get("EventSubscriptionUpdate", set()),
    verb_ref_lookups=DTO_REF_LOOKUPS.get("EventSubscriptionUpdate", {}),
)


# ---------------------------------------------------------------------------
# Flat-flag decorators for the nested ``schedule`` / ``webhook`` configs.
# ---------------------------------------------------------------------------


def _schedule_flags(
    fn: Callable[..., Any],
) -> Callable[..., Any]:
    """Attach ``--cron`` / ``--timezone`` / ``--schedule-enabled`` options.

    These collapse into ``schedule: {cron_expression, timezone, enabled}`` at
    body-assembly time. ``--schedule-enabled/--no-schedule-enabled`` is
    tri-state so omitting it leaves the default alone.
    """
    fn = click.option(
        "--schedule-enabled/--no-schedule-enabled",
        "schedule_enabled",
        default=None,
        help="Whether the schedule is enabled (collapses into schedule config).",
    )(fn)
    fn = click.option(
        "--timezone",
        "schedule_timezone",
        type=str,
        default=None,
        help="Schedule timezone, e.g. 'UTC' (collapses into schedule config).",
    )(fn)
    fn = click.option(
        "--cron",
        "schedule_cron",
        type=str,
        default=None,
        help="Cron expression, e.g. '*/5 * * * *' (collapses into schedule config).",
    )(fn)
    return fn


def _webhook_flags(
    fn: Callable[..., Any],
) -> Callable[..., Any]:
    """Attach ``--adapter`` / ``--webhook-integration`` / ``--webhook-config`` options.

    These collapse into ``webhook: {adapter_name, integration_id, config}`` at
    body-assembly time. ``--webhook-integration`` accepts an integration ref
    (UUID or name); ``--webhook-config`` accepts a JSON literal or
    ``@path/to/config.yaml``.
    """
    fn = click.option(
        "--webhook-config",
        "webhook_config",
        type=str,
        default=None,
        help=(
            "Webhook adapter config as JSON literal or @path/to/file.yaml "
            "(collapses into webhook config)."
        ),
    )(fn)
    fn = click.option(
        "--webhook-integration",
        "webhook_integration",
        type=str,
        default=None,
        help=(
            "Integration ref (UUID or name) for OAuth-based adapters "
            "(collapses into webhook config)."
        ),
    )(fn)
    fn = click.option(
        "--adapter",
        "webhook_adapter",
        type=str,
        default=None,
        help="Webhook adapter name (collapses into webhook config).",
    )(fn)
    return fn


# ---------------------------------------------------------------------------
# Body builders for the flat→nested translation.
# ---------------------------------------------------------------------------


async def _build_schedule_config(
    *,
    cron: str | None,
    timezone: str | None,
    enabled: bool | None,
) -> dict[str, Any] | None:
    """Collapse ``--cron`` / ``--timezone`` / ``--schedule-enabled`` into a dict.

    Returns ``None`` when none of the three was supplied — the caller treats
    that as "leave the DTO's ``schedule`` field unset."
    """
    if cron is None and timezone is None and enabled is None:
        return None
    config: dict[str, Any] = {}
    if cron is not None:
        config["cron_expression"] = cron
    if timezone is not None:
        config["timezone"] = timezone
    if enabled is not None:
        config["enabled"] = enabled
    return config


async def _build_webhook_config(
    *,
    adapter: str | None,
    integration_ref: str | None,
    config_raw: str | None,
    resolver: RefResolver,
) -> dict[str, Any] | None:
    """Collapse ``--adapter`` / ``--webhook-integration`` / ``--webhook-config``
    into a dict.

    Returns ``None`` when none of the three was supplied.
    """
    if adapter is None and integration_ref is None and config_raw is None:
        return None
    config: dict[str, Any] = {}
    if adapter is not None:
        config["adapter_name"] = adapter
    if integration_ref is not None:
        config["integration_id"] = await resolver.resolve("integration", integration_ref)
    if config_raw is not None:
        config["config"] = load_dict_value(config_raw)
    return config


def _pop_schedule_fields(fields: dict[str, Any]) -> tuple[str | None, str | None, bool | None]:
    """Remove schedule-flat fields from ``fields`` and return them."""
    return (
        fields.pop("schedule_cron", None),
        fields.pop("schedule_timezone", None),
        fields.pop("schedule_enabled", None),
    )


def _pop_webhook_fields(fields: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Remove webhook-flat fields from ``fields`` and return them."""
    return (
        fields.pop("webhook_adapter", None),
        fields.pop("webhook_integration", None),
        fields.pop("webhook_config", None),
    )


# ---------------------------------------------------------------------------
# Event source commands
# ---------------------------------------------------------------------------


@events_group.command("list-sources")
@click.pass_context
@pass_resolver
@run_async
async def list_sources(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List all event sources (wrapped ``{items, total}`` payload)."""
    response = await client.get("/api/events/sources")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@events_group.command("get-source")
@click.argument("ref")
@click.pass_context
@pass_resolver
@run_async
async def get_source(
    ctx: click.Context,
    ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single event source by UUID or name."""
    source_uuid = await resolver.resolve("event_source", ref)
    response = await client.get(f"/api/events/sources/{source_uuid}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@events_group.command("list-subscriptions")
@click.argument("source_ref")
@click.pass_context
@pass_resolver
@run_async
async def list_subscriptions(
    ctx: click.Context,
    source_ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List subscriptions for an event source.

    ``SOURCE_REF`` is a UUID or event source name.
    """
    source_uuid = await resolver.resolve("event_source", source_ref)
    response = await client.get(
        f"/api/events/sources/{source_uuid}/subscriptions"
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@events_group.command("get-subscription")
@click.argument("source_ref")
@click.argument("subscription_id")
@click.pass_context
@pass_resolver
@run_async
async def get_subscription(
    ctx: click.Context,
    source_ref: str,
    subscription_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a single subscription by source ref + subscription UUID.

    The server has no per-subscription GET endpoint, so this lists the
    source's subscriptions and filters client-side.
    """
    source_uuid = await resolver.resolve("event_source", source_ref)
    response = await client.get(
        f"/api/events/sources/{source_uuid}/subscriptions"
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("items", []) if isinstance(data, dict) else data
    for item in items:
        if str(item.get("id")) == subscription_id:
            output_result(item, ctx=ctx)
            return
    raise click.ClickException(
        f"subscription {subscription_id!r} not found on source {source_ref!r}"
    )


@events_group.command("create-source")
@_apply_flags(_SOURCE_CREATE_FLAGS)
@_schedule_flags
@_webhook_flags
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_source(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Create a new event source.

    Flat-to-nested flags: ``--cron`` / ``--timezone`` / ``--schedule-enabled``
    collapse into the schedule config; ``--adapter`` /
    ``--webhook-integration`` / ``--webhook-config`` collapse into the webhook
    config. At least one of each group is required when ``--source-type`` is
    ``schedule`` or ``webhook``, respectively — the API validates the shape.

    Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
    source to the caller's org, ``--global`` makes it global, ``--org
    <id|name>`` scopes it to that org.
    """
    cron, tz, enabled = _pop_schedule_fields(fields)
    adapter, integration_ref, webhook_config_raw = _pop_webhook_fields(fields)

    body = await assemble_body(EventSourceCreate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id

    schedule = await _build_schedule_config(cron=cron, timezone=tz, enabled=enabled)
    if schedule is not None:
        body["schedule"] = schedule

    webhook = await _build_webhook_config(
        adapter=adapter,
        integration_ref=integration_ref,
        config_raw=webhook_config_raw,
        resolver=resolver,
    )
    if webhook is not None:
        body["webhook"] = webhook

    response = await client.post("/api/events/sources", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@events_group.command("update-source")
@click.argument("ref")
@_apply_flags(_SOURCE_UPDATE_FLAGS)
@_schedule_flags
@_webhook_flags
@org_option
@click.pass_context
@pass_resolver
@run_async
async def update_source(
    ctx: click.Context,
    ref: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update an event source.

    ``REF`` is a UUID or event source name. Flat-to-nested flags behave the
    same as on ``create-source`` — if any flat schedule / webhook flag is
    supplied, the corresponding nested object is rebuilt and patched.

    Passing ``--org``/``--global`` re-scopes the source (HOME leaves the scope
    unchanged, since omitting org sends no ``organization_id``).
    """
    source_uuid = await resolver.resolve("event_source", ref)

    cron, tz, enabled = _pop_schedule_fields(fields)
    adapter, integration_ref, webhook_config_raw = _pop_webhook_fields(fields)

    body = await assemble_body(EventSourceUpdate, fields, resolver=resolver)
    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id

    schedule = await _build_schedule_config(cron=cron, timezone=tz, enabled=enabled)
    if schedule is not None:
        body["schedule"] = schedule

    webhook = await _build_webhook_config(
        adapter=adapter,
        integration_ref=integration_ref,
        config_raw=webhook_config_raw,
        resolver=resolver,
    )
    if webhook is not None:
        body["webhook"] = webhook

    response = await client.patch(f"/api/events/sources/{source_uuid}", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


# ---------------------------------------------------------------------------
# Event subscription commands
# ---------------------------------------------------------------------------


@events_group.command("subscribe")
@click.argument("source_ref")
@_apply_flags(_SUBSCRIPTION_CREATE_FLAGS)
@click.pass_context
@pass_resolver
@run_async
async def subscribe(
    ctx: click.Context,
    source_ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Subscribe a workflow or agent to an event source.

    ``SOURCE_REF`` is a UUID or event source name. Supply exactly one of
    ``--workflow`` or ``--agent`` (portable refs). ``target_type`` is
    inferred from which flag was used and overrides any ``--target-type``
    the DTO generator may surface.
    """
    workflow_ref = fields.get("workflow_id")
    agent_ref = fields.get("agent_id")

    if workflow_ref and agent_ref:
        raise click.UsageError(
            "--workflow and --agent are mutually exclusive; pick one target."
        )
    if not workflow_ref and not agent_ref:
        raise click.UsageError("exactly one of --workflow / --agent is required.")

    # Force the inferred target_type — DTO default is "workflow" but the
    # user may have passed an explicit value; agent refs imply agent target.
    if agent_ref:
        fields["target_type"] = "agent"
    else:
        fields["target_type"] = "workflow"

    source_uuid = await resolver.resolve("event_source", source_ref)
    body = await assemble_body(EventSubscriptionCreate, fields, resolver=resolver)

    response = await client.post(
        f"/api/events/sources/{source_uuid}/subscriptions", json=body
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@events_group.command("update-subscription")
@click.argument("source_ref")
@click.argument("subscription_id")
@_apply_flags(_SUBSCRIPTION_UPDATE_FLAGS)
# Additional rejection-only flags: not part of EventSubscriptionUpdate — surfaced
# here so we can detect and refuse the attempt with a clear error instead of
# silently ignoring ``--workflow new_wf``.
@click.option(
    "--workflow",
    "workflow_ref",
    type=str,
    default=None,
    help="Rejected: changing the target workflow requires delete + recreate.",
)
@click.option(
    "--agent",
    "agent_ref",
    type=str,
    default=None,
    help="Rejected: changing the target agent requires delete + recreate.",
)
@click.option(
    "--target-type",
    "target_type",
    type=click.Choice(["workflow", "agent"]),
    default=None,
    help="Rejected: changing the target type requires delete + recreate.",
)
@click.pass_context
@pass_resolver
@run_async
async def update_subscription(
    ctx: click.Context,
    source_ref: str,
    subscription_id: str,
    workflow_ref: str | None,
    agent_ref: str | None,
    target_type: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
    **fields: Any,
) -> None:
    """Update an event subscription.

    ``SOURCE_REF`` is a UUID or event source name; ``SUBSCRIPTION_ID`` is the
    subscription's UUID. Only filter / delivery fields are mutable —
    ``--workflow`` / ``--agent`` / ``--target-type`` are surfaced only so we
    can refuse the attempt with a clear error. Delete and recreate if you
    need to change the target.
    """
    # Reject target-changing flags before any network traffic. The
    # rejection-only flags above (``--workflow`` / ``--agent`` /
    # ``--target-type``) are the only way a user could try to change the
    # target — the DTO-generated flags don't expose ``workflow_id`` /
    # ``agent_id`` / ``target_type`` on update, by design.
    if workflow_ref is not None or agent_ref is not None or target_type is not None:
        raise click.UsageError(
            "Cannot change target_type / workflow_id / agent_id on a subscription. "
            "Delete the subscription and create a new one instead."
        )

    source_uuid = await resolver.resolve("event_source", source_ref)
    body = await assemble_body(EventSubscriptionUpdate, fields, resolver=resolver)

    response = await client.patch(
        f"/api/events/sources/{source_uuid}/subscriptions/{subscription_id}",
        json=body,
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["events_group"]
