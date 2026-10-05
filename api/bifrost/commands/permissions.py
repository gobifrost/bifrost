"""CLI commands for the permission catalog.

* ``bifrost permissions list`` → ``GET /api/permissions/catalog``
"""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

permissions_group = entity_group("permissions", "Inspect the permission catalog.")

_SCOPE_LABELS = {
    "per_organization": "per-org",
    "platform_wide": "platform-wide",
    "varies": "varies",
}


def _render_catalog(entries: list[dict[str, Any]]) -> None:
    rows = [
        (
            entry["area"],
            entry["domain"],
            _SCOPE_LABELS[entry["scope"]],
            "yes" if entry["enforced"] else "no",
        )
        for entry in entries
    ]
    table = [("AREA", "PERMISSION", "SCOPE", "ENFORCED"), *rows]
    widths = [max(len(row[col]) for row in table) for col in range(4)]
    for row in table:
        click.echo("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


@permissions_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_permissions(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List permission domains by area, with scope and enforcement."""
    response = await client.get("/api/permissions/catalog")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_render_catalog)
