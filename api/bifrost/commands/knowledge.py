"""CLI commands for searching organization-scoped knowledge."""

from __future__ import annotations

import json
from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.org_target import org_option, resolve_org_target
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async


knowledge_group = entity_group("knowledge", "Search the knowledge store.")


@knowledge_group.command("search")
@click.argument("query")
@click.option(
    "--namespace",
    "namespaces",
    multiple=True,
    help="Namespace to search. Repeat to search more than one.",
)
@click.option("--limit", type=click.IntRange(1), default=5, show_default=True)
@click.option("--min-score", type=click.FloatRange(0, 1), default=None)
@click.option(
    "--metadata-filter",
    default=None,
    help="JSON object matched against document metadata.",
)
@click.option(
    "--fallback/--no-fallback",
    default=True,
    help="Include global knowledge when searching an organization.",
)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def search_knowledge(
    ctx: click.Context,
    query: str,
    namespaces: tuple[str, ...],
    limit: int,
    min_score: float | None,
    metadata_filter: str | None,
    fallback: bool,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Hybrid-search knowledge documents visible in an organization scope."""
    parsed_filter: dict[str, Any] | None = None
    if metadata_filter is not None:
        try:
            candidate = json.loads(metadata_filter)
        except json.JSONDecodeError as exc:
            raise click.UsageError("--metadata-filter must be valid JSON") from exc
        if not isinstance(candidate, dict):
            raise click.UsageError("--metadata-filter must be a JSON object")
        parsed_filter = candidate

    target = await resolve_org_target(org, is_global, resolver)
    body: dict[str, Any] = {
        "query": query,
        "namespace": list(namespaces) if namespaces else ["default"],
        "limit": limit,
        "fallback": fallback,
    }
    if min_score is not None:
        body["min_score"] = min_score
    if parsed_filter is not None:
        body["metadata_filter"] = parsed_filter
    if target.is_set:
        body["scope"] = (
            target.organization_id
            if target.organization_id is not None
            else "global"
        )

    response = await client.post("/api/knowledge/search", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["knowledge_group"]
