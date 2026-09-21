"""Test Designer agent service: generate proposed cases, never approve them.

The designer runs as an evaluation-only ephemeral AgentRun snapshot so it
receives normal tracing, budgeting, output enforcement, and debugger
support without requiring a mutable system Agent row. Its caller-owned
output schema is enforced; generated cases remain drafts
(``accepted=False``) until explicitly accepted, which freezes a new case
version. Reruns never regenerate an accepted case, and the evaluated agent
never defines its own passing criteria.
"""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any
from uuid import UUID

from src.services.agent_evaluations.simulator_models import (
    canonical_hash,
    redact_value,
)

DESIGNER_VERSION = 1
DESIGNER_AGENT_NAME = "test_designer"
COVERAGE_LABELS = ("success", "failure", "safety", "edge")

TESTING_ASSIGNMENT_KEY = "testing"
TESTING_ASSIGNMENT_HELP = (
    "The 'testing' AI model assignment is not configured. "
    "Configure an AI model profile in System Settings > AI Configuration."
)

MAX_DESIGNER_HISTORY_RUNS = 20
MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN = 50
MAX_DESIGNER_HISTORY_BYTES_PER_RUN = 64 * 1024
MAX_DESIGNER_HISTORY_TOTAL_BYTES = 512 * 1024

LEGACY_HISTORY_STEP_TYPES = frozenset({"tool_call", "tool_result", "tool_error"})

# This is deliberately a concrete prompt contract rather than an inferred list:
# the Designer needs valid parameter shapes, not just assertion names. Keep it in
# sync with ``assertions.validate_assertions``; the unit test verifies coverage
# and validates every example.
DETERMINISTIC_ASSERTION_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "type": "terminal_status",
        "params": {"status": "completed"},
        "guidance": "Use the terminal run status, such as completed or failed.",
    },
    {
        "type": "output_schema",
        "params": {},
        "guidance": "Uses the case output schema when provided, otherwise the run output schema.",
    },
    {
        "type": "output_path",
        "params": {"path": "summary", "equals": "ticket resolved"},
        "guidance": "Use a dot-separated output path; optionally use equals or contains.",
    },
    {
        "type": "tool_called",
        "params": {"tool": "get_ticket"},
        "guidance": "The named published tool must be called at least once.",
    },
    {
        "type": "tool_not_called",
        "params": {"tool": "delete_ticket"},
        "guidance": "The named published tool must never be called.",
    },
    {
        "type": "forbidden_tool",
        "params": {"tool": "delete_ticket"},
        "guidance": "Equivalent trajectory guard for a named published tool.",
    },
    {
        "type": "tool_count",
        "params": {"tool": "get_ticket", "count": 1},
        "guidance": "Require an exact nonnegative call count for a published tool.",
    },
    {
        "type": "tool_order",
        "params": {"tools": ["get_ticket", "update_ticket"], "exact": False},
        "guidance": (
            "Require this ordered subsequence; set exact true only to require "
            "the whole call order."
        ),
    },
    {
        "type": "tool_args",
        "params": {"tool": "get_ticket", "args": {"id": "ticket-0001"}},
        "guidance": "Match dot-separated argument paths on at least one call to a published tool.",
    },
    {
        "type": "simulator_state",
        "params": {"path": "entities.ticket.ticket-0001.status", "equals": "resolved"},
        "guidance": "Use a dot-separated simulator-state path; equals is optional.",
    },
    {
        "type": "delegation_tree",
        "params": {"max_children": 0},
        "guidance": "Optionally constrain min_children, max_children, or the exact agents list.",
    },
    {
        "type": "max_iterations",
        "params": {"limit": 5},
        "guidance": "Set a nonnegative numeric limit for total iterations.",
    },
    {
        "type": "max_tokens",
        "params": {"limit": 2000},
        "guidance": "Set a nonnegative numeric limit for total tokens.",
    },
    {
        "type": "max_cost_usd",
        "params": {"limit": 0.05},
        "guidance": "Set a nonnegative numeric USD cost limit.",
    },
    {
        "type": "max_latency_ms",
        "params": {"limit": 5000},
        "guidance": "Set a nonnegative numeric latency limit in milliseconds.",
    },
    {
        "type": "no_real_tools",
        "params": {},
        "guidance": "Require that no real tool executions occurred.",
    },
)


class DesignerError(Exception):
    """Designer input or output cannot be used safely."""


def designer_prompt() -> str:
    return (
        pathlib.Path(__file__).with_name("prompts").joinpath("test_designer.md")
    ).read_text(encoding="utf-8")


def designer_output_schema() -> dict[str, Any]:
    return json.loads(
        pathlib.Path(__file__).with_name("schemas").joinpath(
            "test_designer_output.json"
        ).read_text(encoding="utf-8")
    )


def build_designer_snapshot(
    *,
    model: dict[str, Any] | None = None,
    max_iterations: int = 10,
    max_token_budget: int = 20000,
) -> dict[str, Any]:
    """Ephemeral evaluation-only snapshot for the designer run itself.

    No mutable system Agent row is created or required; the snapshot is
    marked evaluation-only so production enqueue paths reject it.
    """
    return {
        "format_version": 1,
        "agent_id": None,
        "agent_name": DESIGNER_AGENT_NAME,
        "agent_updated_at": None,
        "system_prompt": designer_prompt(),
        "model": dict(model or {}),
        "tools": [],
        "delegated_agents": [],
        "system_tools": [],
        "limits": {
            "max_iterations": max_iterations,
            "max_token_budget": max_token_budget,
        },
        "output_schema": designer_output_schema(),
        "evaluation": {
            "mode": "evaluation_synthetic",
            "evaluation_only": True,
            "designer_version": DESIGNER_VERSION,
        },
    }


def build_designer_input(
    *,
    agent_snapshot: dict[str, Any],
    tool_schemas: dict[str, dict[str, Any]],
    suite_goal: str,
    requested_count: int,
    historical_examples: list[dict[str, Any]] | None = None,
    finding_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the redacted designer input (target + tools + goal + history)."""
    if requested_count < 1 or requested_count > 10:
        raise DesignerError("requested_count must be between 1 and 10.")
    return {
        "agent": {
            "name": agent_snapshot.get("agent_name"),
            "system_prompt": agent_snapshot.get("system_prompt"),
            "tools": [t.get("name") for t in agent_snapshot.get("tools", [])],
            "limits": agent_snapshot.get("limits", {}),
        },
        "tool_schemas": copy.deepcopy(tool_schemas),
        "suite_goal": suite_goal,
        "requested_count": requested_count,
        "assertion_catalog": copy.deepcopy(list(DETERMINISTIC_ASSERTION_CATALOG)),
        "historical_examples": list(historical_examples or []),
        "finding_evidence": [dict(item) for item in (finding_evidence or [])],
        "designer_version": DESIGNER_VERSION,
    }


def redact_history(
    runs: list[dict[str, Any]],
    *,
    allowed_run_ids: set[str],
) -> list[dict[str, Any]]:
    """Authorization + redaction boundary for historical inspiration.

    Runs the caller cannot access are omitted entirely; allowed runs are
    redacted before the designer ever sees them. History inspires realistic
    shapes — it is never replayed as real calls.
    """
    redacted = []
    for run in runs:
        run_id = str(run.get("run_id", run.get("id", "")))
        if run_id not in allowed_run_ids:
            continue
        redacted.append(
            {
                "run_id": run_id,
                "input": redact_value(run.get("input")),
                "output": redact_value(run.get("output")),
                "tool_calls": redact_value(run.get("tool_calls", [])),
            }
        )
    return redacted


def designer_testing_model(*, profile_id: UUID, config) -> dict[str, Any]:
    """Freeze the resolved testing profile into the Designer snapshot model.

    Uses the existing execution-snapshot model shape (profile id plus every
    resolved non-credential setting) so the runtime re-resolves only
    credentials from the live profile while prompt/transport/caps stay
    pinned at admission. The Designer has no agent-level token override.
    """
    return {
        "profile_id": str(profile_id),
        "provider": config.provider,
        "model": config.model,
        "llm_max_tokens": None,
        "endpoint": config.endpoint,
        "openai_transport": config.openai_transport,
        "anthropic_prompt_cache_supported": config.anthropic_prompt_cache_supported,
        "default_max_tokens": config.default_max_tokens,
        "extra_params": dict(config.extra_params or {}),
    }


async def load_designer_history(session, runs: list) -> list[dict[str, Any]]:
    """Project explicitly selected historical runs into redacted designer input.

    The caller (route) has already applied run visibility and tenant scoping
    before this query runs; only these run IDs are projected — descendants
    are never expanded, so a hidden child is never smuggled in. Tool
    evidence comes from durable ``agent_tool_invocations`` rows (actual
    arguments/result/error); runs that predate invocations fall back to
    legacy ``agent_run_steps`` rows. Redaction happens before the designer
    receives anything; oversized projections trim tool calls (marked with
    ``truncated``) instead of failing or silently dropping selected runs.
    """
    ordered = sorted(
        runs,
        key=lambda run: (run.created_at is None, run.created_at or 0, run.id),
    )[:MAX_DESIGNER_HISTORY_RUNS]
    if not ordered:
        return []
    raw_items = []
    for run in ordered:
        tool_calls, truncated = await _history_tool_calls(session, run)
        raw_items.append(
            {
                "run_id": str(run.id),
                "input": run.input,
                "output": run.output,
                "tool_calls": tool_calls,
                "_truncated": truncated,
            }
        )
    allowed = {item["run_id"] for item in raw_items}
    redacted = redact_history(raw_items, allowed_run_ids=allowed)
    by_id = {item["run_id"]: item for item in redacted}
    fitted = []
    for raw in raw_items:
        item = by_id.get(raw["run_id"])
        if item is None:  # pragma: no cover — redact_history keeps allowed runs.
            continue
        item = _fit_history_item(item, truncated=raw["_truncated"])
        fitted.append(item)
    _fit_history_total(fitted)
    return fitted


async def _history_tool_calls(session, run) -> tuple[list[dict[str, Any]], bool]:
    """Durable invocations first, legacy steps only when invocations are absent."""
    from sqlalchemy import select

    from src.models.orm.agent_runs import AgentRunStep, AgentToolInvocation

    invocations = (
        await session.execute(
            select(AgentToolInvocation)
            .where(AgentToolInvocation.run_id == run.id)
            .order_by(
                AgentToolInvocation.started_at,
                AgentToolInvocation.completed_at,
                AgentToolInvocation.operation_id,
            )
            .limit(MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN + 1)
        )
    ).scalars().all()
    if invocations:
        truncated = len(invocations) > MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN
        return [
            _invocation_history_item(invocation)
            for invocation in invocations[:MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN]
        ], truncated
    steps = (
        await session.execute(
            select(AgentRunStep)
            .where(
                AgentRunStep.run_id == run.id,
                AgentRunStep.type.in_(LEGACY_HISTORY_STEP_TYPES),
            )
            .order_by(AgentRunStep.step_number)
            .limit(MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN + 1)
        )
    ).scalars().all()
    truncated = len(steps) > MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN
    calls = []
    for step in steps[:MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN]:
        item = _legacy_step_history_item(step)
        if item is not None:
            calls.append(item)
    return calls, truncated


def _invocation_history_item(invocation) -> dict[str, Any]:
    """Preserve the structured invocation shape; redaction happens later."""
    item: dict[str, Any] = {
        "tool_name": invocation.tool_name,
        "arguments": copy.deepcopy(invocation.arguments or {}),
    }
    if invocation.result is not None:
        item["result"] = copy.deepcopy(invocation.result)
    if invocation.error is not None:
        item["error"] = invocation.error
    return item


def _legacy_step_history_item(step) -> dict[str, Any] | None:
    """Project one legacy step row; malformed rows are skipped, not guessed."""
    content = step.content if isinstance(step.content, dict) else None
    if content is None:
        return None
    tool_name = content.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    item: dict[str, Any] = {"tool_name": tool_name}
    if step.type == "tool_call":
        item["arguments"] = copy.deepcopy(content.get("arguments") or {})
    elif step.type == "tool_result":
        if content.get("arguments") is not None:
            item["arguments"] = copy.deepcopy(content.get("arguments"))
        if content.get("result") is not None:
            result = content["result"]
            # Legacy steps serialized structured tool results as JSON text.
            # Decode before redaction so nested credential keys remain
            # protected and the Designer sees the actual response shape.
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (ValueError, TypeError):
                    pass
            item["result"] = result
    elif step.type == "tool_error":
        if content.get("arguments") is not None:
            item["arguments"] = copy.deepcopy(content.get("arguments"))
        if content.get("error") is not None:
            item["error"] = content.get("error")
    else:  # pragma: no cover — caller filters to known step types.
        return None
    return item


def _history_item_size(item: dict[str, Any]) -> int:
    return len(json.dumps(item, default=str, sort_keys=True).encode())


def _fit_history_item(item: dict[str, Any], *, truncated: bool) -> dict[str, Any]:
    """Trim one run's tool calls until it fits the per-run byte budget."""
    while (item.get("tool_calls") or []) and (
        _history_item_size(item) > MAX_DESIGNER_HISTORY_BYTES_PER_RUN
    ):
        item["tool_calls"] = item["tool_calls"][:-1]
        truncated = True
    if _history_item_size(item) > MAX_DESIGNER_HISTORY_BYTES_PER_RUN:
        raise DesignerError(
            f"Historical run {item.get('run_id')} input/output alone exceeds "
            f"{MAX_DESIGNER_HISTORY_BYTES_PER_RUN} bytes; deselect it or "
            "narrow the selected history."
        )
    if truncated:
        item["truncated"] = True
    return item


def _fit_history_total(items: list[dict[str, Any]]) -> None:
    """Trim trailing tool calls across runs until the total payload fits."""
    total = sum(_history_item_size(item) for item in items)
    while total > MAX_DESIGNER_HISTORY_TOTAL_BYTES:
        widest = None
        for item in items:
            calls = item.get("tool_calls") or []
            if calls and (widest is None or len(calls) > len(widest.get("tool_calls") or [])):
                widest = item
        if widest is None:
            raise DesignerError(
                "Selected history inputs/outputs alone exceed "
                f"{MAX_DESIGNER_HISTORY_TOTAL_BYTES} bytes; deselect runs "
                "or narrow the selected history."
            )
        calls = widest["tool_calls"]
        widest["tool_calls"] = calls[:-1]
        widest["truncated"] = True
        total = sum(_history_item_size(item) for item in items)


def validate_designer_output(
    output: dict[str, Any],
    *,
    tool_schemas: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enforce the caller-owned schema and tool/entity coherence.

    Returns the validated proposal list. Raises :class:`DesignerError`
    listing every problem so the caller can repair or reject the draft —
    malformed output never becomes a case.
    """
    from src.services.agent_evaluations.assertions import validate_assertions
    from src.services.agent_evaluations.simulator_models import FixtureError, validate_fixture

    problems: list[str] = []
    if not isinstance(output, dict) or not isinstance(output.get("proposals"), list):
        raise DesignerError("Designer output must be an object with 'proposals'.")
    proposals = output["proposals"]
    if not (1 <= len(proposals) <= 10):
        raise DesignerError("Designer must propose 1-10 cases.")
    for index, proposal in enumerate(proposals):
        if isinstance(proposal, dict) and "fixture" in proposal:
            try:
                validate_fixture(proposal["fixture"])
            except FixtureError as exc:
                raise DesignerError(f"Proposal {index} has an invalid fixture: {exc}") from exc
        if isinstance(proposal, dict) and "assertions" in proposal:
            assertions = proposal["assertions"]
            if not isinstance(assertions, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("params", {}), dict)
                for item in assertions
            ):
                raise DesignerError(f"Proposal {index} has malformed assertions.")
        problems.extend(_validate_proposal(index, proposal, tool_schemas))
    if problems:
        raise DesignerError(
            "Designer output failed validation: " + "; ".join(problems)
        )
    for proposal in proposals:
        fixture = proposal["fixture"]
        if "version" not in fixture:
            fixture = {"version": 1, **fixture}
        validate_fixture(fixture)
        validate_assertions(proposal["assertions"])
    return proposals


def _validate_proposal(
    index: int, proposal: Any, tool_schemas: dict[str, dict[str, Any]]
) -> list[str]:
    from src.services.agent_evaluations.assertions import KNOWN_ASSERTION_TYPES

    problems = []
    if not isinstance(proposal, dict):
        return [f"proposal {index} must be an object"]
    for key in ("name", "input", "fixture", "assertions", "coverage"):
        if key not in proposal:
            problems.append(f"proposal {index} is missing {key!r}")
    if problems:
        return problems
    if proposal["coverage"] not in COVERAGE_LABELS:
        problems.append(
            f"proposal {index} has unknown coverage {proposal['coverage']!r}"
        )
    fixture = proposal["fixture"]
    allowed = fixture.get("allowed_tools", [])
    unknown_tools = [t for t in allowed if t not in tool_schemas]
    if unknown_tools:
        problems.append(
            f"proposal {index} references unknown tools: {', '.join(sorted(unknown_tools))}"
        )
    for list_key in ("expected_tools", "forbidden_tools"):
        for tool in proposal.get(list_key, []):
            if tool not in tool_schemas:
                problems.append(
                    f"proposal {index} references unknown {list_key[:-1]} {tool!r}"
                )
    for assertion in proposal["assertions"]:
        atype = assertion.get("type") if isinstance(assertion, dict) else None
        if atype not in KNOWN_ASSERTION_TYPES:
            problems.append(
                f"proposal {index} uses unknown assertion type {atype!r}"
            )
        # A semantic judge consumes a cost-bearing platform model profile.
        # It is configured and frozen only by an authorized human at case
        # save time, never smuggled into a generated proposal.
        if atype == "llm_judge":
            problems.append(
                f"proposal {index} cannot configure an llm_judge assertion"
            )
        if atype in ("tool_called", "tool_not_called", "forbidden_tool", "tool_count", "tool_args"):
            tool = (assertion.get("params") or {}).get("tool")
            if tool not in tool_schemas:
                problems.append(
                    f"proposal {index} asserts on unknown tool {tool!r}"
                )
    problems.extend(
        _check_entity_references(index, proposal, set(tool_schemas))
    )
    return problems


def _check_entity_references(
    index: int, proposal: dict[str, Any], known_tools: set[str]
) -> list[str]:
    """Reject assertions/rules referencing entities that cannot resolve.

    An ID resolves when it exists in the fixture's initial collections or a
    simulated ``create_<entity>`` in the allowed tools can allocate it.
    """
    problems = []
    entities = proposal["fixture"].get("entities", {})
    known_ids = {
        str(row_id)
        for collection in entities.values()
        if isinstance(collection, dict)
        for row_id in collection
    }
    creatable = {
        tool[len("create_"):]
        for tool in proposal["fixture"].get("allowed_tools", [])
        if tool.startswith("create_")
    }
    referenced = _referenced_ids(proposal)
    for ref_id in sorted(referenced):
        if ref_id not in known_ids and not creatable:
            problems.append(
                f"proposal {index} references unresolved entity {ref_id!r}"
            )
    return problems


def _referenced_ids(proposal: dict[str, Any]) -> set[str]:
    ids: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "id" and isinstance(item, str):
                    ids.add(item)
                else:
                    _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(proposal.get("assertions", []))
    for rule in proposal["fixture"].get("rules", []):
        _walk(rule.get("match_args", {}))
    return ids


def proposal_signature(proposal: dict[str, Any]) -> str:
    """Stable identity for dedup: tools + assertions + input shape."""
    tools = sorted(
        {a.get("params", {}).get("tool", a.get("type", "")) for a in proposal["assertions"]}
    )
    return canonical_hash(
        {
            "tools": tools,
            "assertions": sorted(a.get("type", "") for a in proposal["assertions"]),
            "input": proposal.get("input"),
            "coverage": proposal.get("coverage"),
        }
    )


def deduplicate_proposals(
    proposals: list[dict[str, Any]],
    existing_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep materially distinct proposals across coverage labels."""
    seen = {proposal_signature(case) for case in existing_cases}
    kept = []
    for proposal in proposals:
        signature = proposal_signature(proposal)
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(proposal)
    return kept


def accept_proposal(
    proposal: dict[str, Any],
    *,
    suite_id: UUID,
    position: int,
    provenance_run_ids: list[UUID] | None = None,
):
    """Freeze a draft proposal into a new accepted case version.

    Acceptance is explicit and reviewable: the returned ORM object carries
    ``accepted=True`` with redacted frozen fixtures. Drafts
    (``accepted=False``) are built by the caller from the same proposal;
    nothing here ever publishes or auto-approves.
    """
    from src.models.orm.agent_evaluations import AgentEvaluationCase

    return AgentEvaluationCase(
        suite_id=suite_id,
        name=proposal["name"],
        position=position,
        enabled=True,
        version=1,
        input=redact_value(proposal.get("input")),
        fixture=redact_value(proposal.get("fixture", {})),
        simulator_policy=proposal.get("simulator_policy", {}),
        assertions=proposal.get("assertions", []),
        expected_tools=proposal.get("expected_tools", []),
        forbidden_tools=proposal.get("forbidden_tools", []),
        output_schema=proposal.get("output_schema"),
        repetitions=1,
        scoring_policy={},
        provenance="generated",
        provenance_run_ids=[str(r) for r in (provenance_run_ids or [])],
        tags=[proposal.get("coverage", "edge")],
        accepted=True,
    )


async def materialize_designer_drafts(session, run) -> int:
    """Persist a completed Designer AgentRun as review-only draft cases.

    The completion path is intentionally server-owned: only output produced
    under the immutable Designer snapshot can reach this function.
    """
    from sqlalchemy import func, select

    from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite
    from src.models.orm.agent_runs import AgentRun
    from src.services.agent_evaluations.assertions import AssertionDefinitionError
    from src.services.agent_evaluations.simulator_models import FixtureError

    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run.id)
        .with_for_update(of=AgentRun)
        .execution_options(populate_existing=True)
    )
    if run is None:
        return 0

    correlation = dict(run.correlation or {})
    if not correlation.get("evaluation_designer") or correlation.get("designer_materialized"):
        return 0
    if run.status != "completed" or not isinstance(run.output, dict):
        return 0
    suite = await session.scalar(
        select(AgentEvaluationSuite)
        .where(AgentEvaluationSuite.id == UUID(correlation["designer_suite_id"]))
        .with_for_update()
    )
    if suite is None or (suite.status == "published" and not suite.is_default):
        correlation["designer_materialized"] = True
        correlation["designer_error"] = "The destination suite is unavailable or already published."
        run.correlation = correlation
        return 0
    try:
        proposals = validate_designer_output(
            run.output, tool_schemas=dict(correlation.get("designer_tool_schemas") or {})
        )
    except (DesignerError, FixtureError, AssertionDefinitionError):
        # Immutable malformed output will not improve on the next sweep. Record
        # the outcome once so invalid runs cannot starve the bounded queue.
        correlation["designer_materialized"] = True
        correlation["designer_error"] = (
            "Generated cases failed validation. Review the Designer output and request corrected drafts."
        )
        run.correlation = correlation
        return 0
    existing = (
        await session.execute(select(AgentEvaluationCase).where(AgentEvaluationCase.suite_id == suite.id))
    ).scalars().all()
    proposals = deduplicate_proposals(
        proposals,
        [{"assertions": list(case.assertions or []), "input": case.input,
          "coverage": (case.tags or ["edge"])[0]} for case in existing],
    )
    for position, proposal in enumerate(proposals):
        version = (
            await session.scalar(select(func.max(AgentEvaluationCase.version)).where(
                AgentEvaluationCase.suite_id == suite.id,
                AgentEvaluationCase.name == proposal["name"],
            ))
        ) or 0
        finding_ids = [
            item for item in (correlation.get("designer_finding_ids") or []) if item
        ]
        linked_finding_id = None
        if len(finding_ids) == 1:
            # The finding may have been deleted while the designer ran.
            # Link only a live same-agent finding, else fall back.
            from src.models.orm.agent_findings import AgentFinding

            try:
                candidate_id = UUID(str(finding_ids[0]))
            except (TypeError, ValueError):
                candidate_id = None
            if candidate_id is not None:
                live = await session.get(AgentFinding, candidate_id)
                if live is not None and live.agent_id == suite.agent_id:
                    linked_finding_id = live.id
        session.add(AgentEvaluationCase(
            suite_id=suite.id, name=proposal["name"], position=position,
            enabled=False, version=version + 1, input=redact_value(proposal.get("input")),
            fixture=redact_value(proposal.get("fixture", {})),
            simulator_policy=proposal.get("simulator_policy", {}),
            assertions=proposal.get("assertions", []),
            expected_tools=proposal.get("expected_tools", []),
            forbidden_tools=proposal.get("forbidden_tools", []),
            output_schema=proposal.get("output_schema"), repetitions=1,
            scoring_policy={}, provenance="finding" if linked_finding_id else "generated",
            finding_id=UUID(str(linked_finding_id)) if linked_finding_id else None,
            provenance_run_ids=list(correlation.get("designer_history_ids") or []),
            tags=[proposal.get("coverage", "edge")], accepted=False,
        ))
    correlation["designer_materialized"] = True
    run.correlation = correlation
    return len(proposals)
