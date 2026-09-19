"""Model tests for the durable agent runtime persistence (Task 2).

Pure-Python ORM assertions: defaults, uniqueness, indexes, parent/root
relationships, and JSON round-trips. No live database required.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJoin,
    AgentRunJoinMember,
    AgentRunJournalEntry,
    AgentToolInvocation,
)


def _unique_names(model) -> set[str]:
    return {
        c.name
        for c in model.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }


def _index_names(model) -> set[str]:
    return {i.name for i in model.__table__.indexes}


def test_agent_run_durable_defaults():
    run = AgentRun(trigger_type="event", status="queued")
    columns = AgentRun.__table__.c
    assert columns["checkpoint_sequence"].default.arg == 0
    assert columns["attempt"].default.arg == 0
    assert columns["completion_event_attempts"].default.arg == 0
    assert columns["checkpoint_sequence"].server_default is not None
    assert columns["attempt"].server_default is not None
    assert columns["completion_event_attempts"].server_default is not None
    assert run.root_run_id is None
    assert run.execution_snapshot is None
    assert run.caller_context is None
    assert run.caller_auth_context is None
    assert run.lease_owner is None
    assert run.lease_token is None
    assert run.lease_expires_at is None
    assert run.last_progress_at is None
    assert run.wake_at is None
    assert run.correlation is None
    assert run.completion_event_pending_at is None
    assert run.completion_event_emitted_at is None
    assert run.completion_event_last_error is None


def test_agent_run_durable_indexes():
    names = _index_names(AgentRun)
    assert "ix_agent_runs_root_run_id" in names
    assert "ix_agent_runs_wake_at" in names
    assert "ix_agent_runs_lease_expires_at" in names
    assert "ix_agent_runs_completion_pending" in names


def test_agent_run_parent_root_relationships():
    parent_id = uuid4()
    parent = AgentRun(id=parent_id, trigger_type="event", status="running")
    parent.root_run_id = parent_id
    child = AgentRun(trigger_type="delegation", status="queued")
    child.parent_run_id = parent_id
    child.root_run_id = parent_id
    child.parent_run = parent
    assert child.parent_run is parent
    assert child.root_run_id == child.parent_run_id


def test_agent_run_snapshot_and_correlation_json_round_trip():
    run = AgentRun(trigger_type="event", status="queued")
    run.execution_snapshot = {
        "format_version": 1,
        "prompt": "do things",
        "model": {"provider": "openai", "model": "gpt-5"},
        "tools": [{"name": "lookup", "version": "3"}],
        "limits": {"max_iterations": 25, "max_tokens": 8000},
    }
    run.correlation = {"ticket_id": "7", "kind": "ticket"}
    run.caller_context = {"ticket_id": 7}
    run.caller_auth_context = {
        "user_id": str(uuid4()),
        "is_provider_org": True,
        "roles": ["Support"],
    }
    assert run.execution_snapshot["tools"][0]["name"] == "lookup"
    assert run.correlation["ticket_id"] == "7"
    assert run.caller_context["ticket_id"] == 7
    assert run.caller_auth_context["is_provider_org"] is True


def test_checkpoint_uniqueness_and_defaults():
    run_id = uuid4()
    first = AgentRunCheckpoint(run_id=run_id, sequence=0, state={"messages": []})
    assert AgentRunCheckpoint.__table__.c["format_version"].default.arg == 1
    assert AgentRunCheckpoint.__table__.c["attempt"].default.arg == 0
    assert "uq_agent_run_checkpoints_run_sequence" in _unique_names(
        AgentRunCheckpoint
    )
    assert "ix_agent_run_checkpoints_run_id" in _index_names(AgentRunCheckpoint)
    assert first.state == {"messages": []}


def test_journal_uniqueness_and_kinds():
    run_id = uuid4()
    entry = AgentRunJournalEntry(
        run_id=run_id,
        sequence=0,
        kind="model_response",
        data={"tool_calls": []},
        provider_invocation_id="inv-1",
        checkpoint_sequence=0,
    )
    assert entry.data == {"tool_calls": []}
    assert "uq_agent_run_journal_run_sequence" in _unique_names(
        AgentRunJournalEntry
    )
    assert "ix_agent_run_journal_run_kind" in _index_names(AgentRunJournalEntry)


def test_tool_invocation_states_and_identity():
    invocation = AgentToolInvocation(
        operation_id="op-1",
        run_id=uuid4(),
        provider_tool_call_id="call-1",
        tool_name="lookup",
        tool_version="3",
        tool_schema={"type": "object"},
        arguments={"q": "x"},
        idempotency_key="idem-1",
    )
    assert AgentToolInvocation.__table__.c["state"].default.arg == "planned"
    assert "uq_agent_tool_invocations_run_tool_call" in _unique_names(
        AgentToolInvocation
    )
    invocation.state = "uncertain"
    invocation.reconciliation = {"hook": "none", "reason": "lease expired"}
    assert invocation.reconciliation["hook"] == "none"


def test_join_mode_constrained_and_parent_tool_call_unique():
    AgentRunJoin(parent_run_id=uuid4(), provider_tool_call_id="call-fanout-1")
    assert AgentRunJoin.__table__.c["mode"].default.arg == "all"
    assert AgentRunJoin.__table__.c["status"].default.arg == "pending"
    assert "uq_agent_run_joins_parent_tool_call" in _unique_names(AgentRunJoin)
    AgentRunJoinMember(join_id=uuid4(), child_run_id=uuid4(), position=0)
    assert AgentRunJoinMember.__table__.c["status"].default.arg == "pending"
    assert "uq_agent_run_join_members_join_child" in _unique_names(
        AgentRunJoinMember
    )


MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "20260918_durable_agent_runtime.py"
)
CALLER_AUTH_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "20260919_runtime_caller_auth_context.py"
)


def _load_migration():
    assert MIGRATION_PATH.exists(), (
        "expected migration api/alembic/versions/20260918_durable_agent_runtime.py"
    )
    spec = importlib.util.spec_from_file_location(
        "durable_agent_runtime_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingOp:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return record


def test_migration_revision_chain():
    module = _load_migration()
    assert module.revision == "20260918_durable_agent_runtime"
    assert module.down_revision == "20260918_merge_plat_prof_heads"


def test_caller_auth_context_migration_follows_current_head():
    assert CALLER_AUTH_MIGRATION_PATH.exists()
    spec = importlib.util.spec_from_file_location(
        "runtime_caller_auth_migration", CALLER_AUTH_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "20260919_runtime_caller_auth"
    assert module.down_revision == "20260919_eval_hardening"


def test_migration_creates_expected_tables_and_backfills():
    module = _load_migration()
    recorder = _RecordingOp()
    module.op = recorder
    module.upgrade()
    created_tables = {
        args[0] for name, args, _ in recorder.calls if name == "create_table"
    }
    assert created_tables == {
        "agent_run_checkpoints",
        "agent_run_journal_entries",
        "agent_tool_invocations",
        "agent_run_joins",
        "agent_run_join_members",
    }
    added_columns = {
        args[1].name
        for name, args, _ in recorder.calls
        if name == "add_column" and args[0] == "agent_runs"
    }
    assert {
        "root_run_id",
        "execution_snapshot",
        "caller_context",
        "checkpoint_sequence",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "last_progress_at",
        "attempt",
        "wake_at",
        "correlation",
        "completion_event_pending_at",
        "completion_event_emitted_at",
        "completion_event_attempts",
        "completion_event_last_error",
    } <= added_columns
    executed_sql = " ".join(
        str(args[0]) for name, args, _ in recorder.calls if name == "execute"
    )
    assert "root_run_id = id" in executed_sql
