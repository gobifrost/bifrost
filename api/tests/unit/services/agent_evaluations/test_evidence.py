"""Durable assertion evidence for engine-owned synthetic calls."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.models.orm.agent_evaluations import (
    AgentSimulationSession,
    AgentSimulationToolRecord,
)
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.ai_usage import AIUsage
from src.services.agent_evaluations.assertions import evaluate_assertions
from src.services.agent_evaluations.evidence import (
    load_persisted_evaluation_evidence,
)
from src.services.agent_runtime import types as runtime_types


async def _usage_run(db_session, usage_rows: list[dict], *, tokens_used: int = 0):
    """Minimal durable tree (root run + simulation session) with AIUsage rows."""
    root_id = uuid4()
    db_session.add(
        AgentRun(
            id=root_id,
            root_run_id=root_id,
            trigger_type="evaluation_synthetic",
            status="completed",
            iterations_used=0,
            tokens_used=tokens_used,
        )
    )
    await db_session.flush()
    db_session.add(
        AgentSimulationSession(
            root_run_id=root_id,
            run_id=root_id,
            case_version=1,
            fixture={"seed_time": "2026-09-18T00:00:00+00:00"},
            state={"clock_ticks": 0, "entities": {}},
        )
    )
    for row in usage_rows:
        db_session.add(AIUsage(agent_run_id=root_id, **row))
    await db_session.flush()
    return root_id


@pytest.mark.asyncio
async def test_engine_tool_calls_are_durable_assertion_evidence(db_session):
    root_id = uuid4()
    simulation_id = uuid4()
    db_session.add(
        AgentRun(
            id=root_id,
            root_run_id=root_id,
            trigger_type="evaluation_synthetic",
            status="completed",
            iterations_used=0,
            tokens_used=0,
        )
    )
    await db_session.flush()
    db_session.add(
        AgentSimulationSession(
            id=simulation_id,
            root_run_id=root_id,
            run_id=root_id,
            case_version=1,
            fixture={"seed_time": "2026-09-18T00:00:00+00:00"},
            state={"clock_ticks": 0, "entities": {}},
        )
    )
    db_session.add_all(
        [
            AgentRunJournalEntry(
                run_id=root_id,
                sequence=1,
                kind=runtime_types.JOURNAL_TOOL_CALL,
                data={
                    "tool_name": "delegate_to_research",
                    "tool_call_id": "delegate-1",
                    "arguments": {"task": "investigate outage"},
                },
            ),
            AgentRunJournalEntry(
                run_id=root_id,
                sequence=2,
                kind=runtime_types.JOURNAL_TOOL_CALL,
                data={
                    "tool_name": "sleep_until",
                    "tool_call_id": "timer-1",
                    "arguments": {"seconds": 60, "reason": "await change"},
                },
            ),
            AgentRunJournalEntry(
                run_id=root_id,
                sequence=3,
                kind=runtime_types.JOURNAL_TOOL_CALL,
                data={
                    "tool_name": "get_ticket",
                    "tool_call_id": "simulator-1",
                    "arguments": {"id": "ticket-1"},
                },
            ),
            # A crash after the call checkpoint can repeat that observable
            # boundary; it remains one logical engine invocation.
            AgentRunJournalEntry(
                run_id=root_id,
                sequence=4,
                kind=runtime_types.JOURNAL_TOOL_CALL,
                data={
                    "tool_name": "delegate_to_research",
                    "tool_call_id": "delegate-1",
                    "arguments": {"task": "investigate outage"},
                },
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        AgentSimulationToolRecord(
            session_id=simulation_id,
            sequence=0,
            operation_id=f"{root_id}:simulator-1",
            tool_name="get_ticket",
            arguments={"id": "ticket-1"},
            result={"id": "ticket-1"},
        )
    )
    # Projection uses this session; retain the fixture's rollback boundary.
    await db_session.flush()

    evidence = await load_persisted_evaluation_evidence(db_session, root_id)

    assert [call["name"] for call in evidence["tool_calls"]] == [
        "delegate_to_research",
        "sleep_until",
        "get_ticket",
    ]
    assert evidence["tool_calls"][0]["arguments"] == {
        "task": "investigate outage"
    }
    assert evidence["tool_calls"][1]["arguments"] == {
        "seconds": 60,
        "reason": "await change",
    }
    assert evidence["tool_calls"][0]["journal_references"] == [
        {"run_id": str(root_id), "sequence": 1, "kind": "tool_call"},
        {"run_id": str(root_id), "sequence": 4, "kind": "tool_call"},
    ]
    outcomes = evaluate_assertions(
        [
            {"type": "tool_called", "params": {"tool": "delegate_to_research"}},
            {"type": "forbidden_tool", "params": {"tool": "sleep_until"}},
        ],
        evidence,
    )
    assert outcomes[0]["passed"] is True
    assert outcomes[1]["passed"] is False


@pytest.mark.asyncio
async def test_usage_evidence_aggregates_cache_counts_and_fraction(db_session):
    root_id = await _usage_run(
        db_session,
        [
            {
                "provider": "openai",
                "model": "fixture",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 40,
                "cache_write_tokens": 10,
            },
            {
                "provider": "openai",
                "model": "fixture",
                "input_tokens": 300,
                "output_tokens": 0,
                "cache_read_tokens": 150,
                "cache_write_tokens": 0,
            },
        ],
    )

    evidence = await load_persisted_evaluation_evidence(db_session, root_id)

    usage = evidence["usage"]
    assert usage["input_tokens"] == 400
    assert usage["output_tokens"] == 50
    assert usage["cache_read_tokens"] == 190
    assert usage["cache_write_tokens"] == 10
    assert usage["cache_hit_fraction"] == pytest.approx(190 / 400)
    assert usage["model_calls"] == 2


@pytest.mark.asyncio
async def test_usage_evidence_records_observed_zero_without_fraction(db_session):
    root_id = await _usage_run(
        db_session,
        [
            {
                "provider": "openai",
                "model": "fixture",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ],
    )

    evidence = await load_persisted_evaluation_evidence(db_session, root_id)

    usage = evidence["usage"]
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["cache_read_tokens"] == 0
    assert usage["cache_write_tokens"] == 0
    assert usage["tokens"] == 0
    assert usage["cache_hit_fraction"] is None


@pytest.mark.asyncio
async def test_usage_tokens_do_not_double_count_cache(db_session):
    root_id = await _usage_run(
        db_session,
        [
            {
                "provider": "anthropic",
                "model": "fixture",
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 90,
                "cache_write_tokens": 5,
            }
        ],
    )

    evidence = await load_persisted_evaluation_evidence(db_session, root_id)

    usage = evidence["usage"]
    # Cache reads are already inside input tokens: 100 + 20, not + 90 again.
    assert usage["tokens"] == 120
    assert usage["cache_hit_fraction"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_usage_evidence_excludes_sequence_zero_summary_usage(db_session):
    root_id = await _usage_run(
        db_session,
        [
            {
                "provider": "openai",
                "model": "runtime-a",
                "sequence": 1,
                "input_tokens": 400,
                "output_tokens": 100,
                "cache_read_tokens": 75,
                "cache_write_tokens": 8,
                "cost": Decimal("0.10"),
            },
            {
                "provider": "openai",
                "model": "runtime-b",
                "sequence": 4,
                "input_tokens": 200,
                "output_tokens": 24,
                "cache_read_tokens": 25,
                "cost": Decimal("0.20"),
            },
            {
                "provider": "openai",
                "model": "summarizer",
                "sequence": 0,
                "input_tokens": 800,
                "output_tokens": 98,
                "cache_read_tokens": 800,
                "cache_write_tokens": 800,
                "cost": Decimal("0.30"),
            },
        ],
        # Runtime usage rows are the authoritative evidence total when they
        # exist, rather than this stale scalar.
        tokens_used=999,
    )

    evidence = await load_persisted_evaluation_evidence(db_session, root_id)

    usage = evidence["usage"]
    assert usage["input_tokens"] == 600
    assert usage["output_tokens"] == 124
    assert usage["tokens"] == 724
    assert usage["cache_read_tokens"] == 100
    assert usage["cache_write_tokens"] == 8
    assert usage["cache_hit_fraction"] == pytest.approx(100 / 600)
    assert Decimal(usage["cost_usd"]) == Decimal("0.30")
    assert usage["model_calls"] == 2
