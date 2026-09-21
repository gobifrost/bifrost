"""Simulation-only proposed tool contracts (Phase 4e, no DB)."""

from __future__ import annotations

import pytest

from shared.proposed_tools import (
    ProposedToolError,
    assert_no_unresolved_proposed_tools,
    find_unresolved_proposed_tools,
    validate_definition,
    validate_definitions,
)
from src.services.agent_evaluations.candidates import (
    CandidateError,
    assert_production_snapshot,
)
from src.services.agent_evaluations.simulator import (
    Simulator,
    SyntheticToolError,
)
from src.services.agent_evaluations.simulator_models import (
    FixtureError,
    validate_fixture,
)


def _definition(**over) -> dict:
    base = {
        "name": "lookup_order",
        "description": "Look up an order by id.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "total": {"type": "number"}},
            "required": ["id", "total"],
        },
        "behavior": [
            {
                "tool": "lookup_order",
                "match_args": {"id": "o-1"},
                "return": {"id": "o-1", "total": 42.0},
            }
        ],
        "simulation_only": True,
    }
    base.update(over)
    return base


def test_valid_definition_canonical():
    out = validate_definition(_definition())
    assert out["name"] == "lookup_order"
    assert out["simulation_only"] is True
    assert out["behavior"][0]["tool"] == "lookup_order"


@pytest.mark.parametrize(
    "override",
    [
        {"simulation_only": False},
        {"name": "has space"},
        {"name": ""},
        {"name": "has-dash"},
        {"input_schema": "not-an-object"},
        {"output_schema": []},
        {"behavior": [{"tool": "other_tool"}]},
        {"behavior": [{"tool": "lookup_order", "bogus": 1}]},
        {"behavior": [{"tool": "lookup_order", "match_args": "nope"}]},
        {"behavior": [{"tool": "lookup_order", "mutate": {}}]},
        {"target_id": "some-uuid"},
        {"workflow_id": "some-uuid"},
        {"extra": 1},
    ],
)
def test_invalid_definitions_rejected(override):
    body = _definition()
    body.update(override)
    with pytest.raises(ProposedToolError):
        validate_definition(body)


def test_non_object_rejected():
    with pytest.raises(ProposedToolError):
        validate_definition("lookup_order")


def test_collisions_rejected():
    with pytest.raises(ProposedToolError, match="Duplicate"):
        validate_definitions([_definition(), _definition()])
    with pytest.raises(ProposedToolError, match="real tool"):
        validate_definitions([_definition()], real_tool_names={"lookup_order"})
    with pytest.raises(ProposedToolError, match="system tool"):
        validate_definitions([_definition()], system_names={"lookup_order"})
    with pytest.raises(ProposedToolError, match="MCP tool"):
        validate_definitions([_definition()], mcp_names={"lookup_order"})
    with pytest.raises(ProposedToolError, match="delegated tool"):
        validate_definitions([_definition()], delegated_names={"lookup_order"})
    ok = validate_definitions([_definition()], real_tool_names={"other_tool"})
    assert [item["name"] for item in ok] == ["lookup_order"]


def test_finder_and_assert():
    assert find_unresolved_proposed_tools({}) == []
    assert find_unresolved_proposed_tools(
        {"tools": [{"name": "real", "target_id": "x"}]}
    ) == []
    assert find_unresolved_proposed_tools(
        {
            "tools": [
                {"name": "draft_tool", "simulation_only": True},
                {"name": "real", "target_id": "x"},
            ],
            "proposed_tools": [{"name": "fixture_tool"}],
        }
    ) == ["draft_tool", "fixture_tool"]
    assert_no_unresolved_proposed_tools({"tools": []}, context="Test")
    with pytest.raises(ProposedToolError, match="draft_tool"):
        assert_no_unresolved_proposed_tools(
            {"tools": [{"name": "draft_tool", "simulation_only": True}]},
            context="Test",
        )
    with pytest.raises(ProposedToolError, match="malformed"):
        assert_no_unresolved_proposed_tools(
            {"proposed_tools": [{"foo": 1}]},
            context="Test",
        )
    with pytest.raises(ProposedToolError, match="malformed"):
        assert_no_unresolved_proposed_tools(
            {"tools": [{"simulation_only": True}]},
            context="Test",
        )
    with pytest.raises(ProposedToolError, match="malformed"):
        assert_no_unresolved_proposed_tools(
            {"proposed_tools": {"name": "draft", "simulation_only": True}},
            context="Test",
        )


def test_fixture_key_validated():
    validate_fixture({"version": 1, "proposed_tools": [_definition()]})
    with pytest.raises(FixtureError):
        validate_fixture({"version": 1, "proposed_tools": [_definition(name="bad name")]})
    with pytest.raises(FixtureError):
        validate_fixture({"version": 1, "proposed_tools": {"name": "lookup_order"}})


def _sim_fixture(**over) -> dict:
    base = {
        "version": 1,
        "entities": {},
        "proposed_tools": [_definition()],
        "seed_time": "2026-09-18T00:00:00+00:00",
    }
    base.update(over)
    return base


def test_proposed_dispatch_and_output_contract():
    sim = Simulator(_sim_fixture(), tool_schemas={})
    assert "lookup_order" in sim.allowed_tools()
    assert sim.call("lookup_order", {"id": "o-1"}) == {"id": "o-1", "total": 42.0}
    with pytest.raises(SyntheticToolError, match="schema"):
        sim.call("lookup_order", {"wrong": 1})
    with pytest.raises(SyntheticToolError, match="no synthetic behavior"):
        sim.call("lookup_order", {"id": "o-2"})


def test_proposed_scalar_output_contract():
    sim = Simulator(
        _sim_fixture(
            proposed_tools=[
                _definition(
                    name="ping",
                    output_schema={"type": "string"},
                    behavior=[
                        {"tool": "ping", "match_args": {}, "return": "pong"}
                    ],
                )
            ]
        ),
        tool_schemas={},
    )
    assert sim.call("ping", {"id": "x"}) == "pong"


def test_proposed_output_mismatch_fails_closed():
    sim = Simulator(
        _sim_fixture(
            proposed_tools=[
                _definition(
                    behavior=[
                        {
                            "tool": "lookup_order",
                            "match_args": {"id": "o-9"},
                            "return": {"id": "o-9"},
                        }
                    ]
                )
            ]
        ),
        tool_schemas={},
    )
    with pytest.raises(SyntheticToolError, match="output"):
        sim.call("lookup_order", {"id": "o-9"})


def test_proposed_collision_with_snapshot_tools_rejected():
    with pytest.raises(FixtureError, match="collide"):
        Simulator(
            _sim_fixture(),
            tool_schemas={
                "lookup_order": {"type": "object", "properties": {}}
            },
        )


def test_proposed_enabled_without_allowed_list_entry():
    sim = Simulator(
        _sim_fixture(allowed_tools=["other_tool"]),
        tool_schemas={"other_tool": {"type": "object", "properties": {}}},
    )
    assert "lookup_order" in sim.allowed_tools()
    assert sim.call("lookup_order", {"id": "o-1"})["total"] == 42.0


def test_production_guard_blocks_unresolved():
    assert_production_snapshot({"tools": [{"name": "real", "target_id": "x"}]})
    assert_production_snapshot(None)
    with pytest.raises(CandidateError, match="draft_tool"):
        assert_production_snapshot(
            {"tools": [{"name": "draft_tool", "simulation_only": True}]}
        )
    with pytest.raises(CandidateError, match="fixture_tool"):
        assert_production_snapshot({"proposed_tools": [{"name": "fixture_tool"}]})
