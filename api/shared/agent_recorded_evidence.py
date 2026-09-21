"""Authorized projection of durable agent runs into recorded assertion input.

:func:`load_recorded_run_evidence` reads one selected run plus the
descendant tree reachable through ``parent_run_id`` linkage and returns
``{evidence, completeness, evidence_refs, limitations}`` shaped for the
A1 recorded-assertion wrapper
(``shared/agent_recorded_evaluation.py``). Completeness keys match the
A1 contract exactly.

Recording proofs (conservative: anything unproven is ``False`` with a
generic limitation, never invented):

- Authorization: the selected run loads under the canonical
  :func:`agent_run_visibility_conditions
  <src.services.execution.agent_run_access.agent_run_visibility_conditions>`,
  plus an explicit tenant comparison for non-superusers (a non-superuser
  with ``organization_id=None`` sees nothing). Descendants are included
  only through parent linkage from the selected run -- never ancestors
  or siblings -- with the same visibility rule and same-tenant check
  enforced per node. Withheld nodes are never read; the tree is marked
  incomplete with a generic reason that leaks no hidden IDs, counts, or
  content. Non-visible and nonexistent top-level IDs raise the same
  domain not-found error.
- Complete call history requires durable terminal evidence (every
  included run is in ``TERMINAL_STATUSES``), a nonempty journal with
  unbroken sequences from 1 on every included node
  (``run_store.next_journal_sequence`` allocates ``max+1``, so the
  first entry of an intact history is 1), and a durable completion
  entry consistent with each row status. A terminal row alone proves
  neither intact history nor output provenance. Journal/ledger
  correspondence additionally requires matching tool identity on top of
  call identity ``(run_id, provider_tool_call_id)``: repeated journal
  observations of one operation project and count once, truly distinct
  repeated calls (different call IDs) project separately, and
  conflicting identity, arguments, or state never proves completeness.
  Engine-owned calls (``runner.is_engine_tool``: ``delegate_to_*``,
  ``delegate_agents``, ``sleep_until``) create no ledger rows and are
  proven by the journal alone. Legacy ``AgentRunStep`` rows are
  observational only and never prove history.
- Dispatch states come from ``tool_invocations``: only ``completed`` /
  ``failed`` ledger rows count as executed side effects. ``planned`` /
  ``running`` rows are in flight, ``uncertain`` rows are observed calls
  with incomplete results (never replayed); any of them makes the
  real-tool count unprovable. A ``failed`` ledger row proves the
  dispatch occurred -- the call happened -- but proves nothing about
  successful side effects. Journal entries are never assumed
  dispatched (schema rejection can occur before dispatch), so
  ``real_tool_executions`` counts terminal ledger rows only.
- Ordering across runs carries no causal meaning (parallel children do
  not acquire global order from timestamps), so ``tool_order`` is true
  only for a proven-complete single-run projection.
- ``tool_arguments`` is true only when every projected call carries a
  dict of arguments with no redacted values. Ledger arguments are
  redacted at dispatch time with the ``[REDACTED]`` marker
  (``core.secret_string``); any marker means values needed to evaluate
  were lost. The final redacted evidence is scanned again, so
  sensitive-key redaction introduced by the return-time redaction pass
  also forces incompleteness.
- A selected terminal run's ``output=None`` is a proven recorded null
  only with durable completion evidence for the selected run;
  non-terminal, completion-less, or legacy-missing output stays
  incomplete. Usage rows exclude sequence-0 summarization rows
  (``AIUsage.sequence > 0``), and valid row amounts are returned as
  observational evidence, but token and cost budget dimensions stay
  incomplete because the current runtime contract does not prove every
  model call has a persisted ``AIUsage`` row (``log_usage`` failures can
  be swallowed). Zero usage is never assumed from missing rows.
  Run-scalar counters are reported only over a complete tree in which
  every node is terminal with intact evidence -- never partial subsets,
  never root+child double-counts. Non-finite or negative usage values
  are rejected, and invalid negative latency is reported unusable rather
  than clamped to zero. ``simulator_state`` stays absent. Delegation
  traversal is bounded with explicit cycle detection; a cycle never
  yields a silent complete projection.
- Every collection is bounded before processing; overflow raises a
  domain oversized error instead of truncating and claiming complete.
  Checkpoints alone prove nothing and are not read. No synthetic-session
  lookup, no writes, no external calls. The returned snapshot is
  immutable data for later job admission to freeze; this service does
  not persist it.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunJournalEntry,
    AgentRunStep,
    AgentToolInvocation,
)
from src.models.orm.ai_usage import AIUsage
from src.services.agent_evaluations.simulator_models import redact_value
from src.services.agent_runtime import types as runtime_types
from src.services.execution.agent_run_access import (
    agent_run_visibility_conditions,
)

MAX_RECORDED_TREE_RUNS = 128
MAX_RECORDED_JOURNAL_ENTRIES = 4_000
MAX_RECORDED_INVOCATIONS = 4_000
MAX_RECORDED_USAGE_ROWS = 4_000

_COMPLETED_LEDGER_STATES = frozenset({"completed", "failed"})
_IN_FLIGHT_LEDGER_STATES = frozenset({"planned", "running"})

REDACTED_MARKER = "[REDACTED]"


class RecordedEvidenceError(Exception):
    """The durable rows cannot safely produce a recorded assertion input."""


class RecordedEvidenceNotFound(RecordedEvidenceError):
    """Selected run is nonexistent or not visible (same error either way)."""


class RecordedEvidenceOversized(RecordedEvidenceError):
    """A bounded collection overflowed; refusing instead of truncating."""


def _is_engine_tool(tool_name: str) -> bool:
    from src.services.agent_evaluations.runner import is_engine_tool

    return is_engine_tool(tool_name)


async def _load_tree_ids(
    session: AsyncSession, selected: AgentRun, user: UserPrincipal
) -> tuple[list[UUID], bool]:
    """Expand parent linkage downward, authorizing each level in SQL.

    Only same-tenant runs passing the canonical visibility conditions
    are traversed or materialized. Hidden direct children are detected
    through a bounded existence probe (reported as one generic
    limitation); traversal never continues through a withheld parent to
    a nominally visible grandchild, and hidden child counts cannot cause
    an oversized error.
    """
    ordered: list[UUID] = [selected.id]
    seen: set[UUID] = {selected.id}
    frontier: list[UUID] = [selected.id]
    withheld = False
    while frontier:
        remaining = MAX_RECORDED_TREE_RUNS - len(seen)
        if remaining < 0:
            raise RecordedEvidenceOversized(
                f"Recorded run tree exceeds {MAX_RECORDED_TREE_RUNS} nodes."
            )
        authorized_predicate = and_(
            AgentRun.org_id == selected.org_id,
            *agent_run_visibility_conditions(user),
        )
        authorized_children = list(
            (
                await session.execute(
                    select(AgentRun.id)
                    .where(
                        AgentRun.parent_run_id.in_(frontier),
                        AgentRun.id.not_in(seen),
                        authorized_predicate,
                    )
                    .limit(remaining + 1)
                )
            )
            .scalars()
            .all()
        )
        if len(authorized_children) > remaining:
            raise RecordedEvidenceOversized(
                f"Recorded run tree exceeds {MAX_RECORDED_TREE_RUNS} nodes."
            )
        hidden_exists = (
            await session.execute(
                select(
                    exists().where(
                        AgentRun.parent_run_id.in_(frontier),
                        AgentRun.id.not_in(seen),
                        authorized_predicate.is_not(True),
                    )
                )
            )
        ).scalar()
        if hidden_exists:
            withheld = True
        frontier = []
        for child_id in authorized_children:
            if child_id in seen:
                continue
            seen.add(child_id)
            ordered.append(child_id)
            frontier.append(child_id)
        if len(seen) > MAX_RECORDED_TREE_RUNS:
            raise RecordedEvidenceOversized(
                f"Recorded run tree exceeds {MAX_RECORDED_TREE_RUNS} nodes."
            )
    return ordered, withheld


def _contains_redacted(value: Any) -> bool:
    if isinstance(value, str):
        return REDACTED_MARKER in value
    if isinstance(value, dict):
        return any(_contains_redacted(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_redacted(item) for item in value)
    return False


def _journal_reference(entry: AgentRunJournalEntry) -> dict[str, Any]:
    return {
        "run_id": str(entry.run_id),
        "sequence": entry.sequence,
        "kind": entry.kind,
    }


async def _load_selected_run(
    session: AsyncSession, target_id: UUID, user: UserPrincipal
) -> AgentRun:
    selected = (
        await session.execute(
            select(AgentRun).where(
                AgentRun.id == target_id,
                *agent_run_visibility_conditions(user),
            )
        )
    ).scalar_one_or_none()
    if selected is None:
        raise RecordedEvidenceNotFound("Agent run not found or not visible.")
    if not user.is_superuser and (
        user.organization_id is None or selected.org_id != user.organization_id
    ):
        raise RecordedEvidenceNotFound("Agent run not found or not visible.")
    return selected


async def load_recorded_run_evidence(
    session: AsyncSession, run_id: UUID, *, user: UserPrincipal
) -> dict[str, Any]:
    """Project one authorized run and its descendants into A1 input.

    Returns ``{"evidence", "completeness", "evidence_refs",
    "limitations"}``. Raises :class:`RecordedEvidenceNotFound` when the
    selected run is nonexistent or not visible, and
    :class:`RecordedEvidenceOversized` when a bounded collection
    overflows. Performs no writes and no external calls.
    """
    if not isinstance(user, UserPrincipal):
        raise TypeError("user must be a UserPrincipal")
    target_id = run_id if isinstance(run_id, UUID) else UUID(str(run_id))

    selected = await _load_selected_run(session, target_id, user)
    tree_ids, withheld_children = await _load_tree_ids(session, selected, user)
    nodes = list(
        (await session.execute(select(AgentRun).where(AgentRun.id.in_(tree_ids))))
        .scalars()
        .all()
    )
    by_id = {node.id: node for node in nodes}

    included: dict[UUID, AgentRun] = {}
    withheld = withheld_children
    for node_id in tree_ids:
        node = by_id.get(node_id)
        if node is None:
            withheld = True
            continue
        included[node_id] = node
    included_ids = list(included)

    terminal = selected.status in runtime_types.TERMINAL_STATUSES
    limitations: list[str] = []
    if not terminal:
        limitations.append(
            "selected run is not in a terminal status; recorded history is partial"
        )
    if withheld:
        limitations.append(
            "delegation tree is incomplete: some descendant runs are "
            "not visible or are in another tenant"
        )
    tree_complete = terminal and not withheld

    journal_rows = list(
        (
            await session.execute(
                select(AgentRunJournalEntry)
                .where(AgentRunJournalEntry.run_id.in_(included_ids))
                .order_by(
                    AgentRunJournalEntry.run_id, AgentRunJournalEntry.sequence
                )
                .limit(MAX_RECORDED_JOURNAL_ENTRIES + 1)
            )
        )
        .scalars()
        .all()
    )
    if len(journal_rows) > MAX_RECORDED_JOURNAL_ENTRIES:
        raise RecordedEvidenceOversized(
            "Recorded journal exceeds "
            f"{MAX_RECORDED_JOURNAL_ENTRIES} entries."
        )

    invocations = list(
        (
            await session.execute(
                select(AgentToolInvocation)
                .where(AgentToolInvocation.run_id.in_(included_ids))
                .order_by(AgentToolInvocation.run_id)
                .limit(MAX_RECORDED_INVOCATIONS + 1)
            )
        )
        .scalars()
        .all()
    )
    if len(invocations) > MAX_RECORDED_INVOCATIONS:
        raise RecordedEvidenceOversized(
            f"Recorded invocations exceed {MAX_RECORDED_INVOCATIONS} rows."
        )

    usage_rows = list(
        (
            await session.execute(
                select(AIUsage)
                .where(
                    AIUsage.agent_run_id.in_(included_ids),
                    # Sequence zero is post-run summarization spend, not
                    # runtime evidence for an evaluation assertion.
                    AIUsage.sequence > 0,
                )
                .order_by(AIUsage.id)
                .limit(MAX_RECORDED_USAGE_ROWS + 1)
            )
        )
        .scalars()
        .all()
    )
    if len(usage_rows) > MAX_RECORDED_USAGE_ROWS:
        raise RecordedEvidenceOversized(
            f"Recorded usage exceeds {MAX_RECORDED_USAGE_ROWS} rows."
        )

    step_run_ids = set(
        (
            await session.execute(
                select(AgentRunStep.run_id)
                .where(AgentRunStep.run_id.in_(included_ids))
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    projection = _project_tool_calls(
        journal_rows, invocations, step_run_ids, included, limitations
    )
    intact_by_run: dict[UUID, bool] = projection["intact_by_run"]
    all_intact = bool(included) and all(
        intact_by_run.get(node_id, False) for node_id in included
    )
    history_proven = tree_complete and all_intact and projection["history_proven"]
    selected_intact = intact_by_run.get(selected.id, False)

    delegation_evidence = _delegation_evidence(selected.id, included)
    if delegation_evidence["cycle_detected"]:
        tree_complete = False
        history_proven = False
    delegation_proven = (
        tree_complete and all_intact and not delegation_evidence["cycle_detected"]
    )
    if delegation_evidence["cycle_detected"]:
        limitations.append(
            "delegation links contain a cycle; the tree cannot be proven complete"
        )

    evidence: dict[str, Any] = {
        "terminal_status": selected.status,
        "output": redact_value(selected.output),
        "malformed_output": selected.contract_valid is False,
        "tool_calls": projection["tool_calls"],
        "delegation": {"children": delegation_evidence["children"]},
    }
    if selected.output_schema is not None:
        evidence["output_schema"] = redact_value(selected.output_schema)

    completeness: dict[str, bool] = {
        "terminal_status": terminal,
        "output": terminal and selected_intact,
        "tool_calls": history_proven,
        "tool_order": history_proven and len(included) == 1,
        "tool_arguments": history_proven and projection["arguments_proven"],
        "delegation": delegation_proven,
        "real_tool_executions": False,
        "usage.iterations": False,
        "usage.tokens": False,
        "usage.cost_usd": False,
        "usage.latency_ms": False,
    }
    if terminal and not selected_intact:
        limitations.append(
            "recorded output lacks durable completion evidence"
        )
    if history_proven and len(included) != 1:
        limitations.append(
            "tool ordering across runs has no established causal order"
        )
    if tree_complete and not projection["arguments_proven"]:
        limitations.append(projection["arguments_limitation"])
    if not tree_complete:
        limitations.append(
            "delegation evidence is partial: descendant runs are missing "
            "or the selected run is not terminal"
        )

    if history_proven and projection["real_count_proven"]:
        evidence["real_tool_executions"] = projection["real_tool_executions"]
        completeness["real_tool_executions"] = True
    else:
        limitations.append(
            "real tool execution count cannot be proven from dispatch evidence"
        )

    _apply_usage_evidence(
        evidence, completeness, limitations, selected, included, usage_rows,
        tree_complete, all_intact,
    )

    evidence = redact_value(evidence)
    if completeness["tool_arguments"]:
        for call in evidence["tool_calls"]:
            if _contains_redacted(call.get("arguments")):
                completeness["tool_arguments"] = False
                limitations.append(
                    "tool arguments contain redacted values; exact-argument "
                    "assertions cannot be proven"
                )
                break
    deduped: list[str] = []
    for limitation in limitations:
        if limitation not in deduped:
            deduped.append(limitation)
    return {
        "evidence": evidence,
        "completeness": completeness,
        "evidence_refs": projection["evidence_refs"],
        "limitations": deduped,
    }


def _run_journal_intact(
    run_id: UUID,
    entries: list[AgentRunJournalEntry],
    node_status: str,
    step_run_ids: set[UUID],
    limitations: list[str],
) -> bool:
    """One node's journal proves intact history only when complete.

    Requires a terminal row status, a nonempty journal with contiguous
    sequences from 1, and a durable completion entry consistent with the
    row status. A terminal row alone proves neither intact history nor
    output provenance; legacy steps without journal coverage stay
    incomplete.
    """
    if node_status not in runtime_types.TERMINAL_STATUSES:
        limitations.append(
            "tool call history is incomplete: a run is not terminal"
        )
        return False
    sequences = sorted(entry.sequence for entry in entries)
    if not sequences:
        if run_id in step_run_ids:
            limitations.append(
                "tool call history is incomplete: legacy steps exist "
                "without journal coverage"
            )
        else:
            limitations.append(
                "tool call history is incomplete: a run has an empty journal"
            )
        return False
    if sequences != list(range(1, len(sequences) + 1)):
        limitations.append(
            "tool call history is incomplete: journal sequences are not unbroken"
        )
        return False
    completions = [
        entry
        for entry in entries
        if entry.kind == runtime_types.JOURNAL_COMPLETION
    ]
    if not completions:
        limitations.append(
            "tool call history is incomplete: a run is missing durable "
            "completion evidence"
        )
        return False
    for completion in completions:
        status = (completion.data or {}).get("status")
        if not isinstance(status, str) or status != node_status:
            limitations.append(
                "tool call history is incomplete: durable completion "
                "evidence is inconsistent with the run status"
            )
            return False
    return True


def _project_tool_calls(
    journal_rows: list[AgentRunJournalEntry],
    invocations: list[AgentToolInvocation],
    step_run_ids: set[UUID],
    included: dict[UUID, AgentRun],
    limitations: list[str],
) -> dict[str, Any]:
    """Project the journal/ledger trace into ordered tool calls.

    Dispatch proof: a journal entry matches a ledger row only on both
    call identity ``(run_id, tool_call_id)`` and tool name. Repeated
    journal observations of one operation project a single call and
    count a single execution; truly distinct repeated calls (different
    call IDs) project separately. Conflicting identity, arguments, or
    state never proves completeness. A ``failed`` ledger row proves the
    dispatch occurred -- the call happened -- but proves nothing about
    successful side effects. Never invents trace entries: ledger-only
    rows mark history unproven without contributing calls, and
    in-flight or uncertain operations are observed but never counted as
    executed side effects.
    """
    matched_ledger: set[tuple[UUID, Any]] = set()
    tool_calls: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    history_proven = True
    arguments_proven = True
    arguments_limitation = ""
    real_executions = 0
    real_count_proven = True
    ledger: dict[tuple[UUID, str | None], AgentToolInvocation] = {}
    duplicate_ledger_keys: set[tuple[UUID, str | None]] = set()
    for inv in invocations:
        key = (inv.run_id, inv.provider_tool_call_id)
        if key in ledger:
            duplicate_ledger_keys.add(key)
            history_proven = False
            real_count_proven = False
            limitations.append(
                "tool call history is incomplete: duplicate dispatch "
                "records share one provider call identity"
            )
            continue
        ledger[key] = inv

    entries_by_run: dict[UUID, list[AgentRunJournalEntry]] = {}
    for entry in journal_rows:
        entries_by_run.setdefault(entry.run_id, []).append(entry)

    intact_by_run: dict[UUID, bool] = {}
    for node_id, node in included.items():
        intact_by_run[node_id] = _run_journal_intact(
            node_id,
            entries_by_run.get(node_id, []),
            node.status,
            step_run_ids,
            limitations,
        )

    def _note_arguments_missing() -> None:
        nonlocal arguments_proven, arguments_limitation
        arguments_proven = False
        arguments_limitation = "tool arguments are missing for a recorded call"

    def _note_arguments_redacted() -> None:
        nonlocal arguments_proven, arguments_limitation
        arguments_proven = False
        arguments_limitation = (
            "tool arguments contain redacted values; exact-argument "
            "assertions cannot be proven"
        )

    def _append_call(
        entry: AgentRunJournalEntry,
        name: str,
        arguments: dict[str, Any],
        operation_id: str | None,
    ) -> None:
        if _contains_redacted(arguments):
            _note_arguments_redacted()
        reference = _journal_reference(entry)
        if operation_id is not None:
            reference["operation_id"] = operation_id
        tool_calls.append(
            {
                "name": name,
                "arguments": arguments,
                "sequence": entry.sequence,
                "operation_id": operation_id,
                "journal_references": [reference],
            }
        )
        evidence_refs.append(dict(reference))

    grouped: dict[tuple[UUID, str], list[AgentRunJournalEntry]] = {}
    unkeyed: list[AgentRunJournalEntry] = []
    for entry in journal_rows:
        if entry.kind != runtime_types.JOURNAL_TOOL_CALL:
            continue
        data = entry.data or {}
        name = data.get("tool_name")
        if not isinstance(name, str) or not name:
            history_proven = False
            limitations.append(
                "tool call history is incomplete: a journal entry "
                "does not name a tool"
            )
            continue
        tool_call_id = data.get("tool_call_id")
        if isinstance(tool_call_id, str):
            grouped.setdefault((entry.run_id, tool_call_id), []).append(entry)
        else:
            unkeyed.append(entry)

    for (run_id, tool_call_id), observations in grouped.items():
        primary = min(observations, key=lambda item: item.sequence)
        data = primary.data or {}
        name = data.get("tool_name")
        for duplicate in observations:
            if duplicate is primary:
                continue
            duplicate_data = duplicate.data or {}
            if (
                duplicate_data.get("tool_name") != name
                or duplicate_data.get("arguments") != data.get("arguments")
            ):
                history_proven = False
                real_count_proven = False
                limitations.append(
                    "tool call history is incomplete: conflicting "
                    "recorded observations for one operation"
                )
                break
        invocation = ledger.get((run_id, tool_call_id))
        journal_args = data.get("arguments")
        if invocation is None:
            if not _is_engine_tool(name):
                history_proven = False
                real_count_proven = False
                limitations.append(
                    "tool call history is incomplete: a recorded call "
                    "has no matching dispatch record"
                )
            if isinstance(journal_args, dict):
                arguments = dict(journal_args)
            else:
                arguments = {}
                _note_arguments_missing()
            _append_call(primary, name, arguments, f"{run_id}:{tool_call_id}")
            continue
        matched_ledger.add((run_id, tool_call_id))
        ledger_args = invocation.arguments
        if invocation.tool_name != name:
            history_proven = False
            real_count_proven = False
            limitations.append(
                "tool call history is incomplete: tool identity "
                "conflicts between journal and dispatch record"
            )
            arguments = dict(journal_args) if isinstance(journal_args, dict) else {}
            _note_arguments_missing()
            _append_call(primary, name, arguments, invocation.operation_id)
            continue
        if isinstance(ledger_args, dict):
            arguments = dict(ledger_args)
            if isinstance(journal_args, dict) and journal_args != ledger_args:
                if _contains_redacted(ledger_args):
                    _note_arguments_redacted()
                else:
                    history_proven = False
                    real_count_proven = False
                    limitations.append(
                        "tool call history is incomplete: conflicting "
                        "recorded arguments for one operation"
                    )
        else:
            arguments = dict(journal_args) if isinstance(journal_args, dict) else {}
            _note_arguments_missing()
        if invocation.state in _COMPLETED_LEDGER_STATES:
            real_executions += 1
        elif invocation.state in _IN_FLIGHT_LEDGER_STATES:
            history_proven = False
            real_count_proven = False
            limitations.append(
                "tool call history is incomplete: an operation is still "
                "in flight"
            )
        else:  # uncertain or any other non-terminal ledger state
            history_proven = False
            real_count_proven = False
            limitations.append(
                "tool call history is incomplete: an operation result "
                "is uncertain"
            )
        _append_call(primary, name, arguments, invocation.operation_id)

    for entry in unkeyed:
        data = entry.data or {}
        name = data.get("tool_name")
        if not _is_engine_tool(name):
            history_proven = False
            real_count_proven = False
            limitations.append(
                "tool call history is incomplete: a recorded call has "
                "no matching dispatch record"
            )
        if isinstance(data.get("arguments"), dict):
            arguments = dict(data["arguments"])
        else:
            arguments = {}
            _note_arguments_missing()
        _append_call(entry, name, arguments, None)

    for inv in invocations:
        key = (inv.run_id, inv.provider_tool_call_id)
        if key in duplicate_ledger_keys or key not in matched_ledger:
            history_proven = False
            real_count_proven = False
            limitations.append(
                "tool call history is incomplete: a dispatch record "
                "has no matching journal entry"
            )

    tool_calls.sort(
        key=lambda call: (
            str(call["journal_references"][0]["run_id"]),
            int(call["sequence"]),
            str(call["name"]),
            str(call.get("operation_id") or ""),
        )
    )
    return {
        "tool_calls": tool_calls,
        "evidence_refs": evidence_refs,
        "history_proven": history_proven,
        "arguments_proven": arguments_proven,
        "arguments_limitation": arguments_limitation,
        "real_tool_executions": real_executions,
        "real_count_proven": real_count_proven,
        "intact_by_run": intact_by_run,
    }


def _delegation_evidence(
    selected_id: UUID, included: dict[UUID, AgentRun]
) -> dict[str, Any]:
    """Render the included descendants as a nested delegation tree.

    Traversal is bounded by the included set size with explicit cycle
    detection: corrupt cyclic links truncate with a marker instead of
    recursing, and the caller must not claim a complete projection.
    """
    children_by_parent: dict[UUID, list[AgentRun]] = {}
    for node in included.values():
        parent_id = node.parent_run_id
        if parent_id == node.id:
            children_by_parent.setdefault(parent_id, []).append(node)
        elif parent_id is not None and parent_id in included:
            children_by_parent.setdefault(parent_id, []).append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda row: (row.created_at, row.id))
    cycle_detected = False

    def render(node: AgentRun, path: set[UUID], depth: int) -> dict[str, Any]:
        nonlocal cycle_detected
        if node.id in path or depth > len(included):
            cycle_detected = True
            return {
                "run_id": str(node.id),
                "parent_run_id": str(node.parent_run_id)
                if node.parent_run_id
                else None,
                "agent_name": (node.execution_snapshot or {}).get("agent_name"),
                "terminal_status": node.status,
                "children": [],
                "cycle_truncated": True,
            }
        item = {
            "run_id": str(node.id),
            "parent_run_id": str(node.parent_run_id)
            if node.parent_run_id
            else None,
            "agent_name": (node.execution_snapshot or {}).get("agent_name"),
            "terminal_status": node.status,
            "children": [],
        }
        item["children"] = [
            render(child, path | {node.id}, depth + 1)
            for child in children_by_parent.get(node.id, [])
        ]
        return item

    selected = included[selected_id]
    return {
        "children": [
            render(child, {selected.id}, 1)
            for child in children_by_parent.get(selected.id, [])
        ],
        "cycle_detected": cycle_detected,
    }


def _apply_usage_evidence(
    evidence: dict[str, Any],
    completeness: dict[str, bool],
    limitations: list[str],
    selected: AgentRun,
    included: dict[UUID, AgentRun],
    usage_rows: list[AIUsage],
    tree_complete: bool,
    all_intact: bool,
) -> None:
    """Attach only usage counters whose recording semantics prove them.

    Run-scalar iteration sums require a complete tree in which every
    node is terminal with intact evidence; anything less is a partial
    subset and stays false. Missing rows never become zero. Usage rows
    can contribute observational amounts, but token/cost completeness
    remains false because the runtime does not prove full row coverage.
    Non-finite or negative values are rejected.
    """
    proven_tree = tree_complete and all_intact
    if proven_tree:
        iteration_values = [node.iterations_used for node in included.values()]
        if any(value is None for value in iteration_values):
            limitations.append("recorded iteration counters are missing")
        elif any(value < 0 for value in iteration_values):
            limitations.append("recorded iteration counters are not usable")
        else:
            evidence.setdefault("usage", {})["iterations"] = sum(
                int(value) for value in iteration_values
            )
            completeness["usage.iterations"] = True
    else:
        limitations.append(
            "recorded usage is partial: some runs are not visible, "
            "not terminal, or lack intact evidence"
        )
    if usage_rows and proven_tree:
        usage = evidence.setdefault("usage", {})
        limitations.append(
            "recorded model usage rows are observational: complete "
            "usage-row coverage is not proven by the runtime contract"
        )
        token_values = [
            (row.input_tokens, row.output_tokens)
            for row in usage_rows
        ]
        if any(
            input_tokens is None
            or output_tokens is None
            or input_tokens < 0
            or output_tokens < 0
            for input_tokens, output_tokens in token_values
        ):
            limitations.append("recorded token values are partial or not usable")
        else:
            input_tokens = sum(int(row.input_tokens) for row in usage_rows)
            output_tokens = sum(int(row.output_tokens) for row in usage_rows)
            usage["input_tokens"] = input_tokens
            usage["output_tokens"] = output_tokens
            usage["tokens"] = input_tokens + output_tokens
            usage["model_calls"] = len(usage_rows)
        if any(
            row.cost is None and row.provider_cost is None for row in usage_rows
        ):
            limitations.append(
                "recorded cost values are partial: a row has no cost"
            )
        else:
            total = sum(
                (Decimal(cost) for cost in (
                    row.cost if row.cost is not None else row.provider_cost
                    for row in usage_rows
                )),
                Decimal("0"),
            )
            total_float = float(total)
            if not math.isfinite(total_float) or total_float < 0:
                limitations.append("recorded cost values are not usable")
            else:
                usage["cost_usd"] = total_float
    elif not usage_rows:
        limitations.append(
            "no recorded model usage rows; zero usage is not assumed"
        )
    else:
        limitations.append(
            "recorded usage is partial: some runs are not visible, "
            "not terminal, or lack intact evidence"
        )
    latency_ms: int | None = None
    latency_unusable = False
    if selected.started_at is not None and selected.completed_at is not None:
        wall_ms = int(
            (selected.completed_at - selected.started_at).total_seconds() * 1_000
        )
        if wall_ms < 0:
            latency_unusable = True
        else:
            latency_ms = wall_ms
    elif selected.duration_ms is not None:
        if selected.duration_ms < 0:
            latency_unusable = True
        else:
            latency_ms = int(selected.duration_ms)
    if latency_ms is not None:
        evidence.setdefault("usage", {})["latency_ms"] = latency_ms
        completeness["usage.latency_ms"] = True
    elif latency_unusable:
        limitations.append("recorded latency values are not usable")
    else:
        limitations.append("no recorded latency values")
