"""Test Designer tests: drafts, redaction, validation, dedup, acceptance."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite
from src.models.orm.agent_runs import AgentRun, AgentRunStep, AgentToolInvocation

from src.services.agent_evaluations import test_designer
from src.services.agent_evaluations.test_designer import (
    DesignerError,
    accept_proposal,
    build_designer_input,
    build_designer_snapshot,
    deduplicate_proposals,
    designer_output_schema,
    designer_prompt,
    designer_testing_model,
    load_designer_history,
    proposal_signature,
    redact_history,
    materialize_designer_drafts,
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
    no_tool_input = build_designer_input(
        agent_snapshot=snapshot, tool_schemas={}, suite_goal="x", requested_count=1
    )
    assert no_tool_input["tool_schemas"] == {}


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


def test_designer_cannot_smuggle_a_semantic_judge_profile():
    with pytest.raises(DesignerError, match="cannot configure an llm_judge"):
        validate_designer_output(
            {
                "proposals": [
                    _proposal(
                        assertions=[
                            {
                                "type": "llm_judge",
                                "params": {
                                    "rubric": "Helpful response",
                                    "prompt_version": "1",
                                    "threshold": 0.8,
                                    "judge_profile_id": str(uuid4()),
                                },
                            }
                        ]
                    )
                ]
            },
            tool_schemas=_schemas(),
        )


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


@pytest.mark.asyncio
async def test_materialization_refreshes_a_preloaded_run_under_lock(db_session):
    """A stale scheduler identity map cannot materialize one Designer run twice."""
    from src.core.database import get_db_context

    suite = AgentEvaluationSuite(
        name=f"designer-race-{uuid4().hex}", status="draft", version=1
    )
    db_session.add(suite)
    await db_session.flush()
    run = AgentRun(
        trigger_type="evaluation_synthetic",
        status="completed",
        output={"proposals": [_proposal()]},
        correlation={
            "evaluation_designer": True,
            "designer_suite_id": str(suite.id),
            "designer_tool_schemas": _schemas(),
        },
    )
    db_session.add(run)
    await db_session.commit()
    stale_run = await db_session.get(AgentRun, run.id)
    assert stale_run is not None
    assert not stale_run.correlation.get("designer_materialized")

    try:
        async with get_db_context() as materializing_session:
            current_run = await materializing_session.get(AgentRun, run.id)
            assert current_run is not None
            assert await materialize_designer_drafts(materializing_session, current_run) == 1

        # This session still has the unmaterialized correlation cached. The
        # row lock must refresh from PostgreSQL before deciding to insert.
        assert await materialize_designer_drafts(db_session, stale_run) == 0
        draft_count = await db_session.scalar(
            select(func.count())
            .select_from(AgentEvaluationCase)
            .where(AgentEvaluationCase.suite_id == suite.id)
        )
        assert draft_count == 1
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.execute(
            delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id)
        )
        await db_session.commit()


def _testing_config() -> SimpleNamespace:
    return SimpleNamespace(
        provider="openai",
        model="gpt-4o-mini",
        endpoint="https://api.openai.com/v1",
        openai_transport=None,
        anthropic_prompt_cache_supported=None,
        default_max_tokens=4096,
        extra_params={"temperature": 0.2},
    )


def test_designer_testing_model_freezes_snapshot_shape_without_credentials():
    profile_id = uuid4()
    model = designer_testing_model(profile_id=profile_id, config=_testing_config())
    assert model == {
        "profile_id": str(profile_id),
        "provider": "openai",
        "model": "gpt-4o-mini",
        "llm_max_tokens": None,
        "endpoint": "https://api.openai.com/v1",
        "openai_transport": None,
        "anthropic_prompt_cache_supported": None,
        "default_max_tokens": 4096,
        "extra_params": {"temperature": 0.2},
    }
    # The snapshot carries the frozen non-credential settings only; the
    # runtime re-resolves the API key from the live profile at execution.
    assert "api_key" not in model


def _history_run(**over) -> AgentRun:
    base = {
        "trigger_type": "autonomous",
        "status": "completed",
        "input": {"task": "look up ticket-0001"},
        "output": {"answer": "ticket found", "api_token": "live-secret"},
    }
    base.update(over)
    return AgentRun(**base)


@pytest.mark.asyncio
async def test_history_projects_invocation_args_results_and_errors(db_session):
    run = _history_run()
    db_session.add(run)
    await db_session.flush()
    operation_prefix = uuid4().hex[:8]
    db_session.add_all(
        [
            AgentToolInvocation(
                operation_id=f"op-{operation_prefix}-1",
                run_id=run.id,
                provider_tool_call_id="call-1",
                tool_name="get_ticket",
                arguments={"id": "ticket-0001"},
                result={"id": "ticket-0001", "password": "hunter2"},
                state="completed",
                idempotency_key=f"{run.id}:call-1",
            ),
            AgentToolInvocation(
                operation_id=f"op-{operation_prefix}-2",
                run_id=run.id,
                provider_tool_call_id="call-2",
                tool_name="update_ticket",
                arguments={"id": "ticket-0001"},
                error="Error: ticket is locked",
                state="failed",
                idempotency_key=f"{run.id}:call-2",
            ),
        ]
    )
    await db_session.commit()
    try:
        history = await load_designer_history(db_session, [run])
        assert len(history) == 1
        item = history[0]
        assert item["run_id"] == str(run.id)
        assert item["output"] == {
            "answer": "ticket found",
            "api_token": "[REDACTED]",
        }
        assert [call["tool_name"] for call in item["tool_calls"]] == [
            "get_ticket",
            "update_ticket",
        ]
        assert item["tool_calls"][0]["arguments"] == {"id": "ticket-0001"}
        assert item["tool_calls"][0]["result"] == {
            "id": "ticket-0001",
            "password": "[REDACTED]",
        }
        assert item["tool_calls"][1]["error"] == "Error: ticket is locked"
        assert "truncated" not in item
    finally:
        await db_session.execute(
            delete(AgentToolInvocation).where(AgentToolInvocation.run_id == run.id)
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_history_falls_back_to_legacy_steps_and_ignores_descendants(db_session):
    run = _history_run(input={"task": "legacy lookup"}, output={"answer": "ok"})
    child = _history_run(
        input={"task": "hidden delegation"},
        output={"secret_token": "child-secret"},
        parent_run_id=None,
    )
    db_session.add_all([run, child])
    await db_session.flush()
    child.parent_run_id = run.id
    child.root_run_id = run.id
    db_session.add_all(
        [
            AgentRunStep(
                run_id=run.id,
                step_number=1,
                type="tool_call",
                content={"tool_name": "get_ticket", "arguments": {"id": "t-1"}},
            ),
            AgentRunStep(
                run_id=run.id,
                step_number=2,
                type="tool_result",
                content={"tool_name": "get_ticket", "result": "ticket t-1"},
            ),
            AgentRunStep(
                run_id=run.id,
                step_number=3,
                type="model",
                content={"text": "thinking"},
            ),
            AgentToolInvocation(
                operation_id=f"op-{uuid4().hex[:8]}-child",
                run_id=child.id,
                provider_tool_call_id="child-call-1",
                tool_name="hidden_tool",
                arguments={"classified": True},
                result={"classified": True},
                state="completed",
                idempotency_key=f"{child.id}:child-call-1",
            ),
        ]
    )
    await db_session.commit()
    try:
        # Only the explicitly selected run is projected: the child's
        # invocation rows exist in the DB but are never expanded into.
        history = await load_designer_history(db_session, [run])
        assert len(history) == 1
        calls = history[0]["tool_calls"]
        assert [call["tool_name"] for call in calls] == [
            "get_ticket",
            "get_ticket",
        ]
        assert calls[0]["arguments"] == {"id": "t-1"}
        assert calls[1]["result"] == "ticket t-1"
        assert all(call["tool_name"] != "hidden_tool" for call in calls)
    finally:
        await db_session.execute(
            delete(AgentToolInvocation).where(
                AgentToolInvocation.run_id.in_([run.id, child.id])
            )
        )
        await db_session.execute(
            delete(AgentRunStep).where(AgentRunStep.run_id == run.id)
        )
        await db_session.execute(
            delete(AgentRun).where(AgentRun.id.in_([run.id, child.id]))
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_history_marks_record_truncation(db_session, monkeypatch):
    monkeypatch.setattr(
        test_designer, "MAX_DESIGNER_HISTORY_TOOL_RECORDS_PER_RUN", 1
    )
    run = _history_run()
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        [
            AgentToolInvocation(
                operation_id=f"op-{uuid4().hex[:8]}-{index}",
                run_id=run.id,
                provider_tool_call_id=f"call-{index}",
                tool_name=f"tool_{index}",
                arguments={"index": index},
                result={"index": index},
                state="completed",
                idempotency_key=f"{run.id}:call-{index}",
            )
            for index in range(3)
        ]
    )
    await db_session.commit()
    try:
        history = await load_designer_history(db_session, [run])
        assert len(history[0]["tool_calls"]) == 1
        assert history[0]["truncated"] is True
    finally:
        await db_session.execute(
            delete(AgentToolInvocation).where(AgentToolInvocation.run_id == run.id)
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_history_empty_selection_projects_nothing(db_session):
    assert await load_designer_history(db_session, []) == []


@pytest.mark.asyncio
async def test_history_trims_tool_calls_to_byte_budget(db_session, monkeypatch):
    monkeypatch.setattr(
        test_designer, "MAX_DESIGNER_HISTORY_BYTES_PER_RUN", 2000
    )
    run = _history_run()
    db_session.add(run)
    await db_session.flush()
    operation_prefix = uuid4().hex[:8]
    db_session.add_all(
        [
            AgentToolInvocation(
                operation_id=f"op-{operation_prefix}-{index}",
                run_id=run.id,
                provider_tool_call_id=f"call-{index}",
                tool_name=f"tool_{index}",
                arguments={"payload": "x" * 1000},
                result={"index": index},
                state="completed",
                idempotency_key=f"{run.id}:call-{index}",
            )
            for index in range(2)
        ]
    )
    await db_session.commit()
    try:
        history = await load_designer_history(db_session, [run])
        # Both calls exceed the budget together; the earliest call is kept,
        # the trim is marked, and input/output are never cut.
        assert len(history[0]["tool_calls"]) == 1
        assert history[0]["tool_calls"][0]["tool_name"] == "tool_0"
        assert history[0]["truncated"] is True
        assert history[0]["input"] == {"task": "look up ticket-0001"}
    finally:
        await db_session.execute(
            delete(AgentToolInvocation).where(AgentToolInvocation.run_id == run.id)
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_invalid_designer_output_is_disposed_once_without_creating_cases(db_session):
    suite = AgentEvaluationSuite(name=f"invalid-designer-{uuid4().hex}", status="draft", version=1)
    db_session.add(suite)
    await db_session.flush()
    proposal = _proposal()
    proposal["fixture"]["rules"] = [{"tool": "get_ticket", "then": {"return": {"ok": True}}}]
    run = AgentRun(
        trigger_type="evaluation_synthetic", status="completed",
        output={"proposals": [proposal]},
        correlation={"evaluation_designer": True, "designer_suite_id": str(suite.id),
                     "designer_tool_schemas": _schemas()},
    )
    db_session.add(run)
    await db_session.commit()
    try:
        assert await materialize_designer_drafts(db_session, run) == 0
        await db_session.commit()
        await db_session.refresh(run)
        assert run.correlation["designer_materialized"] is True
        assert "failed validation" in run.correlation["designer_error"]
        assert await materialize_designer_drafts(db_session, run) == 0
        assert await db_session.scalar(
            select(func.count()).select_from(AgentEvaluationCase)
            .where(AgentEvaluationCase.suite_id == suite.id)
        ) == 0
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id))
        await db_session.commit()


@pytest.mark.parametrize("fixture", [{"rules": ["invented"]}, {"entities": []}, None])
def test_malformed_fixture_is_a_designer_validation_error(fixture):
    with pytest.raises(DesignerError, match="invalid fixture"):
        validate_designer_output({"proposals": [_proposal(fixture=fixture)]}, tool_schemas=_schemas())
