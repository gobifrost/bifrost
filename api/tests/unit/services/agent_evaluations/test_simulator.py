"""Stateful simulator tests: coherent CRUD, rules, schema gating, escape-proofing."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.agent_evaluations.simulator import (
    Simulator,
    SyntheticToolError,
)
from src.services.agent_evaluations.simulator_models import (
    FixtureError,
    canonical_hash,
    redact_value,
    validate_fixture,
)

from src.services.agent_evaluations.runner import (
    EVALUATION_SYNTHETIC_MODE,
    load_synthetic_router,
    synthetic_operation_id,
)


def test_redaction_preserves_numeric_usage_without_exposing_tokens():
    assert redact_value({
        "tokens": 42, "max_tokens": 100, "api_token": "secret", "input_tokens": "secret",
    }) == {
        "tokens": 42, "max_tokens": 100, "api_token": "[REDACTED]", "input_tokens": "[REDACTED]",
    }


def _fixture(**over) -> dict:
    base = {
        "version": 1,
        "entities": {"ticket": {}},
        "allowed_tools": [
            "create_ticket",
            "get_ticket",
            "list_tickets",
            "update_ticket",
            "delete_ticket",
        ],
        "rules": [],
        "seed_time": "2026-09-18T00:00:00+00:00",
    }
    base.update(over)
    return base


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
        "list_tickets": {"type": "object", "properties": {}},
        "update_ticket": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "delete_ticket": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    }


def test_create_then_get_update_list_delete_is_coherent():
    sim = Simulator(_fixture(), tool_schemas=_schemas())
    created = sim.call("create_ticket", {"title": "VPN down"})
    assert created["id"] == "ticket-0001"
    fetched = sim.call("get_ticket", {"id": created["id"]})
    assert fetched["title"] == "VPN down"
    updated = sim.call(
        "update_ticket", {"id": created["id"], "status": "resolved"}
    )
    assert updated["status"] == "resolved"
    listed = sim.call("list_tickets", {})
    assert listed["total"] == 1
    deleted = sim.call("delete_ticket", {"id": created["id"]})
    assert deleted["deleted"] is True
    with pytest.raises(SyntheticToolError, match="not found"):
        sim.call("get_ticket", {"id": created["id"]})


def test_deterministic_ids_rerun_stable():
    first = Simulator(_fixture(), tool_schemas=_schemas())
    second = Simulator(_fixture(), tool_schemas=_schemas())
    a = first.call("create_ticket", {"title": "A"})
    b = second.call("create_ticket", {"title": "A"})
    assert a["id"] == b["id"]
    assert first.state_hash() == second.state_hash()


def test_schema_mismatch_fails_closed():
    sim = Simulator(_fixture(), tool_schemas=_schemas())
    with pytest.raises(SyntheticToolError, match="schema"):
        sim.call("create_ticket", {"wrong": 1})


def test_unhandled_tool_fails_closed():
    sim = Simulator(_fixture(allowed_tools=["summarize_thread"]))
    with pytest.raises(SyntheticToolError, match="no synthetic behavior"):
        sim.call("summarize_thread", {})


def test_tool_not_allowlisted_rejected():
    sim = Simulator(_fixture(allowed_tools=["get_ticket"]))
    with pytest.raises(SyntheticToolError, match="not enabled"):
        sim.call("create_ticket", {"title": "x"})


def test_fixture_rules_match_and_mutate():
    fixture = _fixture(
        rules=[
            {
                "tool": "get_ticket",
                "match_args": {"id": "ticket-vip"},
                "return": {"id": "ticket-vip", "priority": "p1"},
            }
        ]
    )
    sim = Simulator(fixture, tool_schemas=_schemas())
    assert sim.call("get_ticket", {"id": "ticket-vip"})["priority"] == "p1"


def test_rule_mutation_upserts_state():
    fixture = _fixture(
        rules=[
            {
                "tool": "create_ticket",
                "return": {"id": "$args.id", "ok": True},
                "mutate": [
                    {
                        "op": "upsert",
                        "entity": "ticket",
                        "id": "$args.id",
                        "set": {"title": "$args.title"},
                    }
                ],
            }
        ]
    )
    sim = Simulator(fixture, tool_schemas=_schemas())
    sim.call("create_ticket", {"id": "t-9", "title": "Rule-made"})
    assert sim.call("get_ticket", {"id": "t-9"})["title"] == "Rule-made"


def test_malicious_tool_name_cannot_escape():
    sim = Simulator(_fixture(), tool_schemas=_schemas())
    for evil in (
        "__import__",
        "os.system",
        "../../../etc/passwd",
        "execute_workflow",
        "search_knowledge; rm -rf /",
    ):
        with pytest.raises(SyntheticToolError):
            sim.call(evil, {})
    # Even with a hostile fixture the dispatcher only knows synthetic ops.
    hostile = _fixture(
        allowed_tools=["__import__"],
        rules=[{"tool": "__import__", "mutate": [{"op": "explode"}]}],
    )
    hostile_sim = Simulator(hostile)
    with pytest.raises((SyntheticToolError, FixtureError)):
        hostile_sim.call("__import__", {})


def test_simulator_has_no_real_execution_import_path():
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[4] / (
        "src/services/agent_evaluations/simulator.py"
    )
    text = source.read_text()
    for forbidden in (
        "services.execution",
        "services.mcp",
        "services.integrations",
        "rabbitmq",
        "real dispatcher",
    ):
        assert forbidden not in text
    models = source.with_name("simulator_models.py").read_text()
    for forbidden in ("services.execution", "services.mcp", "rabbitmq"):
        assert forbidden not in models


def test_secrets_redacted_in_records():
    sim = Simulator(
        _fixture(
            allowed_tools=["create_ticket"],
            rules=[
                {
                    "tool": "create_ticket",
                    "return": {"api_key": "live-secret", "id": "t-1"},
                }
            ],
        )
    )
    sim.call("create_ticket", {"title": "x", "password": "hunter2"})
    record = sim.records[0]
    assert record["arguments"]["password"] == "[REDACTED]"
    assert record["result"]["api_key"] == "[REDACTED]"


def test_malformed_fixtures_fail_at_save_time():
    with pytest.raises(FixtureError):
        validate_fixture({"version": 999})
    with pytest.raises(FixtureError):
        validate_fixture({"entities": [], "allowed_tools": []})
    with pytest.raises(FixtureError):
        validate_fixture({"entities": {}, "allowed_tools": "nope"})
    with pytest.raises(FixtureError):
        validate_fixture({"entities": {}, "allowed_tools": [], "rules": [{}]})


@pytest.mark.parametrize("fields", [
    {"when": {"args": {"id": "expected"}}, "then": {"return": {"title": "expected"}}},
    {"match_args": []},
    {"mutate": {}},
    {"tool": ""},
])
def test_unsupported_rule_shapes_fail_before_simulation(fields):
    with pytest.raises(FixtureError):
        validate_fixture({"rules": [{"tool": "read_ticket", **fields}]})


def test_canonical_hash_stable():
    assert canonical_hash({"b": 1, "a": 2}) == canonical_hash({"a": 2, "b": 1})


def test_operation_ids_scope_replayed_provider_ids_to_each_child_run():
    provider_call_id = "call_123"
    assert synthetic_operation_id(uuid4(), provider_call_id) != synthetic_operation_id(
        uuid4(), provider_call_id
    )


@pytest.mark.asyncio
async def test_synthetic_router_rejects_cyclic_parent_chain():
    """Malformed parent links must fail closed before a simulator is loaded."""
    first_id, second_id = uuid4(), uuid4()
    marker = {"evaluation": {"mode": EVALUATION_SYNTHETIC_MODE, "evaluation_only": True}}
    correlation = {"evaluation_mode": EVALUATION_SYNTHETIC_MODE}
    rows = {
        first_id: SimpleNamespace(
            id=first_id,
            parent_run_id=second_id,
            root_run_id=None,
            trigger_type=EVALUATION_SYNTHETIC_MODE,
            execution_snapshot=marker,
            correlation=correlation,
        ),
        second_id: SimpleNamespace(
            id=second_id,
            parent_run_id=first_id,
            root_run_id=None,
            trigger_type=EVALUATION_SYNTHETIC_MODE,
            execution_snapshot=marker,
            correlation=correlation,
        ),
    }

    class Session:
        async def get(self, _model, row_id):
            return rows.get(row_id)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    assert await load_synthetic_router(lambda: Session(), first_id) is None
