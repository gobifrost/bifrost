"""Authorized recorded-evidence projection: isolation, proof, fail-closed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from shared import agent_recorded_evidence as evidence_module
from shared.agent_recorded_evaluation import evaluate_recorded_assertions
from shared.agent_recorded_evidence import (
    RecordedEvidenceNotFound,
    RecordedEvidenceOversized,
    load_recorded_run_evidence,
)
from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunJournalEntry,
    AgentRunStep,
    AgentToolInvocation,
)
from src.models.orm.ai_usage import AIUsage
from src.models.orm.organizations import Organization
from src.services.agent_runtime import types as runtime_types

pytestmark = pytest.mark.asyncio

_COMPLETENESS_KEYS = (
    "terminal_status",
    "output",
    "tool_calls",
    "tool_order",
    "tool_arguments",
    "delegation",
    "real_tool_executions",
    "usage.iterations",
    "usage.tokens",
    "usage.cost_usd",
    "usage.latency_ms",
)


def _user(org_id: UUID | None = None, *, superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="recorded@example.com",
        organization_id=org_id,
        is_superuser=superuser,
    )


async def _make_org(db_session, tag: str) -> UUID:
    org = Organization(
        id=uuid4(), name=f"rec-{tag}-{uuid4().hex[:8]}", created_by="recorded-test"
    )
    db_session.add(org)
    await db_session.flush()
    return org.id


async def _add_run(db_session, **overrides) -> UUID:
    overrides.setdefault("trigger_type", "api")
    overrides.setdefault("status", "completed")
    run = AgentRun(id=overrides.pop("id", uuid4()), **overrides)
    if run.root_run_id is None and run.parent_run_id is None:
        run.root_run_id = run.id
    db_session.add(run)
    await db_session.flush()
    return run.id


async def _journal(db_session, run_id: UUID, sequence: int, kind: str, data=None):
    db_session.add(
        AgentRunJournalEntry(
            run_id=run_id, sequence=sequence, kind=kind, data=data or {}
        )
    )
    await db_session.flush()


async def _tool_call(db_session, run_id, sequence, name, call_id, arguments=None):
    await _journal(
        db_session,
        run_id,
        sequence,
        runtime_types.JOURNAL_TOOL_CALL,
        {"tool_name": name, "tool_call_id": call_id, "arguments": arguments or {}},
    )


async def _completion(db_session, run_id, sequence, status="completed"):
    await _journal(
        db_session, run_id, sequence, runtime_types.JOURNAL_COMPLETION,
        {"status": status},
    )


async def _invocation(
    db_session, run_id: UUID, call_id: str, tool: str, **overrides
) -> AgentToolInvocation:
    overrides.setdefault("state", "completed")
    overrides.setdefault("arguments", {})
    invocation = AgentToolInvocation(
        operation_id=uuid4().hex,
        run_id=run_id,
        provider_tool_call_id=call_id,
        tool_name=tool,
        idempotency_key=f"{run_id}:{call_id}",
        **overrides,
    )
    db_session.add(invocation)
    await db_session.flush()
    return invocation


async def _usage(db_session, run_id: UUID, **overrides) -> AIUsage:
    overrides.setdefault("provider", "openai")
    overrides.setdefault("model", "m")
    overrides.setdefault("input_tokens", 100)
    overrides.setdefault("output_tokens", 50)
    overrides.setdefault("sequence", 1)
    row = AIUsage(agent_run_id=run_id, **overrides)
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_complete_run(db_session, org_id: UUID) -> UUID:
    """Terminal single run with intact journal, ledger, usage, latency."""
    root_id = uuid4()
    started = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    await _add_run(
        db_session,
        id=root_id,
        org_id=org_id,
        status="completed",
        output={"answer": "done"},
        started_at=started,
        completed_at=started + timedelta(seconds=5),
        iterations_used=3,
    )
    await _journal(db_session, root_id, 1, "model_request", {"model": "m"})
    await _tool_call(db_session, root_id, 2, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 3)
    await _invocation(db_session, root_id, "call-1", "get_ticket", arguments={"id": "t-1"})
    await _usage(
        db_session, root_id, cost=Decimal("0.002"), duration_ms=4000,
    )
    return root_id


async def _load(db_session, run_id: UUID, user: UserPrincipal) -> dict:
    return await load_recorded_run_evidence(db_session, run_id, user=user)


async def test_principal_is_mandatory(db_session):
    with pytest.raises(TypeError, match="UserPrincipal"):
        await load_recorded_run_evidence(db_session, uuid4(), user=None)
    with pytest.raises(TypeError, match="UserPrincipal"):
        await load_recorded_run_evidence(db_session, uuid4(), user=uuid4())


async def test_missing_and_hidden_runs_share_not_found(db_session):
    org_a = await _make_org(db_session, "a")
    org_b = await _make_org(db_session, "b")
    root_id = await _add_run(db_session, org_id=org_a)
    missing_error = None
    hidden_error = None
    with pytest.raises(RecordedEvidenceNotFound) as exc:
        await _load(db_session, uuid4(), _user(org_a))
    missing_error = str(exc.value)
    with pytest.raises(RecordedEvidenceNotFound) as exc:
        await _load(db_session, root_id, _user(org_b))
    hidden_error = str(exc.value)
    assert missing_error == hidden_error
    assert str(root_id) not in hidden_error


async def test_org_none_isolation(db_session):
    org = await _make_org(db_session, "none")
    run_id = await _add_run(db_session, org_id=None)
    with pytest.raises(RecordedEvidenceNotFound):
        await _load(db_session, run_id, _user(org))
    with pytest.raises(RecordedEvidenceNotFound):
        await _load(db_session, run_id, _user(None))


async def test_private_chat_delegation_denied(db_session):
    org = await _make_org(db_session, "chat")
    owner = uuid4()
    run_id = await _add_run(
        db_session,
        org_id=org,
        trigger_type="delegation",
        parent_run_id=None,
        root_run_id=None,
        caller_user_id=str(owner),
    )
    db_session.expire_all()
    with pytest.raises(RecordedEvidenceNotFound):
        await _load(db_session, run_id, _user(org))
    result = await _load(
        db_session, run_id, UserPrincipal(
            user_id=owner, email="o@example.com", organization_id=org
        ),
    )
    assert result["evidence"]["terminal_status"] == "completed"


async def test_full_terminal_single_run_trace(db_session):
    org = await _make_org(db_session, "full")
    root_id = await _seed_complete_run(db_session, org)
    result = await _load(db_session, root_id, _user(org))

    assert set(result) == {"evidence", "completeness", "evidence_refs", "limitations"}
    assert set(result["completeness"]) == set(_COMPLETENESS_KEYS)
    for key, complete in result["completeness"].items():
        if key in {"usage.tokens", "usage.cost_usd"}:
            assert complete is False
        else:
            assert complete is True, key
    assert any("observational" in item for item in result["limitations"])

    evidence = result["evidence"]
    assert evidence["terminal_status"] == "completed"
    assert evidence["output"] == {"answer": "done"}
    assert [c["name"] for c in evidence["tool_calls"]] == ["get_ticket"]
    call = evidence["tool_calls"][0]
    assert call["arguments"] == {"id": "t-1"}
    assert call["sequence"] == 2
    assert call["journal_references"][0]["run_id"] == str(root_id)
    assert evidence["delegation"] == {"children": []}
    assert evidence["real_tool_executions"] == 0 + 1
    assert evidence["usage"]["iterations"] == 3
    assert evidence["usage"]["tokens"] == 150
    assert evidence["usage"]["cost_usd"] == pytest.approx(0.002)
    assert evidence["usage"]["latency_ms"] == 5000
    assert "simulator_state" not in evidence

    refs = result["evidence_refs"]
    assert len(refs) == 1
    assert refs[0]["run_id"] == str(root_id)
    assert refs[0]["sequence"] == 2
    assert refs[0]["kind"] == "tool_call"
    assert refs[0]["operation_id"]


async def test_explicit_empty_complete_history(db_session):
    org = await _make_org(db_session, "empty")
    root_id = await _add_run(db_session, org_id=org, output={})
    await _completion(db_session, root_id, 1)
    result = await _load(db_session, root_id, _user(org))

    assert result["evidence"]["tool_calls"] == []
    assert result["completeness"]["tool_calls"] is True
    assert result["completeness"]["tool_order"] is True
    assert result["completeness"]["tool_arguments"] is True
    assert result["evidence"]["real_tool_executions"] == 0
    assert result["completeness"]["real_tool_executions"] is True
    assert result["completeness"]["terminal_status"] is True
    assert result["completeness"]["output"] is True
    assert result["completeness"]["delegation"] is True


async def test_terminal_row_without_journal_cannot_pass_forbidden(db_session):
    org = await _make_org(db_session, "noj")
    root_id = await _add_run(db_session, org_id=org, output={"answer": "x"})
    result = await _load(db_session, root_id, _user(org))

    assert result["completeness"]["tool_calls"] is False
    assert result["completeness"]["output"] is False
    outcomes = evaluate_recorded_assertions(
        [{"type": "forbidden_tool", "params": {"tool": "delete_ticket"}}],
        result["evidence"],
        completeness=result["completeness"],
        applicability="applicable",
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


async def test_legacy_steps_only_stay_incomplete(db_session):
    org = await _make_org(db_session, "legacy")
    root_id = await _add_run(db_session, org_id=org, output={"answer": "x"})
    db_session.add(
        AgentRunStep(run_id=root_id, step_number=1, type="tool_call", content={})
    )
    await db_session.flush()
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert any("legacy steps" in item for item in result["limitations"])


async def test_missing_completion_stays_incomplete(db_session):
    org = await _make_org(db_session, "nocomp")
    root_id = await _add_run(db_session, org_id=org, output={"answer": "x"})
    await _journal(db_session, root_id, 1, "model_request", {})
    await _tool_call(db_session, root_id, 2, "get_ticket", "call-1", {"id": "t-1"})
    await _invocation(db_session, root_id, "call-1", "get_ticket")
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert result["completeness"]["output"] is False
    assert any("completion" in item for item in result["limitations"])


async def test_inconsistent_completion_stays_incomplete(db_session):
    org = await _make_org(db_session, "badcomp")
    root_id = await _add_run(db_session, org_id=org, status="completed")
    await _completion(db_session, root_id, 1, status="failed")
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert any("inconsistent" in item for item in result["limitations"])


async def test_completion_without_status_stays_incomplete(db_session):
    org = await _make_org(db_session, "nostatus")
    root_id = await _add_run(db_session, org_id=org, status="completed")
    await _journal(db_session, root_id, 1, runtime_types.JOURNAL_COMPLETION, {})
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert result["completeness"]["output"] is False
    assert any("inconsistent" in item for item in result["limitations"])


async def test_nonterminal_child_marks_tree_incomplete(db_session):
    org = await _make_org(db_session, "ntchild")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    child_id = await _add_run(
        db_session, org_id=org, parent_run_id=root_id,
        root_run_id=root_id, status="running",
    )
    await _completion(db_session, child_id, 1, status="running")
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["delegation"] is False
    assert result["completeness"]["tool_calls"] is False
    assert result["completeness"]["usage.iterations"] is False


async def test_delegation_cycle_marks_projection_incomplete(db_session):
    org = await _make_org(db_session, "cycle")
    root_id = await _add_run(db_session, org_id=org, status="completed")
    run = await db_session.get(AgentRun, root_id)
    assert run is not None
    run.parent_run_id = root_id
    run.root_run_id = root_id
    await _completion(db_session, root_id, 1)
    await db_session.flush()

    result = await _load(db_session, root_id, _user(org))

    assert result["completeness"]["delegation"] is False
    assert result["completeness"]["tool_calls"] is False
    assert result["completeness"]["usage.iterations"] is False
    assert any("cycle" in item for item in result["limitations"])


async def test_hidden_child_withheld_without_content(db_session):
    org_a = await _make_org(db_session, "hidea")
    org_b = await _make_org(db_session, "hideb")
    root_id = await _add_run(db_session, org_id=org_a)
    await _completion(db_session, root_id, 1)
    hidden_id = await _add_run(
        db_session, org_id=org_b, parent_run_id=root_id, root_run_id=root_id,
    )
    await _journal(
        db_session, hidden_id, 1, runtime_types.JOURNAL_TOOL_CALL,
        {"tool_name": "hidden_tool", "tool_call_id": "h-1",
         "arguments": {"secret": "hidden-content"}},
    )
    await _completion(db_session, hidden_id, 2)
    grandchild_id = await _add_run(
        db_session, org_id=org_a, parent_run_id=hidden_id, root_run_id=root_id,
    )
    await _completion(db_session, grandchild_id, 1)

    result = await _load(db_session, root_id, _user(org_a))
    assert result["completeness"]["delegation"] is False
    assert result["completeness"]["tool_calls"] is False
    assert result["evidence"]["delegation"] == {"children": []}
    assert result["evidence"]["tool_calls"] == []
    blob = str(result)
    assert str(hidden_id) not in blob
    assert str(grandchild_id) not in blob
    assert "hidden-content" not in blob
    assert any("not visible" in item for item in result["limitations"])


async def test_many_hidden_children_do_not_raise_hidden_size_overflow(
    db_session, monkeypatch
):
    monkeypatch.setattr(evidence_module, "MAX_RECORDED_TREE_RUNS", 2)
    org_a = await _make_org(db_session, "manyhidea")
    org_b = await _make_org(db_session, "manyhideb")
    root_id = await _add_run(db_session, org_id=org_a)
    await _completion(db_session, root_id, 1)
    hidden_ids = [
        await _add_run(
            db_session,
            org_id=org_b,
            parent_run_id=root_id,
            root_run_id=root_id,
        )
        for _ in range(4)
    ]

    result = await _load(db_session, root_id, _user(org_a))

    assert result["completeness"]["delegation"] is False
    assert result["completeness"]["tool_calls"] is False
    assert result["evidence"]["delegation"] == {"children": []}
    blob = str(result)
    for hidden_id in hidden_ids:
        assert str(hidden_id) not in blob
    assert any("not visible" in item for item in result["limitations"])


async def test_null_org_hidden_child_marks_tree_incomplete(db_session):
    org = await _make_org(db_session, "nullhidden")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    hidden_id = await _add_run(
        db_session,
        org_id=None,
        parent_run_id=root_id,
        root_run_id=root_id,
    )

    result = await _load(db_session, root_id, _user(org))

    assert result["completeness"]["delegation"] is False
    assert result["completeness"]["tool_calls"] is False
    assert result["evidence"]["delegation"] == {"children": []}
    assert str(hidden_id) not in str(result)
    assert any("not visible" in item for item in result["limitations"])


async def test_selected_child_does_not_expand(db_session):
    org = await _make_org(db_session, "child")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    child_id = await _add_run(
        db_session, org_id=org, parent_run_id=root_id, root_run_id=root_id,
    )
    await _tool_call(db_session, child_id, 1, "read", "c-1", {})
    await _completion(db_session, child_id, 2)
    await _invocation(db_session, child_id, "c-1", "read")
    sibling_id = await _add_run(
        db_session, org_id=org, parent_run_id=root_id, root_run_id=root_id,
    )
    await _completion(db_session, sibling_id, 1)

    result = await _load(db_session, child_id, _user(org))
    assert result["evidence"]["delegation"] == {"children": []}
    assert [c["name"] for c in result["evidence"]["tool_calls"]] == ["read"]
    assert str(root_id) not in str(result["evidence_refs"])
    assert str(sibling_id) not in str(result)


async def test_repeated_observation_counts_once(db_session):
    org = await _make_org(db_session, "dupe")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _tool_call(db_session, root_id, 2, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 3)
    await _invocation(db_session, root_id, "call-1", "get_ticket", arguments={"id": "t-1"})
    result = await _load(db_session, root_id, _user(org))
    assert [c["sequence"] for c in result["evidence"]["tool_calls"]] == [1]
    assert result["evidence"]["real_tool_executions"] == 1
    assert result["completeness"]["tool_calls"] is True


async def test_conflicting_observations_stay_incomplete(db_session):
    org = await _make_org(db_session, "confobs")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _tool_call(db_session, root_id, 2, "get_ticket", "call-1", {"id": "other"})
    await _completion(db_session, root_id, 3)
    await _invocation(db_session, root_id, "call-1", "get_ticket", arguments={"id": "t-1"})
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert any("conflicting" in item for item in result["limitations"])


async def test_distinct_repeated_calls_preserved(db_session):
    org = await _make_org(db_session, "rep")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "a"})
    await _tool_call(db_session, root_id, 2, "get_ticket", "call-2", {"id": "b"})
    await _completion(db_session, root_id, 3)
    await _invocation(db_session, root_id, "call-1", "get_ticket", arguments={"id": "a"})
    await _invocation(db_session, root_id, "call-2", "get_ticket", arguments={"id": "b"})
    result = await _load(db_session, root_id, _user(org))
    assert len(result["evidence"]["tool_calls"]) == 2
    assert result["evidence"]["real_tool_executions"] == 2
    assert result["completeness"]["tool_calls"] is True


async def test_identity_conflict_stays_incomplete(db_session):
    org = await _make_org(db_session, "idconf")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 2)
    await _invocation(db_session, root_id, "call-1", "other_tool")
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert any("identity" in item for item in result["limitations"])


async def test_arguments_conflict_stays_incomplete(db_session):
    org = await _make_org(db_session, "argconf")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 2)
    await _invocation(db_session, root_id, "call-1", "get_ticket", arguments={"id": "zz"})
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert any("conflicting" in item for item in result["limitations"])


async def test_unmatched_journal_call_stays_incomplete(db_session):
    org = await _make_org(db_session, "unmatch")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 2)
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert any("no matching dispatch" in item for item in result["limitations"])


async def test_ledger_without_journal_stays_incomplete(db_session):
    org = await _make_org(db_session, "ledonly")
    root_id = await _add_run(db_session, org_id=org)
    await _journal(db_session, root_id, 1, "model_request", {})
    await _completion(db_session, root_id, 2)
    await _invocation(db_session, root_id, "call-9", "get_ticket")
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert result["evidence"]["tool_calls"] == []
    assert any("no matching journal" in item for item in result["limitations"])


async def test_in_flight_operation_stays_incomplete(db_session):
    org = await _make_org(db_session, "flight")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 2)
    await _invocation(db_session, root_id, "call-1", "get_ticket", state="planned")
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is False
    assert result["completeness"]["real_tool_executions"] is False
    assert "real_tool_executions" not in result["evidence"]
    assert any("in flight" in item for item in result["limitations"])


async def test_uncertain_operation_observed_but_incomplete(db_session):
    org = await _make_org(db_session, "unc")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 2)
    await _invocation(db_session, root_id, "call-1", "get_ticket", state="uncertain")
    result = await _load(db_session, root_id, _user(org))
    assert [c["name"] for c in result["evidence"]["tool_calls"]] == ["get_ticket"]
    assert result["completeness"]["tool_calls"] is False
    assert result["completeness"]["real_tool_executions"] is False
    assert any("uncertain" in item for item in result["limitations"])


async def test_failed_ledger_counts_dispatch_not_success(db_session):
    org = await _make_org(db_session, "failed")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "get_ticket", "call-1", {"id": "t-1"})
    await _completion(db_session, root_id, 2)
    await _invocation(
        db_session, root_id, "call-1", "get_ticket",
        state="failed", arguments={"id": "t-1"},
    )
    result = await _load(db_session, root_id, _user(org))
    assert result["evidence"]["real_tool_executions"] == 1
    assert result["completeness"]["real_tool_executions"] is True
    assert result["completeness"]["tool_calls"] is True


async def test_redacted_ledger_marker_loses_arguments(db_session):
    org = await _make_org(db_session, "redled")
    root_id = await _add_run(db_session, org_id=org)
    await _tool_call(db_session, root_id, 1, "lookup", "call-1", {"q": "x"})
    await _completion(db_session, root_id, 2)
    await _invocation(
        db_session, root_id, "call-1", "lookup", arguments={"q": "[REDACTED]"}
    )
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is True
    assert result["completeness"]["tool_arguments"] is False
    assert any("redacted" in item for item in result["limitations"])


async def test_sensitive_key_redaction_loses_arguments(db_session):
    org = await _make_org(db_session, "redkey")
    root_id = await _add_run(db_session, org_id=org)
    await _journal(
        db_session, root_id, 1, runtime_types.JOURNAL_TOOL_CALL,
        {"tool_name": "sleep_until", "tool_call_id": "timer-1",
         "arguments": {"api_key": "live-secret", "seconds": 5}},
    )
    await _completion(db_session, root_id, 2)
    result = await _load(db_session, root_id, _user(org))
    assert result["evidence"]["tool_calls"][0]["arguments"] == {
        "api_key": "[REDACTED]",
        "seconds": 5,
    }
    assert "live-secret" not in str(result)
    assert result["completeness"]["tool_calls"] is True
    assert result["completeness"]["tool_arguments"] is False


async def test_parallel_children_lose_order_only(db_session):
    org = await _make_org(db_session, "par")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    for index in range(2):
        child_id = await _add_run(
            db_session, org_id=org, parent_run_id=root_id, root_run_id=root_id,
        )
        await _tool_call(db_session, child_id, 1, "read", f"c-{index}", {})
        await _completion(db_session, child_id, 2)
        await _invocation(db_session, child_id, f"c-{index}", "read")
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["tool_calls"] is True
    assert result["completeness"]["tool_order"] is False
    assert len(result["evidence"]["tool_calls"]) == 2
    assert any("causal order" in item for item in result["limitations"])


async def test_seq0_usage_excluded(db_session):
    org = await _make_org(db_session, "seq0")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    await _usage(
        db_session, root_id, input_tokens=1000, output_tokens=500, sequence=0,
        cost=Decimal("9.99"),
    )
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["usage.tokens"] is False
    assert result["completeness"]["usage.cost_usd"] is False
    assert "tokens" not in result["evidence"].get("usage", {})
    assert any("zero usage is not assumed" in item for item in result["limitations"])

    await _usage(db_session, root_id, input_tokens=100, output_tokens=50, sequence=2)
    result = await _load(db_session, root_id, _user(org))
    assert result["evidence"]["usage"]["tokens"] == 150
    assert result["completeness"]["usage.tokens"] is False
    assert any("observational" in item for item in result["limitations"])


async def test_partial_cost_stays_incomplete(db_session):
    org = await _make_org(db_session, " Axiom")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    await _usage(db_session, root_id, cost=Decimal("0.002"))
    await _usage(db_session, root_id, input_tokens=10, output_tokens=5)
    result = await _load(db_session, root_id, _user(org))
    assert result["evidence"]["usage"]["tokens"] == 165
    assert result["completeness"]["usage.tokens"] is False
    assert result["completeness"]["usage.cost_usd"] is False
    assert "cost_usd" not in result["evidence"].get("usage", {})
    assert any("partial" in item for item in result["limitations"])


async def test_negative_cost_rejected(db_session):
    org = await _make_org(db_session, "negcost")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    await _usage(db_session, root_id, cost=Decimal("-0.5"))
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["usage.cost_usd"] is False
    assert "cost_usd" not in result["evidence"].get("usage", {})


async def test_negative_latency_not_clamped(db_session):
    org = await _make_org(db_session, "neglat")
    started = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    root_id = await _add_run(
        db_session, org_id=org, started_at=started,
        completed_at=started - timedelta(seconds=3),
    )
    await _completion(db_session, root_id, 1)
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["usage.latency_ms"] is False
    assert "latency_ms" not in result["evidence"].get("usage", {})
    assert any("not usable" in item for item in result["limitations"])


async def test_negative_iterations_not_complete(db_session):
    org = await _make_org(db_session, "negiter")
    root_id = await _add_run(db_session, org_id=org, iterations_used=-1)
    await _completion(db_session, root_id, 1)
    result = await _load(db_session, root_id, _user(org))
    assert result["completeness"]["usage.iterations"] is False
    assert "iterations" not in result["evidence"].get("usage", {})
    assert any("iteration counters are not usable" in item for item in result["limitations"])


async def test_explicit_null_output_is_recorded(db_session):
    org = await _make_org(db_session, "nullout")
    root_id = await _add_run(db_session, org_id=org, output=None)
    await _completion(db_session, root_id, 1)
    result = await _load(db_session, root_id, _user(org))
    assert "output" in result["evidence"]
    assert result["evidence"]["output"] is None
    assert result["completeness"]["output"] is True


async def test_overflow_raises_instead_of_truncating(db_session, monkeypatch):
    monkeypatch.setattr(evidence_module, "MAX_RECORDED_TREE_RUNS", 1)
    org = await _make_org(db_session, "over")
    root_id = await _add_run(db_session, org_id=org)
    await _completion(db_session, root_id, 1)
    await _add_run(db_session, org_id=org, parent_run_id=root_id, root_run_id=root_id)
    with pytest.raises(RecordedEvidenceOversized, match="exceeds"):
        await _load(db_session, root_id, _user(org))


async def test_journal_overflow_raises(db_session, monkeypatch):
    monkeypatch.setattr(evidence_module, "MAX_RECORDED_JOURNAL_ENTRIES", 1)
    org = await _make_org(db_session, "jover")
    root_id = await _add_run(db_session, org_id=org)
    await _journal(db_session, root_id, 1, "model_request", {})
    await _completion(db_session, root_id, 2)
    with pytest.raises(RecordedEvidenceOversized, match="journal"):
        await _load(db_session, root_id, _user(org))


async def test_loader_performs_no_writes(db_session):
    org = await _make_org(db_session, "nowrite")
    root_id = await _seed_complete_run(db_session, org)

    async def counts():
        values = []
        for model in (AgentRun, AgentRunJournalEntry, AgentToolInvocation, AIUsage):
            values.append(
                (
                    await db_session.execute(
                        select(func.count()).select_from(model)
                    )
                ).scalar()
            )
        return tuple(values)

    before = await counts()
    await _load(db_session, root_id, _user(org))
    assert await counts() == before


async def test_completeness_keys_match_a1_contract(db_session):
    org = await _make_org(db_session, "keys")
    root_id = await _seed_complete_run(db_session, org)
    result = await _load(db_session, root_id, _user(org))
    assert set(result["completeness"]) == set(_COMPLETENESS_KEYS)
    outcomes = evaluate_recorded_assertions(
        [
            {"type": "terminal_status", "params": {"status": "completed"}},
            {"type": "tool_called", "params": {"tool": "get_ticket"}},
            {"type": "no_real_tools", "params": {}},
        ],
        result["evidence"],
        completeness=result["completeness"],
        applicability="applicable",
    )
    assert [o["outcome"] for o in outcomes] == ["passed", "passed", "failed"]
