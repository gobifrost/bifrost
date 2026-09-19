"""Bounded, durable evidence projection for synthetic evaluation results.

The scheduler scores Studio results after an AgentRun has terminalized.  It
must not reconstruct a partial view from process-local executor state: the
simulation session, tool records, AgentRun tree, journal references, and
AIUsage rows are the durable source of truth.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.services.agent_evaluations import quotas
from src.services.agent_evaluations.simulator_models import redact_value

MAX_EVIDENCE_TREE_RUNS = 128
MAX_EVIDENCE_JOURNAL_ENTRIES = 4_000
MAX_EVIDENCE_USAGE_ROWS = 4_000


class EvaluationEvidenceError(Exception):
    """The durable rows cannot safely produce an assertion input."""


async def load_persisted_evaluation_evidence(session, run_id: UUID) -> dict[str, Any]:
    """Project one synthetic root run and its descendants into assertion input.

    Every collection is independently bounded.  Refusing an oversized or
    malformed projection is safer than silently dropping calls/children and
    producing a false assertion verdict.
    """
    from src.models.orm.agent_evaluations import (
        AgentSimulationSession,
        AgentSimulationToolRecord,
    )
    from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
    from src.models.orm.ai_usage import AIUsage

    run = await session.get(AgentRun, run_id)
    if run is None:
        raise EvaluationEvidenceError(f"Synthetic run {run_id} no longer exists.")
    root_id = run.root_run_id or run.id
    run_rows = list(
        (
            await session.execute(
                select(AgentRun)
                .options(selectinload(AgentRun.agent))
                .where((AgentRun.id == root_id) | (AgentRun.root_run_id == root_id))
                .order_by(AgentRun.created_at, AgentRun.id)
                .limit(MAX_EVIDENCE_TREE_RUNS + 1)
            )
        ).scalars().all()
    )
    if len(run_rows) > MAX_EVIDENCE_TREE_RUNS:
        raise EvaluationEvidenceError(
            f"Synthetic run tree exceeds {MAX_EVIDENCE_TREE_RUNS} nodes."
        )
    by_id = {row.id: row for row in run_rows}
    root = by_id.get(root_id)
    if root is None:
        raise EvaluationEvidenceError("Synthetic root run is missing from its tree.")

    journal_rows = list(
        (
            await session.execute(
                select(AgentRunJournalEntry)
                .where(AgentRunJournalEntry.run_id.in_(list(by_id)))
                .order_by(AgentRunJournalEntry.run_id, AgentRunJournalEntry.sequence)
                .limit(MAX_EVIDENCE_JOURNAL_ENTRIES + 1)
            )
        ).scalars().all()
    )
    if len(journal_rows) > MAX_EVIDENCE_JOURNAL_ENTRIES:
        raise EvaluationEvidenceError(
            f"Synthetic journal exceeds {MAX_EVIDENCE_JOURNAL_ENTRIES} entries."
        )
    journal_references = _journal_references(journal_rows)

    simulation = await session.scalar(
        select(AgentSimulationSession).where(
            AgentSimulationSession.root_run_id == root_id
        )
    )
    if simulation is None:
        raise EvaluationEvidenceError("Synthetic simulation session is missing.")
    tool_records = list(
        (
            await session.execute(
                select(AgentSimulationToolRecord)
                .where(AgentSimulationToolRecord.session_id == simulation.id)
                .order_by(AgentSimulationToolRecord.sequence)
                .limit(quotas.MAX_SIM_RECORDS_PER_RUN + 1)
            )
        ).scalars().all()
    )
    if len(tool_records) > quotas.MAX_SIM_RECORDS_PER_RUN:
        raise EvaluationEvidenceError(
            f"Synthetic tool records exceed {quotas.MAX_SIM_RECORDS_PER_RUN}."
        )

    usage_rows = list(
        (
            await session.execute(
                select(AIUsage)
                .where(
                    AIUsage.agent_run_id.in_(list(by_id)),
                    # Sequence zero is reserved for post-run summarization,
                    # which contributes to agent spend but is not runtime
                    # evidence for an evaluation assertion.
                    AIUsage.sequence > 0,
                )
                .order_by(AIUsage.id)
                .limit(MAX_EVIDENCE_USAGE_ROWS + 1)
            )
        ).scalars().all()
    )
    if len(usage_rows) > MAX_EVIDENCE_USAGE_ROWS:
        raise EvaluationEvidenceError(
            f"Synthetic usage exceeds {MAX_EVIDENCE_USAGE_ROWS} records."
        )
    tool_journal_references = _tool_journal_references(journal_rows)
    simulator_tool_calls = [
        {
            "name": record.tool_name,
            "arguments": redact_value(record.arguments or {}),
            # This is the append-only simulator sequence, not an
            # ephemeral process-local counter. It is unique per shared
            # root session, including delegated synthetic children.
            "sequence": record.sequence,
            "operation_id": record.operation_id,
            "journal_references": list(
                tool_journal_references.get(
                    _operation_journal_key(record.operation_id), []
                )
            ),
        }
        for record in tool_records
    ]
    engine_tool_calls = _engine_tool_calls(
        journal_rows, tool_journal_references
    )
    tool_calls = [*simulator_tool_calls, *engine_tool_calls]
    # A simulation record sequence is shared across children while journal
    # sequence is local to a run. Composite journal references give every
    # projected item a stable cross-tree order without inventing a global
    # process-local counter.
    tool_calls.sort(key=_tool_call_order_key)
    evidence = {
        "terminal_status": root.status,
        "output": redact_value(root.output),
        "malformed_output": root.contract_valid is False,
        "tool_calls": tool_calls,
        "simulator_state": redact_value(simulation.state or {}),
        "simulator_state_hash": simulation.final_state_hash,
        "delegation": _delegation_evidence(root_id, by_id, journal_references),
        "usage": _usage_evidence(root, run_rows, usage_rows),
        # Synthetic non-engine calls can only have reached the persisted
        # simulator records above. Delegation/timers remain engine-owned.
        "real_tool_executions": 0,
        "output_schema": root.output_schema,
        # A sequence is only unique within one run. Keep the legacy scalar
        # list for simple single-run consumers, and use these composite
        # references for audit links across delegated descendants.
        "journal_sequences": [entry.sequence for entry in journal_rows],
        "journal_references": [_journal_reference(entry) for entry in journal_rows],
    }
    try:
        quotas.check_evidence_size(evidence)
    except quotas.QuotaExceeded as exc:
        raise EvaluationEvidenceError(str(exc)) from exc
    return evidence


def _journal_reference(entry) -> dict[str, Any]:
    return {
        "run_id": str(entry.run_id),
        "sequence": entry.sequence,
        "kind": entry.kind,
    }


def _journal_references(entries: list) -> dict[UUID, list[dict[str, Any]]]:
    """Map a delegated child to its parent's durable intent sequence(s)."""
    references: dict[UUID, list[dict[str, Any]]] = {}
    for entry in entries:
        for child_id in _referenced_child_ids(entry.data or {}):
            references.setdefault(child_id, []).append(_journal_reference(entry))
    return references


def _referenced_child_ids(data: dict[str, Any]) -> set[UUID]:
    """Read both single-delegation and fan-out journal payload shapes."""
    candidates = [data.get("child_run_id"), *(data.get("child_run_ids") or [])]
    candidates.extend(
        item.get("child_run_id")
        for item in data.get("children", [])
        if isinstance(item, dict)
    )
    child_ids: set[UUID] = set()
    for child_id in candidates:
        try:
            child_ids.add(UUID(str(child_id)))
        except (TypeError, ValueError):
            continue
    return child_ids


def _operation_journal_key(operation_id: str | None) -> tuple[UUID, str] | None:
    """Recover the durable ``(run_id, provider_tool_call_id)`` operation key."""
    if not isinstance(operation_id, str):
        return None
    run_text, separator, provider_tool_call_id = operation_id.partition(":")
    if not separator or not provider_tool_call_id:
        return None
    try:
        return UUID(run_text), provider_tool_call_id
    except ValueError:
        return None


def _tool_journal_references(
    entries: list,
) -> dict[tuple[UUID, str], list[dict[str, Any]]]:
    """Join synthetic operations to the checkpoint journal's tool-call ID."""
    references: dict[tuple[UUID, str], list[dict[str, Any]]] = {}
    for entry in entries:
        tool_call_id = (entry.data or {}).get("tool_call_id")
        if isinstance(tool_call_id, str):
            references.setdefault((entry.run_id, tool_call_id), []).append(
                _journal_reference(entry)
            )
    return references


def _engine_tool_calls(
    entries: list,
    references_by_operation: dict[tuple[UUID, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Project delegation/fan-out/timer calls omitted by simulator records."""
    from src.services.agent_evaluations.runner import is_engine_tool
    from src.services.agent_runtime import types as runtime_types

    calls: list[dict[str, Any]] = []
    seen_operations: set[tuple[UUID, str]] = set()
    for entry in entries:
        if entry.kind != runtime_types.JOURNAL_TOOL_CALL:
            continue
        data = entry.data or {}
        name = data.get("tool_name")
        tool_call_id = data.get("tool_call_id")
        if not isinstance(name, str) or not is_engine_tool(name):
            continue
        if not isinstance(tool_call_id, str):
            raise EvaluationEvidenceError(
                "Engine tool-call journal entry is missing its tool_call_id."
            )
        arguments = data.get("arguments")
        if not isinstance(arguments, dict):
            raise EvaluationEvidenceError(
                "Engine tool-call journal entry is missing durable arguments."
            )
        operation_key = (entry.run_id, tool_call_id)
        if operation_key in seen_operations:
            continue
        seen_operations.add(operation_key)
        references = list(references_by_operation.get(operation_key, []))
        if not references:
            references = [_journal_reference(entry)]
        calls.append(
            {
                "name": name,
                "arguments": redact_value(arguments),
                "sequence": entry.sequence,
                "operation_id": f"{entry.run_id}:{tool_call_id}",
                "journal_references": references,
            }
        )
    return calls


def _tool_call_order_key(item: dict[str, Any]) -> tuple[str, int, str, str]:
    """Stable composite order for simulator and engine evidence calls."""
    references = item.get("journal_references") or []
    if references:
        first = min(
            references,
            key=lambda reference: (
                str(reference.get("run_id", "")),
                int(reference.get("sequence", -1)),
            ),
        )
        return (
            str(first.get("run_id", "")),
            int(first.get("sequence", -1)),
            str(item.get("name", "")),
            str(item.get("operation_id", "")),
        )
    return ("", int(item.get("sequence", -1)), str(item.get("name", "")), str(item.get("operation_id", "")))


def _delegation_evidence(
    root_id: UUID,
    rows: dict,
    references: dict[UUID, list[dict[str, Any]]],
) -> dict[str, Any]:
    children_by_parent: dict[UUID, list] = {}
    for row in rows.values():
        if row.parent_run_id in rows and row.parent_run_id != row.id:
            children_by_parent.setdefault(row.parent_run_id, []).append(row)
    for children in children_by_parent.values():
        children.sort(key=lambda row: (row.created_at, row.id))

    def render(row, path: set[UUID]) -> dict[str, Any]:
        item = {
            "run_id": str(row.id),
            "parent_run_id": str(row.parent_run_id) if row.parent_run_id else None,
            # Assertion evidence is an execution artifact. Never let a live
            # Agent rename rewrite historical delegation evidence.
            "agent_name": (row.execution_snapshot or {}).get("agent_name"),
            "terminal_status": row.status,
            "journal_references": list(references.get(row.id, [])),
            "children": [],
        }
        if row.id in path:
            item["cycle_detected"] = True
            return item
        item["children"] = [
            render(child, path | {row.id})
            for child in children_by_parent.get(row.id, [])
        ]
        return item

    descendants = [
        render(row, {root_id})
        for row in children_by_parent.get(root_id, [])
    ]
    return {
        "children": descendants,
        "descendant_count": sum(1 for row in rows if row != root_id),
        "journal_references": [
            reference for values in references.values() for reference in values
        ],
    }


def _usage_evidence(root, run_rows: list, usage_rows: list) -> dict[str, Any]:
    iterations = sum(int(row.iterations_used or 0) for row in run_rows)
    run_tokens = sum(int(row.tokens_used or 0) for row in run_rows)
    input_tokens = sum(int(row.input_tokens or 0) for row in usage_rows)
    output_tokens = sum(int(row.output_tokens or 0) for row in usage_rows)
    cache_read_tokens = sum(int(row.cache_read_tokens or 0) for row in usage_rows)
    cache_write_tokens = sum(int(row.cache_write_tokens or 0) for row in usage_rows)
    # Cache reads are already counted inside input tokens; the fraction is a
    # share of observed input, never a second addition to the token total.
    cache_hit_fraction = (
        cache_read_tokens / input_tokens if input_tokens else None
    )
    # ``tokens`` stays inclusive of input+output only. Counts record a
    # meaningful observed zero when rows exist with zero tokens; only the
    # fraction is None when no input was observed.
    usage_tokens = input_tokens + output_tokens
    provider_durations = [
        int(row.duration_ms) for row in usage_rows if row.duration_ms is not None
    ]
    if root.started_at is not None and root.completed_at is not None:
        wall_latency_ms = max(
            0, int((root.completed_at - root.started_at).total_seconds() * 1_000)
        )
    else:
        wall_latency_ms = (
            int(root.duration_ms) if root.duration_ms is not None else None
        )
    costs = [
        row.cost if row.cost is not None else row.provider_cost
        for row in usage_rows
        if row.cost is not None or row.provider_cost is not None
    ]
    total_cost = sum((Decimal(cost) for cost in costs), Decimal("0"))
    return {
        "iterations": iterations,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cache_hit_fraction": cache_hit_fraction,
        # When durable runtime usage exists, its input/output total is the
        # deterministic evidence value. The run scalar remains the fallback
        # only for legacy rows that predate per-call usage records.
        "tokens": usage_tokens if usage_rows else run_tokens,
        "cost_usd": str(total_cost) if costs else None,
        # Wall latency is the root execution interval. Summing provider calls
        # double-counts parallel work and loses queue/delegation waits.
        "latency_ms": wall_latency_ms,
        "provider_latency_ms": sum(provider_durations) if provider_durations else None,
        "model_calls": len(usage_rows),
    }
