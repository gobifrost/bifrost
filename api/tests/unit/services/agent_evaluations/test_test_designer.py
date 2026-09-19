"""Test Designer tests: drafts, redaction, validation, dedup, acceptance."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.services.agent_evaluations.test_designer import (
    DesignerError,
    accept_proposal,
    build_designer_input,
    build_designer_snapshot,
    deduplicate_proposals,
    designer_output_schema,
    designer_prompt,
    proposal_signature,
    redact_history,
    validate_designer_output,
)


def _schemas() -> dict:
    return {
        "create_ticket": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        "get_ticket": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    }


def _proposal(**over) -> dict:
    base = {
        "name": "vip-lookup",
        "input": {"task": "look up ticket-0001"},
        "fixture": {
            "entities": {"ticket": {"ticket-0001": {"id": "ticket-0001"}}},
            "allowed_tools": ["create_ticket", "get_ticket"],
            "rules": [],
        },
        "simulator_policy": {},
        "assertions": [
            {
                "type": "tool_called",
                "params": {"tool": "get_ticket"},
            },
            {
                "type": "tool_args",
                "params": {"tool": "get_ticket", "args": {"id": "ticket-0001"}},
            },
        ],
        "expected_tools": ["get_ticket"],
        "forbidden_tools": [],
        "coverage": "success",
    }
    base.update(over)
    return base


def test_designer_snapshot_is_ephemeral_and_evaluation_only():
    snapshot = build_designer_snapshot()
    assert snapshot["agent_id"] is None
    assert snapshot["agent_name"] == "test_designer"
    assert snapshot["evaluation"]["evaluation_only"] is True
    assert snapshot["output_schema"] == designer_output_schema()
    assert "regression test cases" in designer_prompt().lower()


def test_designer_input_redacts_nothing_but_assembles():
    snapshot = {
        "agent_name": "support",
        "system_prompt": "Be helpful.",
        "tools": [{"name": "get_ticket"}],
        "limits": {"max_iterations": 5},
    }
    built = build_designer_input(
        agent_snapshot=snapshot,
        tool_schemas=_schemas(),
        suite_goal="regression",
        requested_count=2,
    )
    assert built["agent"]["name"] == "support"
    assert built["requested_count"] == 2
    with pytest.raises(DesignerError, match="requested_count"):
        build_designer_input(
            agent_snapshot=snapshot, tool_schemas=_schemas(),
            suite_goal="x", requested_count=0,
        )
    with pytest.raises(DesignerError, match="tool schema"):
        build_designer_input(
            agent_snapshot=snapshot, tool_schemas={},
            suite_goal="x", requested_count=1,
        )


def test_unauthorized_history_omitted_and_allowed_redacted():
    runs = [
        {
            "run_id": "allowed-1",
            "input": {"task": "hi"},
            "output": {"api_token": "live-secret"},
            "tool_calls": [{"name": "get_ticket", "password": "hunter2"}],
        },
        {"run_id": "forbidden-9", "input": {}, "output": {}},
    ]
    redacted = redact_history(runs, allowed_run_ids={"allowed-1"})
    assert [r["run_id"] for r in redacted] == ["allowed-1"]
    assert redacted[0]["output"] == {"api_token": "[REDACTED]"}
    assert redacted[0]["tool_calls"][0]["password"] == "[REDACTED]"


def test_coherent_create_get_chain_validates():
    proposals = validate_designer_output(
        {"proposals": [_proposal()]}, tool_schemas=_schemas()
    )
    assert len(proposals) == 1


def test_malformed_output_lists_problems_for_repair():
    with pytest.raises(DesignerError, match="unknown tools"):
        validate_designer_output(
            {
                "proposals": [
                    _proposal(
                        fixture={
                            "entities": {},
                            "allowed_tools": ["teleport"],
                            "rules": [],
                        }
                    )
                ]
            },
            tool_schemas=_schemas(),
        )
    with pytest.raises(DesignerError, match="unknown assertion type"):
        validate_designer_output(
            {
                "proposals": [
                    _proposal(
                        assertions=[{"type": "vibes", "params": {}}],
                    )
                ]
            },
            tool_schemas=_schemas(),
        )
    with pytest.raises(DesignerError, match="unresolved entity"):
        validate_designer_output(
            {
                "proposals": [
                    _proposal(
                        fixture={
                            "entities": {},
                            "allowed_tools": ["get_ticket"],
                            "rules": [],
                        }
                    )
                ]
            },
            tool_schemas=_schemas(),
        )
    with pytest.raises(DesignerError, match="proposals"):
        validate_designer_output({"nope": []}, tool_schemas=_schemas())


def test_dedup_keeps_materially_distinct_cases():
    existing = [_proposal()]
    same = _proposal()
    different = _proposal(
        name="vip-lookup-edge",
        coverage="edge",
        assertions=[{"type": "tool_called", "params": {"tool": "create_ticket"}}],
    )
    assert proposal_signature(_proposal()) == proposal_signature(same)
    kept = deduplicate_proposals([same, different], existing)
    assert [p["name"] for p in kept] == ["vip-lookup-edge"]


def test_acceptance_freezes_case_without_publication():
    proposal = _proposal()
    case = accept_proposal(proposal, suite_id=uuid4(), position=3)
    assert case.accepted is True
    assert case.version == 1
    assert case.position == 3
    assert case.provenance == "generated"
    assert case.tags == ["success"]
    assert case.fixture["entities"]["ticket"]["ticket-0001"]["id"] == "ticket-0001"
    # Acceptance is explicit: the proposal dict itself carries no approval.
    assert "accepted" not in proposal


def test_deterministic_acceptance():
    first = accept_proposal(_proposal(), suite_id=uuid4(), position=0)
    second = accept_proposal(_proposal(), suite_id=first.suite_id, position=0)
    assert first.fixture == second.fixture
    assert first.assertions == second.assertions
