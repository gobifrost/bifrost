"""CLI commands for asking what a user's access would be.

* ``bifrost users access check <user>`` → ``POST /api/users/{id}/access/check``

A what-if: could this user perform an operation in an organization, directly or
through a workflow? The answer is the access model's trace; nothing is enforced.
"""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

users_group = entity_group("users", "Ask what a user's access would be.")

_MARKS = {"passed": "✓", "stopped": "✗", "not_applicable": "–", "not_reached": "·"}


def _print_trace(trace: dict[str, Any]) -> None:
    for step in trace["steps"]:
        click.echo(f"{_MARKS[step['status']]}  {step['label']}  {step['reason']}".rstrip())
    note = "" if trace["enforced"] else " (not enforced)"
    click.echo(f"Outcome: {trace['outcome']}{note}")


access_group = entity_group("access", "Check a user's access.")
users_group.add_command(access_group)


@access_group.command("check")
@click.argument("user_ref", metavar="USER")
@click.option("--org", required=True, help="Organization UUID or name, or 'global'.")
@click.option("--operation", required=True, help="Catalog operation id or 'METHOD /api/path'.")
@click.option("--workflow", help="Workflow whose powers apply (UUID, name, or path::func). Omit to act directly.")
@click.pass_context
@pass_resolver
@run_async
async def check_access(
    ctx: click.Context,
    user_ref: str,
    org: str,
    operation: str,
    workflow: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Show how the access model decides USER performing --operation in --org.

    USER is a UUID or an email.

    Examples:

      bifrost users access check ada@contoso.test --org global --operation tables.documents.create
      bifrost users access check ada@contoso.test --org Contoso --operation tables.documents.create --workflow "Sync Invoices"
    """
    user_id = await resolver.resolve("user", user_ref)
    body: dict[str, Any] = {
        "organization_id": "global" if org == "global" else await resolver.resolve("org", org),
        "operation": operation,
    }
    if workflow is not None:
        body["workflow_id"] = await resolver.resolve("workflow", workflow)

    response = await client.post(f"/api/users/{user_id}/access/check", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_trace)


__all__ = ["users_group"]
