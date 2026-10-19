"""Reviewable workflow conversion through the same REST operations as the UI."""

from __future__ import annotations

import json
from typing import Any, TextIO

import click

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async


conversion_group = entity_group("convert", "Analyze, review and convert workflow permissions.")


@conversion_group.command("analyze")
@click.option("--workflow", "workflow_refs", multiple=True, help="Workflow UUID, name or path::function. Repeat for a selection.")
@click.option("--scope", default="all", help="all, global, or an organization UUID when no workflows are selected.")
@click.option("--platform-cutover", is_flag=True, help="Prepare a platform-wide readiness review; does not change the default.")
@click.pass_context
@pass_resolver
@run_async
async def analyze(
    ctx: click.Context,
    workflow_refs: tuple[str, ...],
    scope: str,
    platform_cutover: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Start durable read-only analysis. Observe it with platform-jobs get."""
    if platform_cutover and (workflow_refs or scope != "all"):
        raise click.UsageError("--platform-cutover requires the whole platform (no --workflow and --scope all).")
    body: dict[str, Any] = {"scope": scope, "platform_cutover": platform_cutover}
    if workflow_refs:
        body["workflow_ids"] = list(dict.fromkeys([
            await resolver.resolve("workflow", ref) for ref in workflow_refs
        ]))
    response = await client.post("/api/workflow-conversions", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@conversion_group.command("report")
@click.argument("job_id", type=click.UUID)
@click.option("--offset", type=click.IntRange(min=0), default=0)
@click.option("--limit", type=click.IntRange(min=1, max=200), default=50)
@click.pass_context
@pass_resolver
@run_async
async def report(
    ctx: click.Context, job_id: Any, offset: int, limit: int,
    *, client: BifrostClient, resolver: RefResolver,
) -> None:
    """Read a page of a saved analysis without restarting it."""
    response = await client.get(f"/api/workflow-conversions/{job_id}/report", params={"offset": offset, "limit": limit})
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@conversion_group.command("apply")
@click.argument("job_id", type=click.UUID)
@click.option("--review-file", type=click.File("r"), required=True, help="JSON reviewed selections, fingerprints and explicit acknowledgements from the saved report.")
@click.pass_context
@pass_resolver
@run_async
async def apply(
    ctx: click.Context, job_id: Any, review_file: TextIO,
    *, client: BifrostClient, resolver: RefResolver,
) -> None:
    """Apply explicitly reviewed proposals; stale proposals are refused."""
    try:
        body = json.load(review_file)
    except (ValueError, OSError) as exc:
        raise click.BadParameter("Review file must contain a JSON object.", param_hint="--review-file") from exc
    if not isinstance(body, dict) or not body.get("selections"):
        raise click.BadParameter("Review file must contain a nonempty selections array.", param_hint="--review-file")
    response = await client.post(f"/api/workflow-conversions/{job_id}/apply", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@conversion_group.command("cutover")
@click.argument("job_id", type=click.UUID)
@click.option("--acknowledge-platform-cutover", is_flag=True, required=True, help="Explicitly request Declared Permissions as the platform default after readiness is revalidated.")
@click.pass_context
@pass_resolver
@run_async
async def cutover(
    ctx: click.Context, job_id: Any, acknowledge_platform_cutover: bool,
    *, client: BifrostClient, resolver: RefResolver,
) -> None:
    """Request the final reviewed platform switch. Analysis alone never switches it."""
    if not acknowledge_platform_cutover:
        raise click.UsageError("--acknowledge-platform-cutover is required.")
    response = await client.post(
        f"/api/workflow-conversions/{job_id}/cutover",
        json={"acknowledged_remaining_exceptions": True},
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)
