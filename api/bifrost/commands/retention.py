"""CLI commands for run and audit retention.

* ``bifrost retention show`` → ``GET /api/maintenance/{run,audit}-retention/settings``
* ``bifrost retention set runs|audit`` → ``PUT`` the same endpoints
* ``bifrost retention preview runs|audit`` → ``POST /api/maintenance/{run,audit}-retention/run``
  as a dry run, then follow the platform job
* ``bifrost retention run runs|audit`` → the same, for real

Runs and events are deleted after their window; audit events are archived to object
storage after the database window and deleted after the archive window.
"""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.platform_jobs import poll_platform_job
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

retention_group = entity_group("retention", "Show and change how long runs, events, and audit events are kept.")

_FOREVER = "forever"

_RUN_PATHS = {
    "runs": "/api/maintenance/run-retention/run",
    "audit": "/api/maintenance/audit-retention/run",
}
_PREVIEW_LABELS = {"runs": "Run retention preview", "audit": "Audit archive preview"}
_RUN_LABELS = {"runs": "Run retention", "audit": "Audit archive"}


class _DaysOrForever(click.ParamType):
    name = "days|forever"

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> int | None:
        if value == _FOREVER:
            return None
        try:
            return int(value)
        except ValueError:
            self.fail(f"{value!r} is not an integer or 'forever'.", param, ctx)


def _day(timestamp: str) -> str:
    return timestamp[:10]


def _window(days: int | None) -> str:
    return "forever" if days is None else f"{days} days"


def _detail(parts: list[tuple[str, str | None]]) -> str:
    shown = [f"{label} {_day(value)}" for label, value in parts if value is not None]
    return f" ({', '.join(shown)})" if shown else ""


def _print_last_run(last_run: dict[str, Any] | None) -> None:
    if last_run is None:
        return
    line = f"  Last run: {last_run['status']} {last_run['completed_at'] or last_run['created_at']}"
    if last_run["error"] is not None:
        line += f" — {last_run['error']['message']}"
    click.echo(line)


def _print_runs(status: dict[str, Any]) -> None:
    info = status["info"]
    click.echo(
        f"Runs and events: kept {_window(info['days'])}"
        + _detail([("oldest finished run", info["oldest_finished_run"])])
    )
    _print_last_run(status["last_run"])


def _print_audit(status: dict[str, Any]) -> None:
    info = status["info"]
    click.echo(
        f"Audit: {info['hot_days']} days in the database, archive kept {_window(info['archive_days'])}"
        + _detail(
            [
                ("oldest in database", info["oldest_in_database"]),
                ("archived through", info["archived_through"]),
            ]
        )
    )
    _print_last_run(status["last_run"])


def _print_show(statuses: dict[str, Any]) -> None:
    _print_runs(statuses["runs"])
    _print_audit(statuses["audit"])


@retention_group.command("show")
@click.pass_context
@pass_resolver
@run_async
async def show_retention(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Show the retention windows, what the oldest data is, and the last run of each."""
    runs = await client.get("/api/maintenance/run-retention/settings")
    runs.raise_for_status()
    audit = await client.get("/api/maintenance/audit-retention/settings")
    audit.raise_for_status()
    output_result({"runs": runs.json(), "audit": audit.json()}, ctx=ctx, human=_print_show)


set_group = entity_group("set", "Change a retention window.")
retention_group.add_command(set_group)


@set_group.command("runs")
@click.option("--days", type=int, help="Keep finished runs and events this many days (30 to 3650).")
@click.option("--forever", is_flag=True, help="Keep finished runs and events forever.")
@click.pass_context
@pass_resolver
@run_async
async def set_runs(
    ctx: click.Context,
    days: int | None,
    forever: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Set how long finished runs and their events are kept.

    \b
    Examples:
      bifrost retention set runs --days 60
      bifrost retention set runs --forever
    """
    if (days is None) == (not forever):
        raise click.UsageError("Give exactly one of --days and --forever.")
    response = await client.put("/api/maintenance/run-retention/settings", json={"days": days})
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_runs)


@set_group.command("audit")
@click.option("--database", type=int, help="Days audit events stay in the database before they are archived.")
@click.option(
    "--archive",
    type=_DaysOrForever(),
    default=None,
    show_default=False,
    help="Days archived audit events are kept before deletion, or 'forever'.",
)
@click.pass_context
@pass_resolver
@run_async
async def set_audit(
    ctx: click.Context,
    database: int | None,
    archive: int | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Set the audit database window, the archive window, or both.

    A window you leave out keeps its current value.

    \b
    Examples:
      bifrost retention set audit --database 60
      bifrost retention set audit --archive forever
    """
    archive_given = ctx.get_parameter_source("archive") is not click.core.ParameterSource.DEFAULT
    if database is None and not archive_given:
        raise click.UsageError("Give --database, --archive, or both.")
    current = await client.get("/api/maintenance/audit-retention/settings")
    current.raise_for_status()
    settings = current.json()["settings"]
    body = {
        "hot_days": settings["hot_days"] if database is None else database,
        "archive_days": archive if archive_given else settings["archive_days"],
    }
    response = await client.put("/api/maintenance/audit-retention/settings", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_audit)


async def _run_job(
    client: BifrostClient, area: str, *, dry_run: bool, label: str, ctx: click.Context
) -> None:
    response = await client.post(_RUN_PATHS[area], json={"dry_run": dry_run})
    response.raise_for_status()
    job = await poll_platform_job(client, response.json()["job_id"], label=label)
    output_result(job["result"], ctx=ctx)


@retention_group.command("preview")
@click.argument("area", type=click.Choice(["runs", "audit"]))
@click.pass_context
@pass_resolver
@run_async
async def preview_retention(
    ctx: click.Context,
    area: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Show what a retention run would do now, without changing anything."""
    await _run_job(client, area, dry_run=True, label=_PREVIEW_LABELS[area], ctx=ctx)


@retention_group.command("run")
@click.argument("area", type=click.Choice(["runs", "audit"]))
@click.pass_context
@pass_resolver
@run_async
async def run_retention(
    ctx: click.Context,
    area: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Run retention now: delete expired runs and events, or archive aged audit events."""
    await _run_job(client, area, dry_run=False, label=_RUN_LABELS[area], ctx=ctx)


__all__ = ["retention_group"]
