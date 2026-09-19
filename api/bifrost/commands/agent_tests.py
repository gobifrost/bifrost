"""CLI workflows for the Agent Evaluation Studio.

``bifrost agent-tests`` covers suite/case/candidate management, Test
Designer drafts, suite runs backed by the shared PlatformJob contract,
status/result inspection, and baseline-versus-candidate comparison.

Conventions follow the other entity groups: ``RefResolver`` resolves
Agent/tool/delegate refs, ``@file.json``/``@file.yaml`` load
fixtures/assertions/candidate overlays through the shared safe loaders,
``run --wait`` polls the shared PlatformJob endpoint with a client-side
deadline (server work continues past the deadline), and ``--json`` output
is automation-safe.
"""

from __future__ import annotations

import json
from typing import Any

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.dto_flags import load_dict_value
from bifrost.org_target import org_option
from bifrost.platform_jobs import poll_platform_job
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async

agent_tests_group = entity_group("agent-tests", "Manage agent evaluation suites.")

_BASE = "/api/agent-evaluations"


def _load_any_value(raw: str | None) -> Any | None:
    """Resolve ``@path`` (YAML/JSON object or array) or an inline JSON literal."""
    if raw is None:
        return None
    if raw.startswith("@"):
        from pathlib import Path

        loaded = yaml.safe_load(Path(raw[1:]).read_text(encoding="utf-8"))
        if not isinstance(loaded, (dict, list)):
            raise click.BadParameter("file must contain a JSON/YAML object or array")
        return loaded
    return json.loads(raw)


def _execution_id_from(response: Any) -> str | None:
    headers = getattr(response, "headers", {}) or {}
    return headers.get("X-Evaluation-Execution-Id")


@agent_tests_group.command("suites-list")
@click.pass_context
@pass_resolver
@run_async
async def suites_list(
    ctx: click.Context, *, client: BifrostClient, resolver: RefResolver
) -> None:
    """List evaluation suites."""
    del resolver
    response = await client.get(f"{_BASE}/suites")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("suites-get")
@click.argument("suite_id")
@click.pass_context
@pass_resolver
@run_async
async def suites_get(
    ctx: click.Context,
    suite_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get one suite by UUID."""
    del resolver
    response = await client.get(f"{_BASE}/suites/{suite_id}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("suites-create")
@click.option("--name", required=True, help="Suite name.")
@click.option("--description", default=None, help="Suite description.")
@click.option("--agent", "agent_ref", default=None, help="Baseline agent name or UUID.")
@org_option
@click.pass_context
@pass_resolver
@run_async
async def suites_create(
    ctx: click.Context,
    name: str,
    description: str | None,
    agent_ref: str | None,
    org: str | None,
    is_global: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Create a draft evaluation suite."""
    body: dict[str, Any] = {"name": name}
    if description is not None:
        body["description"] = description
    if agent_ref is not None:
        body["agent_id"] = await resolver.resolve("agent", agent_ref)
    from bifrost.org_target import resolve_org_target

    target = await resolve_org_target(org, is_global, resolver)
    if target.is_set:
        body["organization_id"] = target.organization_id
    response = await client.post(f"{_BASE}/suites", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("suites-publish")
@click.argument("suite_id")
@click.pass_context
@pass_resolver
@run_async
async def suites_publish(
    ctx: click.Context,
    suite_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Publish a suite (published suites are immutable)."""
    del resolver
    response = await client.post(f"{_BASE}/suites/{suite_id}/publish")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("cases-list")
@click.argument("suite_id")
@click.pass_context
@pass_resolver
@run_async
async def cases_list(
    ctx: click.Context,
    suite_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List cases in a suite."""
    del resolver
    response = await client.get(f"{_BASE}/suites/{suite_id}/cases")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("cases-create")
@click.argument("suite_id")
@click.option("--name", required=True, help="Case name.")
@click.option("--input", "input_raw", default=None, help="Invocation input JSON or @file.")
@click.option("--fixture", "fixture_raw", required=True, help="Fixture object JSON or @file.")
@click.option("--assertions", "assertions_raw", default=None, help="Assertions array JSON or @file.")
@click.option("--expected-tools", default=None, help="Comma-separated expected tool names.")
@click.option("--forbidden-tools", default=None, help="Comma-separated forbidden tool names.")
@click.option("--output-schema", "schema_raw", default=None, help="Output schema JSON or @file.")
@click.option("--repetitions", type=int, default=1, help="Repetitions per side (1-10).")
@click.pass_context
@pass_resolver
@run_async
async def cases_create(
    ctx: click.Context,
    suite_id: str,
    name: str,
    input_raw: str | None,
    fixture_raw: str,
    assertions_raw: str | None,
    expected_tools: str | None,
    forbidden_tools: str | None,
    schema_raw: str | None,
    repetitions: int,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Create a case from inline JSON or @file payloads."""
    del resolver
    body: dict[str, Any] = {
        "name": name,
        "fixture": load_dict_value(fixture_raw) or {},
        "repetitions": repetitions,
    }
    if input_raw is not None:
        body["input"] = load_dict_value(input_raw)
    assertions = _load_any_value(assertions_raw)
    if assertions is not None:
        if not isinstance(assertions, list):
            raise click.BadParameter("--assertions must be a JSON/YAML array")
        body["assertions"] = assertions
    if expected_tools:
        body["expected_tools"] = [t.strip() for t in expected_tools.split(",") if t.strip()]
    if forbidden_tools:
        body["forbidden_tools"] = [t.strip() for t in forbidden_tools.split(",") if t.strip()]
    if schema_raw is not None:
        body["output_schema"] = load_dict_value(schema_raw)
    response = await client.post(f"{_BASE}/suites/{suite_id}/cases", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("cases-export")
@click.argument("suite_id")
@click.option("--output", "output_path", default=None, help="Write JSON to PATH instead of stdout.")
@click.pass_context
@pass_resolver
@run_async
async def cases_export(
    ctx: click.Context,
    suite_id: str,
    output_path: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Export all cases of a suite as machine-readable JSON."""
    del resolver
    response = await client.get(f"{_BASE}/suites/{suite_id}/cases")
    response.raise_for_status()
    payload = {"suite_id": suite_id, "cases": response.json()}
    if output_path is not None:
        from pathlib import Path

        Path(output_path).write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        click.echo(f"Wrote {len(payload['cases'])} cases to {output_path}")
        return
    output_result(payload, ctx=ctx)


@agent_tests_group.command("candidates-create")
@click.option("--agent", "agent_ref", required=True, help="Base agent name or UUID.")
@click.option("--name", default=None, help="Candidate label.")
@click.option("--overlays", "overlays_raw", default=None, help="Overlay object JSON or @file.")
@click.option("--system-prompt", default=None, help="Prompt override (or @file).")
@click.pass_context
@pass_resolver
@run_async
async def candidates_create(
    ctx: click.Context,
    agent_ref: str,
    name: str | None,
    overlays_raw: str | None,
    system_prompt: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Create an immutable evaluation-only candidate snapshot."""
    from pathlib import Path

    agent_uuid = await resolver.resolve("agent", agent_ref)
    overlays = load_dict_value(overlays_raw) or {}
    if system_prompt is not None:
        if system_prompt.startswith("@"):
            system_prompt = Path(system_prompt[1:]).read_text(encoding="utf-8")
        overlays["system_prompt"] = system_prompt
    for list_key, kind in (("tool_ids", "workflow"), ("delegated_agent_ids", "agent")):
        values = overlays.get(list_key)
        if values:
            resolved = []
            for value in values:
                resolved.append(await resolver.resolve(kind, str(value)))  # type: ignore[arg-type]
            overlays[list_key] = resolved
    body: dict[str, Any] = {"base_agent_id": agent_uuid, "overlays": overlays}
    if name is not None:
        body["name"] = name
    response = await client.post(f"{_BASE}/candidates", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("candidates-get")
@click.argument("candidate_id")
@click.pass_context
@pass_resolver
@run_async
async def candidates_get(
    ctx: click.Context,
    candidate_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Get a candidate snapshot by UUID."""
    del resolver
    response = await client.get(f"{_BASE}/candidates/{candidate_id}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("designer-drafts")
@click.argument("suite_id")
@click.option("--goal", required=True, help="What the generated cases should cover.")
@click.option("--count", "requested_count", type=click.IntRange(1, 10), default=4)
@click.option("--historical-run", "historical_run_ids", multiple=True, help="Authorized historical AgentRun UUID.")
@click.pass_context
@pass_resolver
@run_async
async def designer_drafts(
    ctx: click.Context,
    suite_id: str,
    goal: str,
    requested_count: int,
    historical_run_ids: tuple[str, ...],
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Start the server-authorized Test Designer AgentRun."""
    del resolver
    body = {"suite_goal": goal, "requested_count": requested_count,
            "historical_run_ids": list(historical_run_ids)}
    response = await client.post(
        f"{_BASE}/suites/{suite_id}/designer/drafts", json=body
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("designer-accept")
@click.argument("suite_id")
@click.argument("draft_id")
@click.pass_context
@pass_resolver
@run_async
async def designer_accept(
    ctx: click.Context,
    suite_id: str,
    draft_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Explicitly accept a draft, freezing a new case version."""
    del resolver
    response = await client.post(
        f"{_BASE}/suites/{suite_id}/cases/accept", json={"draft_id": draft_id}
    )
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("run")
@click.option("--suite", "suite_id", required=True, help="Suite UUID.")
@click.option("--candidate", "candidate_id", default=None, help="Candidate UUID (omit for baseline-only).")
@click.option("--repetitions", type=int, default=None, help="Override repetitions (1-10).")
@click.option("--wait/--no-wait", default=False, help="Poll the shared PlatformJob until terminal.")
@click.option("--timeout", "timeout_s", type=int, default=1200, help="Client-side wait deadline in seconds.")
@click.pass_context
@pass_resolver
@run_async
async def run_suite(
    ctx: click.Context,
    suite_id: str,
    candidate_id: str | None,
    repetitions: int | None,
    wait: bool,
    timeout_s: int,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Enqueue a suite execution (202 + shared PlatformJob observation)."""
    del resolver
    body: dict[str, Any] = {"suite_id": suite_id}
    if candidate_id is not None:
        body["candidate_id"] = candidate_id
    if repetitions is not None:
        body["repetitions_override"] = repetitions
    response = await client.post(f"{_BASE}/executions", json=body)
    response.raise_for_status()
    accepted = response.json()
    execution_id = _execution_id_from(response)
    click.echo(
        f"Enqueued evaluation (job {accepted.get('job_id')}, "
        f"execution {execution_id}, reused={accepted.get('reused')})",
        err=True,
    )
    if not wait:
        output_result({**accepted, "execution_id": execution_id}, ctx=ctx)
        return
    job = await poll_platform_job(
        client,
        str(accepted.get("job_id")),
        label="Evaluating suite",
        timeout_seconds=float(timeout_s),
        timeout_operation="suite evaluation",
    )
    output_result({**job, "execution_id": execution_id}, ctx=ctx)


@agent_tests_group.command("status")
@click.argument("execution_id")
@click.pass_context
@pass_resolver
@run_async
async def execution_status(
    ctx: click.Context,
    execution_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Show execution counters and per-case results."""
    del resolver
    execution = await client.get(f"{_BASE}/executions/{execution_id}")
    execution.raise_for_status()
    results = await client.get(f"{_BASE}/executions/{execution_id}/results")
    results.raise_for_status()
    output_result(
        {"execution": execution.json(), "results": results.json()}, ctx=ctx
    )


@agent_tests_group.command("results")
@click.argument("execution_id")
@click.pass_context
@pass_resolver
@run_async
async def execution_results(
    ctx: click.Context,
    execution_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """List per-case results with linked debugger run IDs."""
    del resolver
    response = await client.get(f"{_BASE}/executions/{execution_id}/results")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@agent_tests_group.command("compare")
@click.argument("execution_id")
@click.pass_context
@pass_resolver
@run_async
async def execution_compare(
    ctx: click.Context,
    execution_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Summarize regressions, failures, usage deltas, and linked runs."""
    del resolver
    response = await client.get(f"{_BASE}/executions/{execution_id}/results")
    response.raise_for_status()
    rows = response.json()
    summary: dict[str, Any] = {
        "execution_id": execution_id,
        "total": len(rows),
        "regressions": [],
        "failures": [],
        "usage_deltas": {},
        "runs": [],
    }
    for row in rows:
        comparison = row.get("comparison") or {}
        case_id = row.get("case_id")
        if comparison.get("verdict") in ("regression", "mixed"):
            summary["regressions"].append(
                {
                    "case_id": case_id,
                    "regressions": comparison.get("regressions", []),
                    "verdict": comparison.get("verdict"),
                }
            )
        if row.get("status") in ("failed", "error"):
            summary["failures"].append(
                {"case_id": case_id, "status": row.get("status")}
            )
        usage_delta = comparison.get("usage_delta") or {}
        if usage_delta:
            summary["usage_deltas"][str(case_id)] = usage_delta
        summary["runs"].append(
            {
                "case_id": case_id,
                "baseline_run_id": row.get("baseline_run_id"),
                "candidate_run_id": row.get("candidate_run_id"),
            }
        )
    output_result(summary, ctx=ctx)


@agent_tests_group.command("cancel")
@click.argument("execution_id")
@click.pass_context
@pass_resolver
@run_async
async def execution_cancel(
    ctx: click.Context,
    execution_id: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Cancel an execution (only unfinished synthetic runs are cancelled)."""
    del resolver
    response = await client.post(f"{_BASE}/executions/{execution_id}/cancel")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)
