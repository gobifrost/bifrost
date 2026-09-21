"""CLI commands for recurring PlatformJob triggers (quality schedules)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

recurring_triggers_group = entity_group(
    "recurring-triggers", "Manage recurring quality schedules."
)
_BASE = "/api/recurring-triggers"
_CREATE_FIELDS = {
    "operation_type",
    "operation_id",
    "operation_params",
    "cron_expression",
    "timezone",
    "overlap_policy",
}
_UPDATE_FIELDS = {"cron_expression", "timezone", "enabled", "operation_params"}


def _exit_usage(message: str) -> NoReturn:
    ctx = click.get_current_context(silent=True)
    obj = ctx.obj if ctx is not None and isinstance(ctx.obj, dict) else {}
    if obj.get("json_output"):
        click.echo(json.dumps({"error": "usage_error", "message": message}, sort_keys=True))
    else:
        click.echo(f"Error: {message}", err=True)
    raise click.exceptions.Exit(2)


def _raise_for_schedule_status(response) -> None:
    if response.status_code == 422:
        detail = None
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            detail = body.get("detail")
        _exit_usage(f"invalid request: {detail if detail is not None else response.text}")
    response.raise_for_status()


def _require_fields(body: dict[str, Any], fields: set[str]) -> None:
    missing = sorted(field for field in fields if body.get(field) in (None, "", []))
    if missing:
        _exit_usage(f"missing required field(s): {', '.join(missing)}")


def _load_file(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise click.BadParameter(f"cannot read file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise click.BadParameter(f"invalid JSON/YAML file: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise click.BadParameter("--file must contain a JSON/YAML object")
    return dict(loaded)


def _load_file_for_command(path: str | None, *, allowed: set[str]) -> dict[str, Any]:
    try:
        loaded = _load_file(path)
    except click.ClickException as exc:
        _exit_usage(exc.format_message())
    unknown = set(loaded) - allowed
    if unknown:
        _exit_usage(f"unknown field(s) in --file: {', '.join(sorted(unknown))}")
    return loaded


def _validate_uuid(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        UUID(value)
    except ValueError as exc:
        _exit_usage(f"{label} must be a UUID: {value}")
        raise exc  # unreachable; _exit_usage raises
    return value


@recurring_triggers_group.command("create")
@click.option("--file", "file_path", default=None, help="JSON/YAML request body.")
@click.option(
    "--operation",
    "operation",
    type=click.Choice(["agent_review", "agent_evaluation_suite"]),
    default=None,
)
@click.option("--operation-id", default=None, help="Review definition or suite UUID.")
@click.option("--params-file", default=None, help="JSON/YAML operation_params object.")
@click.option("--cron", "cron_expression", default=None, help="5-field cron expression.")
@click.option("--timezone", default=None, help="IANA timezone.")
@click.pass_context
@pass_resolver
@run_async
async def create_schedule(
    ctx: click.Context,
    file_path: str | None,
    operation: str | None,
    operation_id: str | None,
    params_file: str | None,
    cron_expression: str | None,
    timezone: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    body = _load_file_for_command(file_path, allowed=_CREATE_FIELDS)
    if operation is not None:
        body["operation_type"] = operation
    if operation_id is not None:
        _validate_uuid(operation_id, "--operation-id")
        body["operation_id"] = operation_id
    if params_file is not None:
        try:
            loaded = yaml.safe_load(Path(params_file).read_text(encoding="utf-8"))
        except OSError as exc:
            _exit_usage(f"cannot read params file: {exc}")
        except yaml.YAMLError as exc:
            _exit_usage(f"invalid JSON/YAML params file: {exc}")
        if not isinstance(loaded, dict):
            _exit_usage("--params-file must contain a JSON/YAML object")
        body["operation_params"] = loaded
    if cron_expression is not None:
        body["cron_expression"] = cron_expression
    if timezone is not None:
        body["timezone"] = timezone
    _require_fields(body, {"operation_type", "operation_id", "cron_expression"})
    response = await client.post(_BASE, json=body)
    _raise_for_schedule_status(response)
    output_result(response.json(), ctx=ctx)


@recurring_triggers_group.command("list")
@click.option(
    "--operation",
    "operation",
    type=click.Choice(["agent_review", "agent_evaluation_suite"]),
    default=None,
)
@click.option("--org-id", default=None, help="Organization UUID (admin only).")
@click.option("--enabled/--disabled", "enabled", default=None)
@click.option("--limit", type=click.IntRange(1, 200), default=50)
@click.option("--offset", type=click.IntRange(0), default=0)
@click.pass_context
@pass_resolver
@run_async
async def list_schedules(
    ctx: click.Context,
    operation: str | None,
    org_id: str | None,
    enabled: bool | None,
    limit: int,
    offset: int,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if operation is not None:
        params["operation_type"] = operation
    if org_id is not None:
        _validate_uuid(org_id, "--org-id")
        params["organization_id"] = org_id
    if enabled is not None:
        params["enabled"] = enabled
    response = await client.get(_BASE, params=params)
    _raise_for_schedule_status(response)
    output_result(response.json(), ctx=ctx)


@recurring_triggers_group.command("get")
@click.argument("trigger_id")
@click.pass_context
@pass_resolver
@run_async
async def get_schedule_cmd(
    ctx: click.Context,
    trigger_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    _validate_uuid(trigger_id, "trigger_id")
    response = await client.get(f"{_BASE}/{trigger_id}")
    _raise_for_schedule_status(response)
    output_result(response.json(), ctx=ctx)


@recurring_triggers_group.command("update")
@click.argument("trigger_id")
@click.option("--file", "file_path", default=None, help="JSON/YAML request body.")
@click.option("--cron", "cron_expression", default=None)
@click.option("--timezone", default=None)
@click.option("--params-file", default=None, help="JSON/YAML operation_params object.")
@click.option("--enable", "enable", is_flag=True, default=False)
@click.option("--disable", "disable", is_flag=True, default=False)
@click.pass_context
@pass_resolver
@run_async
async def update_schedule_cmd(
    ctx: click.Context,
    trigger_id: str,
    file_path: str | None,
    cron_expression: str | None,
    timezone: str | None,
    params_file: str | None,
    enable: bool,
    disable: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    _validate_uuid(trigger_id, "trigger_id")
    if enable and disable:
        _exit_usage("Specify either --enable or --disable, not both.")
    body = _load_file_for_command(file_path, allowed=_UPDATE_FIELDS)
    if cron_expression is not None:
        body["cron_expression"] = cron_expression
    if timezone is not None:
        body["timezone"] = timezone
    if params_file is not None:
        try:
            loaded = yaml.safe_load(Path(params_file).read_text(encoding="utf-8"))
        except OSError as exc:
            _exit_usage(f"cannot read params file: {exc}")
        except yaml.YAMLError as exc:
            _exit_usage(f"invalid JSON/YAML params file: {exc}")
        if not isinstance(loaded, dict):
            _exit_usage("--params-file must contain a JSON/YAML object")
        body["operation_params"] = loaded
    if enable:
        body["enabled"] = True
    if disable:
        body["enabled"] = False
    if not body:
        _exit_usage("Specify at least one update field.")
    response = await client.patch(f"{_BASE}/{trigger_id}", json=body)
    _raise_for_schedule_status(response)
    output_result(response.json(), ctx=ctx)


@recurring_triggers_group.command("disable")
@click.argument("trigger_id")
@click.pass_context
@pass_resolver
@run_async
async def disable_schedule(
    ctx: click.Context,
    trigger_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    _validate_uuid(trigger_id, "trigger_id")
    response = await client.patch(f"{_BASE}/{trigger_id}", json={"enabled": False})
    _raise_for_schedule_status(response)
    output_result(response.json(), ctx=ctx)


@recurring_triggers_group.command("fires")
@click.argument("trigger_id")
@click.option("--limit", type=click.IntRange(1, 200), default=50)
@click.option("--offset", type=click.IntRange(0), default=0)
@click.pass_context
@pass_resolver
@run_async
async def list_fires_cmd(
    ctx: click.Context,
    trigger_id: str,
    limit: int,
    offset: int,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    _validate_uuid(trigger_id, "trigger_id")
    response = await client.get(
        f"{_BASE}/{trigger_id}/fires", params={"limit": limit, "offset": offset}
    )
    _raise_for_schedule_status(response)
    output_result(response.json(), ctx=ctx)
