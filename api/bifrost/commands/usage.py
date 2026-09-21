"""Usage reporting CLI commands."""

from __future__ import annotations

from datetime import date
from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.commands.base import entity_group, output_result, pass_resolver, run_async
from bifrost.refs import RefResolver


usage_group = entity_group("usage", "Usage reporting commands.")


def _parse_date(value: str, option_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise click.UsageError(f"{option_name} must be YYYY-MM-DD") from exc


def _page_note(page: dict[str, Any]) -> str:
    total = page.get("total_groups", 0) or 0
    offset = page.get("offset", 0) or 0
    limit = page.get("limit", 0) or 0
    omitted = page.get("omitted_group_count", 0) or 0
    shown = len(page.get("items") or [])
    end = offset + shown
    note = f"groups {offset + 1}-{end} of {total}" if shown else f"groups 0 of {total}"
    if omitted:
        note = f"{note}; {omitted} omitted by pagination"
    elif limit and total > end:
        note = f"{note}; more available"
    return note


def _row_totals(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("totals") or {}


def _purpose_line(item: dict[str, Any]) -> str:
    totals = _row_totals(item)
    return (
        f"{item.get('purpose') or 'unknown'}: "
        f"calls={totals.get('call_count')} "
        f"known_cost={totals.get('known_cost')} "
        f"tokens={totals.get('input_tokens')}/{totals.get('output_tokens')}"
    )


def _provider_model_line(item: dict[str, Any]) -> str:
    totals = _row_totals(item)
    provider = item.get("provider") or "unknown"
    model = item.get("model") or "unknown"
    return (
        f"{provider}/{model}: "
        f"purpose={item.get('purpose') or 'unknown'} "
        f"calls={totals.get('call_count')} "
        f"known_cost={totals.get('known_cost')}"
    )


def _compact_human_report(payload: dict[str, Any]) -> dict[str, Any]:
    overall = payload.get("overall") or {}
    coverage = payload.get("coverage") or {}
    by_purpose = payload.get("by_purpose") or {}
    by_provider_model = payload.get("by_provider_model") or {}
    return {
        "known_cost": overall.get("known_cost"),
        "provider_reported_cost": overall.get("observed_provider_cost"),
        "estimated_cost": overall.get("estimated_cost"),
        "calls": overall.get("call_count"),
        "input_tokens": overall.get("input_tokens"),
        "output_tokens": overall.get("output_tokens"),
        "cache_read_tokens": overall.get("cache_read_tokens"),
        "cache_write_tokens": overall.get("cache_write_tokens"),
        "missing_cost_calls": overall.get("missing_cost_call_count"),
        "started_unresolved_attempts": coverage.get("started_attempt_count"),
        "unobserved_attempts": coverage.get("unobserved_attempt_count"),
        "legacy_coverage_unknown": coverage.get("legacy_coverage_unknown"),
        "by_purpose": [_purpose_line(item) for item in by_purpose.get("items") or []],
        "by_purpose_page": _page_note(by_purpose),
        "by_provider_model": [_provider_model_line(item) for item in by_provider_model.get("items") or []],
        "by_provider_model_page": _page_note(by_provider_model),
    }


@usage_group.command("report")
@click.option("--start-date", required=True, help="Start date in UTC, YYYY-MM-DD.")
@click.option("--end-date", required=True, help="End date in UTC, YYYY-MM-DD.")
@click.option("--org-id", default=None, help="Organization UUID filter.")
@click.option("--source", type=click.Choice(["all", "executions", "chat", "agents"]), default="all", show_default=True)
@click.option("--purpose", default=None, help="Usage purpose filter.")
@click.option("--provider", default=None, help="Provider filter.")
@click.option("--model", default=None, help="Model filter.")
@click.option("--profile-id", default=None, help="Profile UUID filter.")
@click.option("--profile-fingerprint", default=None, help="Profile fingerprint filter.")
@click.option("--limit", type=int, default=50, show_default=True, help="Dimension page size.")
@click.option("--offset", type=int, default=0, show_default=True, help="Dimension page offset.")
@click.pass_context
@pass_resolver
@run_async
async def usage_report(
    ctx: click.Context,
    start_date: str,
    end_date: str,
    org_id: str | None,
    source: str,
    purpose: str | None,
    provider: str | None,
    model: str | None,
    profile_id: str | None,
    profile_fingerprint: str | None,
    limit: int,
    offset: int,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Show testing/review-aware usage breakdown."""
    del resolver
    start = _parse_date(start_date, "--start-date")
    end = _parse_date(end_date, "--end-date")
    if end < start:
        raise click.UsageError("--end-date must be on or after --start-date")
    params = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "org_id": org_id,
        "source": source,
        "purpose": purpose,
        "provider": provider,
        "model": model,
        "profile_id": profile_id,
        "profile_fingerprint": profile_fingerprint,
        "limit": limit,
        "offset": offset,
    }
    response = await client.get(
        "/api/reports/usage/breakdown",
        params={key: value for key, value in params.items() if value is not None},
    )
    response.raise_for_status()
    payload = response.json()
    output_result(payload if ctx.obj and ctx.obj.get("json_output") else _compact_human_report(payload), ctx=ctx)
