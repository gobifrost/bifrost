"""CLI commands for searching and managing organization-scoped knowledge."""

from __future__ import annotations

import json
from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.org_target import org_option, resolve_org_target
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async


knowledge_group = entity_group("knowledge", "Search and manage the knowledge store.")


async def _knowledge_boundary(
    org: str | None,
    is_global: bool,
    resolver: RefResolver,
) -> str | None:
    target = await resolve_org_target(org, is_global, resolver)
    if not target.is_set:
        return None
    if target.organization_id is None:
        return "platform"
    return f"organization:{target.organization_id}"


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
            target.organization_id if target.organization_id is not None else "global"
        )

    response = await client.post("/api/knowledge/search", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@knowledge_group.command("list-namespaces")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def list_namespaces(
    ctx: click.Context,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List knowledge namespaces visible in the selected scope."""
    boundary = await _knowledge_boundary(org, is_global, resolver)
    headers = {"X-Bifrost-Boundary": boundary} if boundary else None
    response = await client.get("/api/knowledge-sources", headers=headers)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@knowledge_group.command("list-documents")
@click.option("--namespace", default=None, help="Only list one namespace.")
@click.option("--search", default=None, help="Filter by key/content substring.")
@click.option("--limit", type=click.IntRange(1, 500), default=100, show_default=True)
@click.option("--offset", type=click.IntRange(0), default=0, show_default=True)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def list_documents(
    ctx: click.Context,
    namespace: str | None,
    search: str | None,
    limit: int,
    offset: int,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List knowledge documents visible in the selected scope."""
    boundary = await _knowledge_boundary(org, is_global, resolver)
    headers = {"X-Bifrost-Boundary": boundary} if boundary else None
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if namespace:
        params["namespace"] = namespace
    if search:
        params["search"] = search
    response = await client.get(
        "/api/knowledge-sources/documents",
        params=params,
        headers=headers,
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@knowledge_group.command("get-document")
@click.argument("namespace")
@click.argument("document_id")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def get_document(
    ctx: click.Context,
    namespace: str,
    document_id: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a knowledge document by namespace and UUID."""
    boundary = await _knowledge_boundary(org, is_global, resolver)
    headers = {"X-Bifrost-Boundary": boundary} if boundary else None
    response = await client.get(
        f"/api/knowledge-sources/{namespace}/documents/{document_id}",
        headers=headers,
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


def _metadata_from_json(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise click.UsageError("--metadata must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise click.UsageError("--metadata must be a JSON object")
    return parsed


@knowledge_group.command("create-document")
@click.argument("namespace")
@click.option("--content", required=True, help="Markdown/plain text content.")
@click.option("--key", default=None, help="Optional stable document key.")
@click.option("--metadata", default=None, help="JSON object for document metadata.")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def create_document(
    ctx: click.Context,
    namespace: str,
    content: str,
    key: str | None,
    metadata: str | None,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Create a knowledge document in the selected scope."""
    boundary = await _knowledge_boundary(org, is_global, resolver)
    headers = {"X-Bifrost-Boundary": boundary} if boundary else None
    body: dict[str, Any] = {
        "content": content,
        "metadata": _metadata_from_json(metadata),
    }
    if key is not None:
        body["key"] = key
    response = await client.post(
        f"/api/knowledge-sources/{namespace}/documents",
        json=body,
        headers=headers,
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@knowledge_group.command("update-document")
@click.argument("namespace")
@click.argument("document_id")
@click.option(
    "--content", required=True, help="Replacement Markdown/plain text content."
)
@click.option("--metadata", default=None, help="JSON object for document metadata.")
@click.option(
    "--replace",
    is_flag=True,
    default=False,
    help="Replace conflicting target document on scope move.",
)
@org_option
@click.pass_context
@pass_resolver
@run_async
async def update_document(
    ctx: click.Context,
    namespace: str,
    document_id: str,
    content: str,
    metadata: str | None,
    replace: bool,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Update a knowledge document in the selected exact scope."""
    boundary = await _knowledge_boundary(org, is_global, resolver)
    headers = {"X-Bifrost-Boundary": boundary} if boundary else None
    body: dict[str, Any] = {"content": content}
    if metadata is not None:
        body["metadata"] = _metadata_from_json(metadata)
    response = await client.put(
        f"/api/knowledge-sources/{namespace}/documents/{document_id}",
        json=body,
        params={"replace": replace},
        headers=headers,
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@knowledge_group.command("delete-document")
@click.argument("namespace")
@click.argument("document_id")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def delete_document(
    ctx: click.Context,
    namespace: str,
    document_id: str,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Delete a knowledge document in the selected exact scope."""
    boundary = await _knowledge_boundary(org, is_global, resolver)
    headers = {"X-Bifrost-Boundary": boundary} if boundary else None
    response = await client.delete(
        f"/api/knowledge-sources/{namespace}/documents/{document_id}",
        headers=headers,
    )
    response.raise_for_status()
    output_result({"deleted": document_id, "namespace": namespace}, ctx=ctx)


__all__ = ["knowledge_group"]
