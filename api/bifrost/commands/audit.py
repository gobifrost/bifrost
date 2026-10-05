"""CLI commands for reading the audit log.

* ``bifrost audit list`` → ``GET /api/audit``
* ``bifrost audit explain <event_id>`` → ``GET /api/audit/{event_id}/explain``

``explain`` shows a stored ``access.check`` event as it was decided then and as
the access model judges it now, step by step.
"""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

audit_group = entity_group("audit", "Read the audit log and explain access decisions.")

_GROUP_BY = ["workflow", "action", "resource_type", "outcome", "user", "organization"]

_NOW_UNAVAILABLE = {
    "rows_not_stored": "table row decisions can't be re-run: the rows aren't stored",
    "run_user_missing": "the run's user no longer exists",
    "workflow_missing": "the workflow no longer exists",
    "solution_not_recorded": "this file check was recorded before Solutions were stored with it",
}


def _columns(rows: list[list[str]], separator: str) -> list[str]:
    """Pad every column but the last to its widest cell and join each row."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return [
        separator.join([cell.ljust(width) for cell, width in zip(row, widths)] + [row[-1]]).rstrip()
        for row in rows
    ]


def _print_list(response: dict[str, Any]) -> None:
    if response["groups"] is not None:
        rows = [[str(g["count"]), g["key"] or "-", g["last_seen"]] for g in response["groups"]]
    else:
        rows = [
            [
                e["timestamp"],
                e["action"],
                e["outcome"],
                e["actor"]["user_email"] or "-",
                e["actor"]["organization_name"] or "-",
                e["resource_type"] or "-",
            ]
            for e in response["entries"]
        ]
    if rows:
        click.echo("\n".join(_columns(rows, "  ")))


@audit_group.command("list")
@click.option("--action", help="Action prefix, e.g. 'user.' or 'access.check'.")
@click.option("--outcome", type=click.Choice(["success", "failure"]), help="Only successes or failures.")
@click.option("--resource-type", help="Only events on this resource type.")
@click.option("--user", "user_ref", help="Acting user: UUID or email.")
@click.option("--execution", help="Workflow execution ID.")
@click.option("--since", help="Start of the time range (ISO 8601, inclusive).")
@click.option("--until", help="End of the time range (ISO 8601, inclusive).")
@click.option("--search", help="Free-text search on actor, organization, action, resource type, IP, and details.")
@click.option(
    "--group-by",
    type=click.Choice(_GROUP_BY),
    help="Count matching events per value instead of listing them.",
)
@click.option("--limit", type=click.IntRange(1, 500), help="Maximum entries to return (server default 50).")
@click.pass_context
@pass_resolver
@run_async
async def list_audit(
    ctx: click.Context,
    action: str | None,
    outcome: str | None,
    resource_type: str | None,
    user_ref: str | None,
    execution: str | None,
    since: str | None,
    until: str | None,
    search: str | None,
    group_by: str | None,
    limit: int | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List audit log entries, or count them with --group-by."""
    params: dict[str, Any] = {}
    if action is not None:
        params["action"] = action
    if outcome is not None:
        params["outcome"] = outcome
    if resource_type is not None:
        params["resource_type"] = resource_type
    if user_ref is not None:
        params["user_id"] = await resolver.resolve("user", user_ref)
    if execution is not None:
        params["execution_id"] = execution
    if since is not None:
        params["start_date"] = since
    if until is not None:
        params["end_date"] = until
    if search is not None:
        params["search"] = search
    if group_by is not None:
        params["group_by"] = group_by
    if limit is not None:
        params["limit"] = limit

    response = await client.get("/api/audit", params=params)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_list)


def _cell(step: dict[str, Any]) -> str:
    return f"{step['status']} ({step['reason']})" if step["reason"] else step["status"]


def _print_explanation(explanation: dict[str, Any]) -> None:
    event = explanation["event"]
    header = [
        ("Action", event["action"]),
        ("Outcome", event["outcome"]),
        ("When", event["timestamp"]),
        ("Run user", event["actor"]["user_email"] or "-"),
        ("Organization", event["actor"]["organization_name"] or "-"),
    ]
    for label, value in header:
        click.echo(f"{label + ':':<14}{value}")
    click.echo()

    then_steps = explanation["then"]["steps"]
    rows = [["STEP", "THEN"]] + [[step["label"], _cell(step)] for step in then_steps]
    now = explanation["now"]
    if now is not None:
        now_by_key = {step["key"]: step for step in now["steps"]}
        rows[0].append("NOW")
        for row, step in zip(rows[1:], then_steps):
            row.append(_cell(now_by_key[step["key"]]))
    click.echo("\n".join(_columns(rows, " | ")))

    if now is None:
        click.echo(f"NOW: not available — {_NOW_UNAVAILABLE[explanation['now_unavailable']]}")
    else:
        click.echo(f"Changed: {'yes' if explanation['changed'] else 'no'}")


@audit_group.command("explain")
@click.argument("event_id")
@click.pass_context
@pass_resolver
@run_async
async def explain_audit(
    ctx: click.Context,
    event_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Explain a stored access check: how it was decided then, and how it would be now."""
    response = await client.get(f"/api/audit/{event_id}/explain")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_explanation)


__all__ = ["audit_group"]
