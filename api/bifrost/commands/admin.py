"""CLI commands for platform administration.

* ``bifrost admin authz legacy-status`` → ``GET /api/maintenance/authz/legacy-status``:
  live runs that still hold legacy-format runtime credentials, and connected
  remote launchers that still receive them. Read-only. The release that
  removes legacy runtime tokens refuses to install until both lists are
  empty (see docs/runbooks/authz-epoch.md).
"""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

admin_group = entity_group("admin", "Platform administration.")
authz_group = entity_group("authz", "Inspect runtime credentials and the authority version.")
admin_group.add_command(authz_group)


def _print_legacy_status(status: dict[str, Any]) -> None:
    runs = status["live_runs"]
    launchers = status["launchers"]
    if not runs and not launchers:
        click.echo("No live legacy-format runs and no legacy launchers.")
        return
    click.echo(f"Live legacy-format runs: {len(runs)}")
    for run in runs:
        started = run["started_at"] or "not started"
        click.echo(f"  {run['kind']} {run['id']} {run['name']} ({run['status']}, started {started})")
    click.echo(f"Legacy launchers: {len(launchers)}")
    for launcher in launchers:
        click.echo(
            f"  {launcher['worker_id']} {launcher['hostname'] or '-'} "
            f"({launcher['state']}, credential protocol {launcher['credential_protocol']})"
        )


@authz_group.command("legacy-status")
@click.pass_context
@pass_resolver
@run_async
async def legacy_status(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List live runs and remote launchers still on legacy runtime credentials.

    Restart a listed service attempt through the normal service restart (it
    relaunches with new-format credentials) and upgrade a listed launcher;
    legacy workflow runs end on their own.
    """
    response = await client.get("/api/maintenance/authz/legacy-status")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_legacy_status)
