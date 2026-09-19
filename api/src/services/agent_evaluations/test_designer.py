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
) -> dict[str, Any]:
    """Assemble the redacted designer input (target + tools + goal + history)."""
    if requested_count < 1 or requested_count > 10:
        raise DesignerError("requested_count must be between 1 and 10.")
    if not tool_schemas:
        raise DesignerError("Designer requires at least one published tool schema.")
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
        "historical_examples": list(historical_examples or []),
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
    from src.services.agent_evaluations.simulator_models import validate_fixture

    problems: list[str] = []
    if not isinstance(output, dict) or not isinstance(output.get("proposals"), list):
        raise DesignerError("Designer output must be an object with 'proposals'.")
    proposals = output["proposals"]
    if not (1 <= len(proposals) <= 10):
        raise DesignerError("Designer must propose 1-10 cases.")
    for index, proposal in enumerate(proposals):
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
