"""CLI commands for agent findings."""

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

agent_findings_group = entity_group("agent-findings", "Manage agent findings.")
_BASE = "/api/agent-findings"
_CREATE_FIELDS = {
    "agent_id",
    "description",
    "expected_behavior",
    "source_kind",
    "source_run_id",
    "source_sequence",
    "external_ref",
    "finding_kind",
    "evidence_markdown",
}
_UPDATE_FIELDS = {
    "description",
    "expected_behavior",
    "status",
    "finding_kind",
    "evidence_markdown",
}


def _exit_usage(message: str) -> NoReturn:
    ctx = click.get_current_context(silent=True)
    obj = ctx.obj if ctx is not None and isinstance(ctx.obj, dict) else {}
    if obj.get("json_output"):
        click.echo(json.dumps({"error": "usage_error", "message": message}, sort_keys=True))
    else:
        click.echo(f"Error: {message}", err=True)
    raise click.exceptions.Exit(2)


def _raise_for_findings_status(response) -> None:
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


def _read_text(value: str | None, file_path: str | None, label: str) -> str | None:
    if value is not None and file_path is not None:
        raise click.UsageError(f"Specify either --{label} or --{label}-file, not both.")
    if file_path is not None:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise click.BadParameter(f"cannot read {label} file: {exc}") from exc
    return value


def _read_text_for_command(
    value: str | None, file_path: str | None, label: str
) -> str | None:
    try:
        return _read_text(value, file_path, label)
    except click.ClickException as exc:
        _exit_usage(exc.format_message())


def _validate_uuid(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        UUID(value)
    except ValueError as exc:
        _exit_usage(f"{label} must be a UUID: {value}")
        raise exc  # unreachable; _exit_usage raises
    return value


@agent_findings_group.command("create")
@click.option("--file", "file_path", default=None, help="JSON/YAML request body.")
@click.option("--agent", "agent_ref", default=None, help="Agent name or UUID.")
@click.option("--description", default=None, help="Observed problem.")
@click.option("--description-file", default=None, help="Read description from PATH.")
@click.option("--expected", default=None, help="Expected behavior.")
@click.option("--expected-file", default=None, help="Read expected behavior from PATH.")
@click.option("--kind", "kind_value", type=click.Choice(["problem", "opportunity"]), default=None)
@click.option("--evidence-markdown", default=None, help="Evidence Markdown.")
@click.option(
    "--evidence-markdown-file", default=None, help="Read evidence Markdown from PATH."
)
@click.option(
    "--source-kind",
    "source_kind",
    type=click.Choice(["run", "manual", "external"]),
    default=None,
)
@click.option("--source-run", default=None, help="Source AgentRun UUID.")
@click.option("--source-sequence", type=int, default=None)
@click.option("--external-ref", default=None)
@click.pass_context
@pass_resolver
@run_async
async def create_finding(
    ctx: click.Context,
    file_path: str | None,
    agent_ref: str | None,
    description: str | None,
    description_file: str | None,
    expected: str | None,
    expected_file: str | None,
    kind_value: str | None,
    evidence_markdown: str | None,
    evidence_markdown_file: str | None,
    source_kind: str | None,
    source_run: str | None,
    source_sequence: int | None,
    external_ref: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    body = _load_file_for_command(file_path, allowed=_CREATE_FIELDS)
    if agent_ref is not None:
        body["agent_id"] = await resolver.resolve("agent", agent_ref)
    description_value = _read_text_for_command(description, description_file, "description")
    if description_value is not None:
        body["description"] = description_value
    expected_value = _read_text_for_command(expected, expected_file, "expected")
    if expected_value is not None:
        body["expected_behavior"] = expected_value
    if kind_value is not None:
        body["finding_kind"] = kind_value
    evidence_value = _read_text_for_command(
        evidence_markdown, evidence_markdown_file, "evidence-markdown"
    )
    if evidence_value is not None:
        body["evidence_markdown"] = evidence_value
    if source_kind is not None:
        body["source_kind"] = source_kind
    if source_run is not None:
        _validate_uuid(source_run, "--source-run")
        body["source_run_id"] = source_run
        if "source_kind" not in body:
            body["source_kind"] = "run"
    if source_sequence is not None:
        body["source_sequence"] = source_sequence
    if external_ref is not None:
        body["external_ref"] = external_ref
        if "source_kind" not in body and "source_run_id" not in body:
            body["source_kind"] = "external"
    _require_fields(body, {"agent_id", "description"})
    response = await client.post(_BASE, json=body)
    _raise_for_findings_status(response)
    output_result(response.json(), ctx=ctx)


@agent_findings_group.command("search")
@click.option("--agent", "agent_ref", default=None, help="Agent name or UUID.")
@click.option(
    "--status", "status_filter", type=click.Choice(["open", "dismissed"]), default=None
)
@click.option("--kind", "kind_value", type=click.Choice(["problem", "opportunity"]), default=None)
@click.option(
    "--source-kind",
    "source_kind",
    type=click.Choice(["run", "manual", "external"]),
    default=None,
)
@click.option("--review", "review_id", default=None, help="Source review UUID.")
@click.option("--review-run", "review_run_id", default=None, help="Source review run UUID.")
@click.option("--q", "query", default=None, help="Literal text search.")
@click.option("--org-id", default=None, help="Organization UUID (admin only).")
@click.option("--limit", type=click.IntRange(1, 200), default=50)
@click.option("--offset", type=click.IntRange(0), default=0)
@click.pass_context
@pass_resolver
@run_async
async def search_findings_cmd(
    ctx: click.Context,
    agent_ref: str | None,
    status_filter: str | None,
    kind_value: str | None,
    source_kind: str | None,
    review_id: str | None,
    review_run_id: str | None,
    query: str | None,
    org_id: str | None,
    limit: int,
    offset: int,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if agent_ref is not None:
        params["agent_id"] = await resolver.resolve("agent", agent_ref)
    if status_filter is not None:
        params["status"] = status_filter
    if kind_value is not None:
        params["finding_kind"] = kind_value
    if source_kind is not None:
        params["source_kind"] = source_kind
    if review_id is not None:
        _validate_uuid(review_id, "--review")
        params["review_id"] = review_id
    if review_run_id is not None:
        _validate_uuid(review_run_id, "--review-run")
        params["review_run_id"] = review_run_id
    if query is not None:
        params["q"] = query
    if org_id is not None:
        _validate_uuid(org_id, "--org-id")
        params["organization_id"] = org_id
    response = await client.get(f"{_BASE}/search", params=params)
    _raise_for_findings_status(response)
    output_result(response.json(), ctx=ctx)


@agent_findings_group.command("list")
@click.option("--agent", "agent_ref", required=True, help="Agent name or UUID.")
@click.option(
    "--status", "status_filter", type=click.Choice(["open", "dismissed"]), default=None
)
@click.pass_context
@pass_resolver
@run_async
async def list_findings_cmd(
    ctx: click.Context,
    agent_ref: str,
    status_filter: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    agent_id = await resolver.resolve("agent", agent_ref)
    params: dict[str, Any] = {"agent_id": agent_id}
    if status_filter is not None:
        params["status"] = status_filter
    response = await client.get(_BASE, params=params)
    _raise_for_findings_status(response)
    output_result(response.json(), ctx=ctx)


@agent_findings_group.command("get")
@click.argument("finding_id")
@click.pass_context
@pass_resolver
@run_async
async def get_finding_cmd(
    ctx: click.Context,
    finding_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    _validate_uuid(finding_id, "finding_id")
    response = await client.get(f"{_BASE}/{finding_id}")
    _raise_for_findings_status(response)
    output_result(response.json(), ctx=ctx)


@agent_findings_group.command("update")
@click.argument("finding_id")
@click.option("--file", "file_path", default=None, help="JSON/YAML request body.")
@click.option("--description", default=None)
@click.option("--description-file", default=None)
@click.option("--expected", default=None)
@click.option("--expected-file", default=None)
@click.option("--status", "status_value", type=click.Choice(["open", "dismissed"]), default=None)
@click.option("--kind", "kind_value", type=click.Choice(["problem", "opportunity"]), default=None)
@click.option("--evidence-markdown", default=None)
@click.option("--evidence-markdown-file", default=None)
@click.option(
    "--clear-evidence-markdown",
    is_flag=True,
    default=False,
    help="Clear evidence Markdown (sends explicit null).",
)
@click.pass_context
@pass_resolver
@run_async
async def update_finding_cmd(
    ctx: click.Context,
    finding_id: str,
    file_path: str | None,
    description: str | None,
    description_file: str | None,
    expected: str | None,
    expected_file: str | None,
    status_value: str | None,
    kind_value: str | None,
    evidence_markdown: str | None,
    evidence_markdown_file: str | None,
    clear_evidence_markdown: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    del resolver
    _validate_uuid(finding_id, "finding_id")
    body = _load_file_for_command(file_path, allowed=_UPDATE_FIELDS)
    description_value = _read_text_for_command(description, description_file, "description")
    if description_value is not None:
        body["description"] = description_value
    expected_value = _read_text_for_command(expected, expected_file, "expected")
    if expected_value is not None:
        body["expected_behavior"] = expected_value
    if status_value is not None:
        body["status"] = status_value
    if kind_value is not None:
        body["finding_kind"] = kind_value
    if clear_evidence_markdown and (
        evidence_markdown is not None or evidence_markdown_file is not None
    ):
        _exit_usage("Specify either --clear-evidence-markdown or --evidence-markdown(-file), not both.")
    if clear_evidence_markdown:
        body["evidence_markdown"] = None
    else:
        evidence_value = _read_text_for_command(
            evidence_markdown, evidence_markdown_file, "evidence-markdown"
        )
        if evidence_value is not None:
            body["evidence_markdown"] = evidence_value
    if not body:
        _exit_usage("Specify at least one update field.")
    response = await client.patch(f"{_BASE}/{finding_id}", json=body)
    _raise_for_findings_status(response)
    output_result(response.json(), ctx=ctx)
