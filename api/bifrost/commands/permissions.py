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

SCOPE_LABELS = {
    "per_organization": "per-org",
    "platform_wide": "platform-wide",
    "varies": "varies",
}

WILDCARD = "*"


def permission_names(catalog: list[dict[str, Any]]) -> dict[str, str]:
    """Display names by permission, as the catalog words them ("Read and Write Users").

    The Platform Admin wildcard is not a catalog permission; it reads "All Permissions".
    """
    names = {WILDCARD: "All Permissions"}
    for entry in catalog:
        names.update(entry["names"])
    return names


def _render_catalog(entries: list[dict[str, Any]]) -> None:
    rows = [
        (
            entry["area"],
            name,
            permission,
            SCOPE_LABELS[entry["scope"]],
            "yes" if entry["enforced"] else "no",
        )
        for entry in entries
        for permission, name in entry["names"].items()
    ]
    table = [("AREA", "NAME", "PERMISSION", "SCOPE", "ENFORCED"), *rows]
    widths = [max(len(row[col]) for row in table) for col in range(5)]
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
    """List every permission by area, with its name, scope and enforcement."""
    response = await client.get("/api/permissions/catalog")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_render_catalog)
