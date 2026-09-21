"""CLI commands for on-demand agent reviews."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.commands.usage import _compact_human_report
from bifrost.platform_jobs import poll_platform_job
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

agent_reviews_group = entity_group("agent-reviews", "Manage on-demand agent reviews.")
_BASE = "/api/agent-reviews"
_CREATE_FIELDS = {"agent_id", "organization_id", "name", "review_statement", "evidence_format_instructions", "model_profile_id"}
_VERSION_FIELDS = {"review_statement", "evidence_format_instructions", "model_profile_id"}
_RUN_FIELDS = {"run_ids"}



def _exit_usage(message: str) -> NoReturn:
    ctx = click.get_current_context(silent=True)
    obj = ctx.obj if ctx is not None and isinstance(ctx.obj, dict) else {}
    if obj.get("json_output"):
        click.echo(json.dumps({"error": "usage_error", "message": message}, sort_keys=True))
    else:
        click.echo(f"Error: {message}", err=True)
    raise click.exceptions.Exit(2)



def _raise_for_review_status(response) -> None:
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


def _csv_uuid_values_for_command(raw: str | None, label: str) -> list[str]:
    try:
        return _csv_uuid_values(raw, label)
    except click.ClickException as exc:
        _exit_usage(exc.format_message())

def _read_text(value: str | None, file_path: str | None, label: str) -> str | None:
    if value is not None and file_path is not None:
        raise click.UsageError(f"Specify either --{label} or --{label}-file, not both.")
    if file_path is not None:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise click.BadParameter(f"cannot read {label} file: {exc}") from exc
    return value


def _read_text_for_command(value: str | None, file_path: str | None, label: str) -> str | None:
    try:
        return _read_text(value, file_path, label)
    except click.ClickException as exc:
        _exit_usage(exc.format_message())


def _csv_uuid_values(raw: str | None, label: str) -> list[str]:
    if raw is None:
        return []
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise click.UsageError(f"{label} must include at least one UUID.")
    for value in values:
        try:
            UUID(value)
        except ValueError as exc:
            raise click.UsageError(f"{label} contains an invalid UUID: {value}") from exc
    return values



@agent_reviews_group.command("create")
@click.option("--file", "file_path", default=None, help="JSON/YAML request body.")
@click.option("--agent", "agent_ref", default=None, help="Agent name or UUID.")
@click.option("--name", default=None, help="Review definition name.")
@click.option("--statement", default=None, help="Review statement.")
@click.option("--statement-file", default=None, help="Read review statement from PATH.")
@click.option("--evidence-format", default=None, help="Evidence formatting instructions.")
@click.option("--evidence-format-file", default=None, help="Read evidence formatting instructions from PATH.")
@click.option("--profile-id", default=None, help="Explicit model profile UUID (admin only).")
@click.option("--org-id", default=None, help="Organization UUID or 'global'.")
@click.pass_context
@pass_resolver
@run_async
async def create_review(ctx: click.Context, file_path: str | None, agent_ref: str | None, name: str | None, statement: str | None, statement_file: str | None, evidence_format: str | None, evidence_format_file: str | None, profile_id: str | None, org_id: str | None, *, client: BifrostClient, resolver: RefResolver) -> None:
    body = _load_file_for_command(file_path, allowed=_CREATE_FIELDS)
    if agent_ref is not None:
        body["agent_id"] = await resolver.resolve("agent", agent_ref)
    if name is not None:
        body["name"] = name
    statement_value = _read_text_for_command(statement, statement_file, "statement")
    if statement_value is not None:
        body["review_statement"] = statement_value
    evidence_value = _read_text_for_command(evidence_format, evidence_format_file, "evidence-format")
    if evidence_value is not None:
        body["evidence_format_instructions"] = evidence_value
    if profile_id is not None:
        body["model_profile_id"] = profile_id
    if org_id is not None:
        body["organization_id"] = None if org_id == "global" else org_id
    _require_fields(body, {"agent_id", "name", "review_statement"})
    response = await client.post(_BASE, json=body)
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("list")
@click.option("--agent", "agent_ref", required=True, help="Agent name or UUID.")
@click.option("--status", "status_filter", type=click.Choice(["active", "disabled", "all"]), default="active")
@click.option("--limit", type=click.IntRange(1, 200), default=50)
@click.option("--offset", type=click.IntRange(0), default=0)
@click.pass_context
@pass_resolver
@run_async
async def list_reviews(ctx: click.Context, agent_ref: str, status_filter: str, limit: int, offset: int, *, client: BifrostClient, resolver: RefResolver) -> None:
    agent_id = await resolver.resolve("agent", agent_ref)
    response = await client.get(_BASE, params={"agent_id": agent_id, "status": status_filter, "limit": limit, "offset": offset})
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("get")
@click.argument("review_id")
@click.pass_context
@pass_resolver
@run_async
async def get_review(ctx: click.Context, review_id: str, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    response = await client.get(f"{_BASE}/{review_id}")
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("update")
@click.argument("review_id")
@click.option("--name", default=None)
@click.option("--status", "status_value", type=click.Choice(["active", "disabled"]), default=None)
@click.pass_context
@pass_resolver
@run_async
async def update_review(ctx: click.Context, review_id: str, name: str | None, status_value: str | None, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if status_value is not None:
        body["status"] = status_value
    if not body:
        _exit_usage("Specify at least one update field.")
    response = await client.patch(f"{_BASE}/{review_id}", json=body)
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("version")
@click.argument("review_id")
@click.option("--file", "file_path", default=None, help="JSON/YAML request body.")
@click.option("--statement", default=None, help="Review statement.")
@click.option("--statement-file", default=None, help="Read review statement from PATH.")
@click.option("--evidence-format", default=None, help="Evidence formatting instructions.")
@click.option("--evidence-format-file", default=None, help="Read evidence formatting instructions from PATH.")
@click.option("--profile-id", default=None, help="Explicit model profile UUID (admin only).")
@click.pass_context
@pass_resolver
@run_async
async def create_version(ctx: click.Context, review_id: str, file_path: str | None, statement: str | None, statement_file: str | None, evidence_format: str | None, evidence_format_file: str | None, profile_id: str | None, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    body = _load_file_for_command(file_path, allowed=_VERSION_FIELDS)
    statement_value = _read_text_for_command(statement, statement_file, "statement")
    if statement_value is not None:
        body["review_statement"] = statement_value
    evidence_value = _read_text_for_command(evidence_format, evidence_format_file, "evidence-format")
    if evidence_value is not None:
        body["evidence_format_instructions"] = evidence_value
    if profile_id is not None:
        body["model_profile_id"] = profile_id
    _require_fields(body, {"review_statement"})
    response = await client.post(f"{_BASE}/{review_id}/versions", json=body)
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("versions")
@click.argument("review_id")
@click.option("--limit", type=click.IntRange(1, 200), default=50)
@click.option("--offset", type=click.IntRange(0), default=0)
@click.pass_context
@pass_resolver
@run_async
async def versions(ctx: click.Context, review_id: str, limit: int, offset: int, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    response = await client.get(f"{_BASE}/{review_id}/versions", params={"limit": limit, "offset": offset})
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("run")
@click.argument("review_id")
@click.option("--file", "file_path", default=None, help="JSON/YAML request body.")
@click.option("--runs", "runs_value", default=None, help="Comma-separated production AgentRun IDs.")
@click.option("--wait/--no-wait", default=False, help="Poll the shared PlatformJob until terminal.")
@click.option("--timeout", "timeout_s", type=click.IntRange(1), default=1200, help="Client-side wait deadline in seconds.")
@click.pass_context
@pass_resolver
@run_async
async def run_review(ctx: click.Context, review_id: str, file_path: str | None, runs_value: str | None, wait: bool, timeout_s: int, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    body = _load_file_for_command(file_path, allowed=_RUN_FIELDS)
    run_ids = _csv_uuid_values_for_command(runs_value, "--runs")
    if run_ids:
        body["run_ids"] = run_ids
    _require_fields(body, {"run_ids"})
    response = await client.post(f"{_BASE}/{review_id}/runs", json=body)
    _raise_for_review_status(response)
    accepted = response.json()
    review_run_id = accepted.get("review_run_id")
    if not review_run_id:
        raise click.ClickException("Server response did not include review_run_id.")
    click.echo(f"Enqueued agent review (job {accepted.get('job_id')}, review_run {review_run_id}, reused={accepted.get('reused')})", err=True)
    if not wait:
        output_result({**accepted, "review_run_id": review_run_id}, ctx=ctx)
        return
    try:
        job = await poll_platform_job(client, str(accepted.get("job_id")), label="Running agent review", timeout_seconds=float(timeout_s), timeout_operation="agent review", return_terminal_failures=True)
    except click.ClickException as exc:
        output_result({**accepted, "review_run_id": review_run_id, "status": "wait_failed", "message": str(exc)}, ctx=ctx)
        ctx.exit(4)
    if job.get("status") in ("failed", "cancelled"):
        output_result({**job, "review_run_id": review_run_id}, ctx=ctx)
        ctx.exit(3)
    results_response = await client.get(f"{_BASE}/runs/{review_run_id}/results")
    if results_response.status_code == 404:
        output_result({"review_run_id": review_run_id, "status": "unavailable"}, ctx=ctx)
        ctx.exit(4)
    _raise_for_review_status(results_response)
    output_result({"job": job, "results": results_response.json()}, ctx=ctx)


@agent_reviews_group.command("results")
@click.argument("review_run_id")
@click.pass_context
@pass_resolver
@run_async
async def results(ctx: click.Context, review_run_id: str, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    response = await client.get(f"{_BASE}/runs/{review_run_id}/results")
    if response.status_code == 404:
        output_result({"review_run_id": review_run_id, "status": "unavailable"}, ctx=ctx)
        ctx.exit(4)
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("usage")
@click.argument("review_run_id")
@click.option("--limit", type=click.IntRange(1, 200), default=50)
@click.option("--offset", type=click.IntRange(0), default=0)
@click.pass_context
@pass_resolver
@run_async
async def usage(ctx: click.Context, review_run_id: str, limit: int, offset: int, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    response = await client.get(f"{_BASE}/runs/{review_run_id}/usage", params={"limit": limit, "offset": offset})
    _raise_for_review_status(response)
    payload = response.json()
    output_result(payload if ctx.obj and ctx.obj.get("json_output") else _compact_human_report(payload), ctx=ctx)


@agent_reviews_group.command("status")
@click.argument("job_id")
@click.pass_context
@pass_resolver
@run_async
async def status_cmd(ctx: click.Context, job_id: str, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    response = await client.get(f"/api/platform-jobs/{job_id}")
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)


@agent_reviews_group.command("cancel")
@click.argument("job_id")
@click.pass_context
@pass_resolver
@run_async
async def cancel_cmd(ctx: click.Context, job_id: str, *, client: BifrostClient, resolver: RefResolver) -> None:
    del resolver
    response = await client.post(f"/api/platform-jobs/{job_id}/cancel")
    _raise_for_review_status(response)
    output_result(response.json(), ctx=ctx)
