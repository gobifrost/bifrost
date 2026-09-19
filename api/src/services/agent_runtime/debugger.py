"""Authorized read projections over the durable agent runtime journal.

The journal is the canonical debugger timeline. Every reader here reuses the
exact visibility/tenant checks of the AgentRun detail routes
(:func:`agent_run_visibility_conditions`): a user who cannot read a child run
directly cannot reveal it through a root tree or a descendant timeline.

Security posture:

- ``caller_context`` is never read, let alone returned.
- Lease tokens, credentials, and stored authorization tokens are never
  projected (snapshots carry configuration identity and hashes only).
- Journal payloads pass a per-kind response allowlist (default deny) plus
  key-name redaction of secret-bearing values (``[REDACTED]``). Write-time
  value redaction in ``run_store`` remains the first layer; this is the
  second, key-name layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from src.core.principal import UserPrincipal
from src.models.contracts.agent_debugger import (
    AgentCheckpointPage,
    AgentCheckpointSummary,
    AgentRunSnapshotView,
    AgentRunTree,
    AgentRunTreeNode,
    AgentSnapshotCompletionEvent,
    AgentSnapshotContract,
    AgentSnapshotLease,
    AgentSnapshotModel,
    AgentTimelineEntry,
    AgentTimelinePage,
    decode_debug_cursor,
    encode_debug_cursor,
)
from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJoin,
    AgentRunJoinMember,
    AgentRunJournalEntry,
    AgentToolInvocation,
)
from src.services.agent_runtime import types as rt
from src.services.agent_runtime.checkpoint_codec import (
    CheckpointDecodeError,
    decode_messages,
)
from src.services.execution.agent_run_access import (
    agent_run_visibility_conditions,
)

logger = logging.getLogger(__name__)


class DebuggerNotFoundError(Exception):
    """Requested run is missing or invisible to the caller (maps to 404)."""


# -----------------------------------------------------------------------------
# Limits
# -----------------------------------------------------------------------------

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
MAX_CHECKPOINT_LIMIT = 200
MAX_TREE_DEPTH = 25
MAX_TREE_NODES = 500


def clamp_limit(limit: int, maximum: int = MAX_PAGE_LIMIT) -> int:
    """Clamp a page limit to ``[1, maximum]``."""
    return max(1, min(int(limit), maximum))


# -----------------------------------------------------------------------------
# Central redaction + response allowlist
# -----------------------------------------------------------------------------

REDACTED = "[REDACTED]"

_SECRET_KEY_RE = re.compile(
    r"password|passwd|secret|token|api[_-]?key|auth|credential"
    r"|private[_-]?key|client[_-]?secret|access[_-]?key|session[_-]?key"
    r"|cookie|authorization|set-cookie",
    re.IGNORECASE,
)

# Keys that must never appear in any debugger payload, even redacted.
_FORBIDDEN_KEYS = frozenset(
    {
        "caller_context",
        "lease_token",
        "credentials",
        "authorization",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "private_key",
        "api_key",
    }
)

# Per-kind response allowlist over journal ``data`` keys. Unknown keys are
# dropped (default deny). Keys observed in the runtime write paths
# (autonomous executor, delegation, timers, resume, tool invocations).
_DETAIL_ALLOWLIST: dict[str, frozenset[str]] = {
    rt.JOURNAL_MODEL_REQUEST: frozenset(
        {"model", "provider", "attempt", "message_count", "max_tokens"}
    ),
    rt.JOURNAL_MODEL_RESPONSE: frozenset(
        {
            "model",
            "finish_reason",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "duration_ms",
            "attempt",
        }
    ),
    rt.JOURNAL_MODEL_ERROR: frozenset({"model", "error", "attempt"}),
    rt.JOURNAL_TOOL_CALL: frozenset(
        {
            "tool_name",
            "operation_id",
            "provider_tool_call_id",
            "arguments",
            "attempt",
        }
    ),
    rt.JOURNAL_TOOL_RESULT: frozenset(
        {
            "tool_name",
            "operation_id",
            "provider_tool_call_id",
            "result",
            "duration_ms",
            "attempt",
        }
    ),
    rt.JOURNAL_TOOL_ERROR: frozenset(
        {
            "tool_name",
            "operation_id",
            "provider_tool_call_id",
            "error",
            "duration_ms",
            "attempt",
        }
    ),
    rt.JOURNAL_DELEGATION: frozenset(
        {
            "tool_call_id",
            "child_run_id",
            "target_agent_id",
            "target_agent_name",
            "task",
        }
    ),
    rt.JOURNAL_WAIT: frozenset(
        {"child_run_ids", "join_id", "wait_kind", "target_status"}
    ),
    rt.JOURNAL_RESUME: frozenset({"reason", "from_status", "attempt"}),
    rt.JOURNAL_LEASE_RECOVERY: frozenset(
        {"previous_owner", "attempt", "reason", "uncertain_tools"}
    ),
    rt.JOURNAL_VALIDATION: frozenset({"valid", "errors", "corrected"}),
    rt.JOURNAL_COMPLETION: frozenset(
        {"status", "contract_valid", "contract_errors", "duration_ms"}
    ),
    rt.JOURNAL_CHECKPOINT: frozenset(
        {"checkpoint_sequence", "message_count", "attempt"}
    ),
    rt.JOURNAL_CANCELLATION: frozenset({"reason", "cancelled_by"}),
    rt.JOURNAL_TIMER: frozenset(
        {"tool_call_id", "reason", "wake_at", "fired", "duration_ms"}
    ),
}

_MAX_STRING_LEN = 4000
_MAX_DETAIL_KEYS = 50


def _redact_key_name(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key)) or key.lower() in _FORBIDDEN_KEYS


def _redact_value(key: str, value: Any, depth: int = 0) -> Any:
    """Redact ``value`` when ``key`` is secret-bearing; recurse otherwise."""
    if _redact_key_name(key):
        return REDACTED
    return _redact_any(value, depth + 1)


def _redact_any(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LEN:
            return value[:_MAX_STRING_LEN] + "…[truncated]"
        return value
    if isinstance(value, dict):
        if depth > 6:
            return REDACTED
        return {
            str(k): _redact_value(str(k), v, depth) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        if depth > 6:
            return REDACTED
        return [_redact_any(item, depth) for item in value[:100]]
    return value


def redact_detail(kind: str, data: dict[str, Any] | None) -> dict[str, Any]:
    """Project journal ``data`` through the kind allowlist + redaction."""
    allowed = _DETAIL_ALLOWLIST.get(kind, frozenset())
    detail: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if len(detail) >= _MAX_DETAIL_KEYS:
            break
        if key not in allowed:
            continue
        detail[key] = _redact_value(key, value)
    return detail


# -----------------------------------------------------------------------------
# Visibility helpers (exact detail-route checks)
# -----------------------------------------------------------------------------


async def _visible_run(
    session: AsyncSession, run_id: UUID, user: UserPrincipal
) -> AgentRun:
    """Load one run under the exact detail-route visibility checks."""
    result = await session.execute(
        select(AgentRun)
        .options(joinedload(AgentRun.agent))
        .where(
            AgentRun.id == run_id, *agent_run_visibility_conditions(user)
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise DebuggerNotFoundError(f"Agent run {run_id} not found")
    return run


# -----------------------------------------------------------------------------
# Run tree (single batched query, no N+1)
# -----------------------------------------------------------------------------


async def get_run_tree(
    session: AsyncSession, run_id: UUID, user: UserPrincipal
) -> AgentRunTree:
    """Load the delegation tree containing ``run_id`` in one batched query."""
    requested = await _visible_run(session, run_id, user)
    root_id = requested.root_run_id or requested.id

    result = await session.execute(
        select(AgentRun)
        .options(joinedload(AgentRun.agent))
        .where(
            (AgentRun.id == root_id) | (AgentRun.root_run_id == root_id),
            *agent_run_visibility_conditions(user),
        )
        # Keep the root in a bounded result even if corrupt timestamps put a
        # descendant before it. Fetch one sentinel row to report truncation
        # without materializing an unbounded delegation tree.
        .order_by((AgentRun.id == root_id).desc(), AgentRun.created_at, AgentRun.id)
        .limit(MAX_TREE_NODES + 1)
    )
    rows = list(result.scalars().unique().all())
    has_more_rows = len(rows) > MAX_TREE_NODES
    rows = rows[:MAX_TREE_NODES]
    by_id = {row.id: row for row in rows}
    if root_id not in by_id:
        # The root itself is invisible: reveal nothing about the tree.
        raise DebuggerNotFoundError(f"Agent run {run_id} not found")

    children: dict[UUID, list[AgentRun]] = {row.id: [] for row in rows}
    for row in rows:
        parent_id = row.parent_run_id
        if parent_id is not None and parent_id in by_id and parent_id != row.id:
            children[parent_id].append(row)

    truncated = has_more_rows
    represented: set[UUID] = set()

    def add_diagnostic(node: AgentRunTreeNode, message: str) -> None:
        node.diagnostic = (
            f"{node.diagnostic}; {message}" if node.diagnostic else message
        )

    def build(node_run: AgentRun, depth: int, path: set[UUID]) -> AgentRunTreeNode:
        represented.add(node_run.id)
        node = AgentRunTreeNode(
            run_id=node_run.id,
            agent_id=node_run.agent_id,
            agent_name=node_run.agent.name if node_run.agent else None,
            status=node_run.status,
            parent_run_id=node_run.parent_run_id,
            depth=depth,
            attempt=node_run.attempt or 0,
            created_at=node_run.created_at,
            completed_at=node_run.completed_at,
        )
        if depth >= MAX_TREE_DEPTH:
            add_diagnostic(node, "maximum tree depth reached; subtree truncated")
            return node
        path = path | {node_run.id}
        for child in children.get(node_run.id, []):
            if child.id in path:
                add_diagnostic(
                    node, "parent cycle detected; subtree truncated to break the loop"
                )
                continue
            if child.id in represented:
                add_diagnostic(
                    node, "child already represented; subtree truncated to keep nodes unique"
                )
                continue
            node.children.append(build(child, depth + 1, path))
        return node

    root = by_id[root_id]
    root_node = build(root, 0, set())
    # Disconnected corrupt components and rows omitted by a depth boundary
    # still appear once under the requested root. ``rows`` is already capped,
    # so this attachment cannot exceed MAX_TREE_NODES.
    for row in rows:
        if row.id in represented:
            continue
        detached = build(row, 1, set())
        add_diagnostic(
            detached,
            "disconnected parent component; shown under the requested root",
        )
        root_node.children.append(detached)

    def _has_diagnostic(node: AgentRunTreeNode) -> bool:
        return node.diagnostic is not None or any(
            _has_diagnostic(child) for child in node.children
        )

    return AgentRunTree(
        requested_run_id=requested.id,
        root_run_id=root_id,
        root=root_node,
        total_runs=len(rows),
        truncated=truncated or _has_diagnostic(root_node),
    )


# -----------------------------------------------------------------------------
# Timeline cursors (opaque; stable for ``(run_id, sequence)`` pagination)
# -----------------------------------------------------------------------------


def encode_timeline_cursor(
    scope_run_id: UUID,
    run_id: UUID,
    sequence: int,
    created_at: datetime,
    *,
    include_descendants: bool,
    kind: str | None,
    attempt: int | None,
) -> str:
    """Encode an opaque timeline cursor.

    ``scope_run_id`` is the requested run the page belongs to (binds the
    cursor to its scope); ``(run_id, sequence, created_at)`` is the position
    of the last returned entry and yields a total order across descendants.
    """
    payload = json.dumps(
        {
            "scope_run_id": str(scope_run_id),
            "run_id": str(run_id),
            "sequence": sequence,
            "created_at": created_at.isoformat(),
            "include_descendants": include_descendants,
            "kind": kind,
            "attempt": attempt,
        },
        separators=(",", ":"),
    ).encode()
    return urlsafe_b64encode(payload).decode().rstrip("=")


def decode_timeline_cursor(
    cursor: str,
    scope_run_id: UUID,
    *,
    include_descendants: bool,
    kind: str | None,
    attempt: int | None,
) -> tuple[UUID, int, datetime]:
    """Decode a cursor into ``(last_run_id, last_sequence, last_created_at)``.

    Raises ``ValueError`` for malformed cursors or cursors bound to a
    different scope run.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(urlsafe_b64decode(padded).decode())
        if (
            UUID(str(payload["scope_run_id"])) != scope_run_id
            or payload["include_descendants"] is not include_descendants
            or payload["kind"] != kind
            or payload["attempt"] != attempt
        ):
            raise ValueError("cursor is invalid")
        return (
            UUID(str(payload["run_id"])),
            int(payload["sequence"]),
            datetime.fromisoformat(str(payload["created_at"])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, BinasciiError) as exc:
        raise ValueError("cursor is invalid") from exc


def _journal_attempt_expression() -> Any:
    """Resolve the claim attempt for journal rows that do not repeat it.

    Runtime writers put ``attempt`` on claim/recovery entries, while normal
    model, tool, wait, and completion entries are only bounded by that claim.
    The debugger therefore derives their attempt from the latest preceding
    claim rather than requiring every durable writer to duplicate metadata.
    """
    claim = aliased(AgentRunJournalEntry)
    claim_attempt = (
        select(claim.data.op("->>")("attempt"))
        .where(
            claim.run_id == AgentRunJournalEntry.run_id,
            claim.sequence <= AgentRunJournalEntry.sequence,
            claim.kind.in_((rt.JOURNAL_RESUME, rt.JOURNAL_LEASE_RECOVERY)),
            claim.data.op("->>")("attempt").is_not(None),
        )
        .order_by(claim.sequence.desc())
        .limit(1)
        .correlate(AgentRunJournalEntry)
        .scalar_subquery()
    )
    return func.coalesce(
        AgentRunJournalEntry.data.op("->>")("attempt"), claim_attempt
    )


def _as_uuid(value: Any) -> UUID | None:
    """Return a UUID only for a valid UUID-shaped journal value."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _visible_child_run_ids(
    session: AsyncSession,
    entries: list[AgentRunJournalEntry],
    user: UserPrincipal,
) -> set[UUID]:
    """Batch-check journal child references under the normal visibility rule."""
    candidates: set[UUID] = set()
    for entry in entries:
        data = entry.data or {}
        child_id = _as_uuid(data.get("child_run_id"))
        if child_id is not None:
            candidates.add(child_id)
        child_ids = data.get("child_run_ids")
        if isinstance(child_ids, list):
            for value in child_ids:
                child_id = _as_uuid(value)
                if child_id is not None:
                    candidates.add(child_id)
    if not candidates:
        return set()
    result = await session.execute(
        select(AgentRun.id).where(
            AgentRun.id.in_(candidates), *agent_run_visibility_conditions(user)
        )
    )
    return set(result.scalars().all())


async def _visible_descendants(
    session: AsyncSession, requested: AgentRun, user: UserPrincipal
) -> list[AgentRun]:
    """Load only the requested run and its visible descendants.

    ``root_run_id`` identifies a broad delegation tree, not a subtree. A
    bounded recursive CTE follows ``parent_run_id`` downward from the request
    so inspecting a nested child cannot pull in its ancestors or siblings.
    """
    descendants = (
        select(AgentRun.id.label("run_id"), literal(0).label("depth"))
        .where(AgentRun.id == requested.id, *agent_run_visibility_conditions(user))
        .cte("visible_agent_run_descendants", recursive=True)
    )
    descendants = descendants.union_all(
        select(AgentRun.id, descendants.c.depth + 1).where(
            AgentRun.parent_run_id == descendants.c.run_id,
            descendants.c.depth < MAX_TREE_DEPTH,
            *agent_run_visibility_conditions(user),
        )
    )
    descendant_ids = (
        select(descendants.c.run_id)
        .group_by(descendants.c.run_id)
        .order_by(func.min(descendants.c.depth), descendants.c.run_id)
        .limit(MAX_TREE_NODES)
    )
    result = await session.execute(
        select(AgentRun)
        .options(joinedload(AgentRun.agent))
        .where(AgentRun.id.in_(descendant_ids))
    )
    return list(result.scalars().unique().all())


def _visible_entry_data(
    data: dict[str, Any], visible_child_ids: set[UUID]
) -> dict[str, Any]:
    """Remove delegation references that cannot be read directly.

    ``detail`` and ``summary`` must use the same scrubbed payload. Projecting
    the typed ``child_run_id`` alone is insufficient because journal detail
    and the human summary would otherwise still disclose the hidden run.
    """
    visible = dict(data)
    child_id = _as_uuid(data.get("child_run_id"))
    if child_id not in visible_child_ids:
        for key in (
            "child_run_id",
            "target_agent_id",
            "target_agent_name",
            "task",
        ):
            visible.pop(key, None)
    child_ids = data.get("child_run_ids")
    if isinstance(child_ids, list):
        visible["child_run_ids"] = [
            str(child_id)
            for value in child_ids
            if (child_id := _as_uuid(value)) in visible_child_ids
        ]
    return visible


# -----------------------------------------------------------------------------
# Timeline summaries
# -----------------------------------------------------------------------------


def summarize_entry(kind: str, data: dict[str, Any] | None) -> str:
    """One-line human summary for a journal event."""
    data = data or {}
    if kind == rt.JOURNAL_MODEL_REQUEST:
        return f"model request ({data.get('model', 'model')})"
    if kind == rt.JOURNAL_MODEL_RESPONSE:
        calls = data.get("tool_calls") or []
        if calls:
            return f"model response with {len(calls)} tool call(s)"
        return f"model response ({data.get('finish_reason', 'done')})"
    if kind == rt.JOURNAL_MODEL_ERROR:
        return f"model error: {data.get('error', 'unknown')}"
    if kind == rt.JOURNAL_TOOL_CALL:
        return f"tool {data.get('tool_name', 'unknown')} started"
    if kind == rt.JOURNAL_TOOL_RESULT:
        return f"tool {data.get('tool_name', 'unknown')} completed"
    if kind == rt.JOURNAL_TOOL_ERROR:
        return f"tool {data.get('tool_name', 'unknown')} failed"
    if kind == rt.JOURNAL_DELEGATION:
        return (
            f"delegated to {data.get('target_agent_name', 'agent')} "
            f"(run {data.get('child_run_id', '?')})"
        )
    if kind == rt.JOURNAL_WAIT:
        return "waiting on child run(s)"
    if kind == rt.JOURNAL_RESUME:
        return f"resumed: {data.get('reason', 'woken')}"
    if kind == rt.JOURNAL_LEASE_RECOVERY:
        return f"lease recovered: {data.get('reason', 'worker lost')}"
    if kind == rt.JOURNAL_VALIDATION:
        return (
            "output contract valid"
            if data.get("valid")
            else "output contract failed"
        )
    if kind == rt.JOURNAL_COMPLETION:
        return f"run {data.get('status', 'completed')}"
    if kind == rt.JOURNAL_CHECKPOINT:
        return f"checkpoint {data.get('checkpoint_sequence', '?')} committed"
    if kind == rt.JOURNAL_CANCELLATION:
        return f"cancelled: {data.get('reason', 'requested')}"
    if kind == rt.JOURNAL_TIMER:
        if data.get("fired"):
            return "timer fired"
        return f"sleeping until {data.get('wake_at', '?')}"
    return kind.replace("_", " ")


async def _annotate_tool_state(
    session: AsyncSession,
    entries: list[AgentRunJournalEntry],
    projected: list[AgentTimelineEntry],
) -> None:
    """Attach durable tool reconciliation state to tool entries (batched)."""
    tool_call_ids: set[str] = set()
    for entry in entries:
        call_id = (entry.data or {}).get("provider_tool_call_id")
        if entry.kind in (
            rt.JOURNAL_TOOL_CALL,
            rt.JOURNAL_TOOL_RESULT,
            rt.JOURNAL_TOOL_ERROR,
        ) and isinstance(call_id, str):
            tool_call_ids.add(call_id)
    if not tool_call_ids:
        return
    scope_runs = {entry.run_id for entry in entries}
    result = await session.execute(
        select(AgentToolInvocation).where(
            AgentToolInvocation.run_id.in_(scope_runs),
            AgentToolInvocation.provider_tool_call_id.in_(tool_call_ids),
        )
    )
    invocations = {
        (inv.run_id, inv.provider_tool_call_id): inv
        for inv in result.scalars().all()
    }
    for entry, view in zip(entries, projected):
        call_id = (entry.data or {}).get("provider_tool_call_id")
        inv = invocations.get((entry.run_id, call_id))
        if inv is None:
            continue
        view.operation_id = inv.operation_id
        detail = dict(view.detail)
        detail["invocation_state"] = inv.state
        if inv.started_at and inv.completed_at:
            detail["duration_ms"] = int(
                (inv.completed_at - inv.started_at).total_seconds() * 1000
            )
        if inv.state == "uncertain":
            detail["uncertain"] = True
        if inv.reconciliation:
            detail["reconciliation"] = _redact_any(inv.reconciliation)
        if inv.error and entry.kind == rt.JOURNAL_TOOL_ERROR:
            detail.setdefault("error", inv.error[:2000])
        view.detail = detail


async def _annotate_join_progress(
    session: AsyncSession,
    scope_run_ids: set[UUID],
    projected: list[AgentTimelineEntry],
) -> None:
    """Attach fan-out join progress to wait/delegation entries (batched)."""
    if not scope_run_ids:
        return
    result = await session.execute(
        select(AgentRunJoin).where(
            AgentRunJoin.parent_run_id.in_(scope_run_ids)
        )
    )
    joins = list(result.scalars().all())
    if not joins:
        return
    member_result = await session.execute(
        select(AgentRunJoinMember).where(
            AgentRunJoinMember.join_id.in_([join.id for join in joins])
        )
    )
    members_by_join: dict[UUID, list[AgentRunJoinMember]] = {}
    for member in member_result.scalars().all():
        members_by_join.setdefault(member.join_id, []).append(member)
    joins_by_parent: dict[UUID, list[AgentRunJoin]] = {}
    for join in joins:
        joins_by_parent.setdefault(join.parent_run_id, []).append(join)
    for view in projected:
        if view.kind not in (rt.JOURNAL_WAIT, rt.JOURNAL_DELEGATION):
            continue
        parent_joins = joins_by_parent.get(view.run_id, [])
        if not parent_joins:
            continue
        progress = []
        for join in parent_joins:
            members = members_by_join.get(join.id, [])
            done = sum(
                1 for m in members if m.status in ("complete", "failed")
            )
            progress.append(
                {
                    "join_id": str(join.id),
                    "mode": join.mode,
                    "status": join.status,
                    "completed_members": done,
                    "total_members": len(members),
                }
            )
            if view.join_id is None:
                view.join_id = join.id
        view.detail = {**view.detail, "joins": progress}


async def get_timeline(
    session: AsyncSession,
    run_id: UUID,
    user: UserPrincipal,
    *,
    limit: int = DEFAULT_PAGE_LIMIT,
    cursor: str | None = None,
    kind: str | None = None,
    attempt: int | None = None,
    include_descendants: bool = False,
) -> AgentTimelinePage:
    """Cursor-page the journal timeline for ``run_id``.

    Single-run pages order by journal sequence; descendant pages merge runs
    by ``(created_at, run_id, sequence)``. Both resume without gaps or
    duplicates via the opaque cursor.
    """
    requested = await _visible_run(session, run_id, user)
    limit = clamp_limit(limit)
    if kind is not None and kind not in rt.JOURNAL_EVENT_KINDS:
        raise ValueError(f"unknown journal kind: {kind!r}")

    scope_ids: set[UUID] = {requested.id}
    run_meta: dict[UUID, AgentRun] = {requested.id: requested}
    if include_descendants:
        for row in await _visible_descendants(session, requested, user):
            scope_ids.add(row.id)
            run_meta[row.id] = row

    after_run: UUID | None = None
    after_sequence = 0
    after_created: datetime | None = None
    if cursor is not None:
        after_run, after_sequence, after_created = decode_timeline_cursor(
            cursor,
            requested.id,
            include_descendants=include_descendants,
            kind=kind,
            attempt=attempt,
        )

    journal_attempt = _journal_attempt_expression()
    stmt = select(
        AgentRunJournalEntry, journal_attempt.label("resolved_attempt")
    ).where(
        AgentRunJournalEntry.run_id.in_(scope_ids)
    )
    if kind is not None:
        stmt = stmt.where(AgentRunJournalEntry.kind == kind)
    if attempt is not None:
        stmt = stmt.where(journal_attempt == str(attempt))
    if after_created is not None and include_descendants:
        assert after_run is not None
        stmt = stmt.where(
            tuple_(
                AgentRunJournalEntry.created_at,
                AgentRunJournalEntry.run_id,
                AgentRunJournalEntry.sequence,
            )
            > tuple_(after_created, after_run, after_sequence)
        )
    elif cursor is not None:
        stmt = stmt.where(AgentRunJournalEntry.sequence > after_sequence)

    if include_descendants:
        stmt = stmt.order_by(
            AgentRunJournalEntry.created_at,
            AgentRunJournalEntry.run_id,
            AgentRunJournalEntry.sequence,
        )
    else:
        stmt = stmt.order_by(AgentRunJournalEntry.sequence)
    stmt = stmt.limit(limit + 1)

    result_rows = list((await session.execute(stmt)).all())
    has_more = len(result_rows) > limit
    result_rows = result_rows[:limit]
    entries = [row[0] for row in result_rows]
    attempts_by_entry_id = {
        entry.id: int(resolved_attempt)
        if isinstance(resolved_attempt, str) and resolved_attempt.isdigit()
        else None
        for entry, resolved_attempt in result_rows
    }

    # Descendant runs referenced by delegation entries may not be visible;
    # resolve child metadata only inside the visible scope.
    visible_children = scope_ids | await _visible_child_run_ids(
        session, entries, user
    )

    projected: list[AgentTimelineEntry] = []
    for entry in entries:
        meta = run_meta.get(entry.run_id)
        data = _visible_entry_data(entry.data or {}, visible_children)
        child_run_id = None
        candidate = _as_uuid(data.get("child_run_id"))
        if candidate is not None and candidate in visible_children:
            child_run_id = candidate
        join_id = None
        if isinstance(data.get("join_id"), str):
            try:
                join_id = UUID(str(data["join_id"]))
            except ValueError:
                join_id = None
        projected.append(
            AgentTimelineEntry(
                sequence=entry.sequence,
                kind=entry.kind,
                run_id=entry.run_id,
                root_run_id=meta.root_run_id if meta else None,
                parent_run_id=meta.parent_run_id if meta else None,
                attempt=attempts_by_entry_id[entry.id],
                created_at=entry.created_at,
                duration_ms=(
                    data.get("duration_ms")
                    if isinstance(data.get("duration_ms"), int)
                    else None
                ),
                input_tokens=(
                    data.get("input_tokens")
                    if isinstance(data.get("input_tokens"), int)
                    else None
                ),
                output_tokens=(
                    data.get("output_tokens")
                    if isinstance(data.get("output_tokens"), int)
                    else None
                ),
                summary=summarize_entry(
                    entry.kind, redact_detail(entry.kind, data)
                ),
                detail=redact_detail(entry.kind, data),
                operation_id=(
                    data.get("operation_id")
                    if isinstance(data.get("operation_id"), str)
                    else None
                ),
                child_run_id=child_run_id,
                join_id=join_id,
            )
        )

    await _annotate_tool_state(session, entries, projected)
    await _annotate_join_progress(session, scope_ids, projected)

    next_cursor = None
    if has_more and entries:
        last = entries[-1]
        next_cursor = encode_timeline_cursor(
            requested.id,
            last.run_id,
            last.sequence,
            last.created_at,
            include_descendants=include_descendants,
            kind=kind,
            attempt=attempt,
        )

    return AgentTimelinePage(
        run_id=requested.id,
        include_descendants=include_descendants,
        entries=projected,
        next_cursor=next_cursor,
    )


# -----------------------------------------------------------------------------
# Execution snapshot view (identity and hashes, never secrets)
# -----------------------------------------------------------------------------


def _snapshot_identity(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Project the immutable snapshot to debuggable identity fields."""
    snapshot = snapshot or {}
    system_prompt = snapshot.get("system_prompt")
    prompt_hash = None
    if isinstance(system_prompt, str) and system_prompt:
        prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()
    tools = snapshot.get("tools") or []
    tool_names = [
        str(tool.get("name"))
        for tool in tools
        if isinstance(tool, dict) and tool.get("name")
    ]
    delegated = snapshot.get("delegated_agents") or []
    model = snapshot.get("model") or {}
    return {
        "version": snapshot.get("format_version"),
        "agent_id": _as_uuid(snapshot.get("agent_id")),
        "agent_name": (
            str(snapshot["agent_name"])
            if isinstance(snapshot.get("agent_name"), str)
            else None
        ),
        "agent_updated_at": snapshot.get("agent_updated_at"),
        "prompt_hash": prompt_hash,
        "model": {
            "profile_id": (
                str(model.get("profile_id"))
                if model.get("profile_id")
                else None
            ),
            "provider": model.get("provider"),
            "model": model.get("model"),
            "llm_max_tokens": model.get("llm_max_tokens"),
        },
        "tool_names": tool_names,
        "delegated_agents": [
            {"id": str(d.get("id")), "name": str(d.get("name"))}
            for d in delegated
            if isinstance(d, dict) and d.get("id") and d.get("name")
        ],
        "system_tools": [str(t) for t in (snapshot.get("system_tools") or [])],
        "limits": dict(snapshot.get("limits") or {}),
    }


async def get_snapshot(
    session: AsyncSession, run_id: UUID, user: UserPrincipal
) -> AgentRunSnapshotView:
    """Project the immutable execution snapshot plus live lifecycle state."""
    run = await _visible_run(session, run_id, user)
    identity = _snapshot_identity(run.execution_snapshot)
    model = identity["model"]
    correlation = run.correlation or {}
    if not isinstance(correlation, dict):
        correlation = {}

    agent_updated_at = None
    if identity["agent_updated_at"]:
        try:
            agent_updated_at = datetime.fromisoformat(
                str(identity["agent_updated_at"])
            )
        except ValueError:
            agent_updated_at = None

    return AgentRunSnapshotView(
        run_id=run.id,
        # These are immutable snapshot fields, not mutable live Agent fields.
        agent_id=identity["agent_id"],
        agent_name=identity["agent_name"],
        snapshot_version=identity["version"],
        agent_updated_at=agent_updated_at,
        system_prompt_sha256=identity["prompt_hash"],
        model=AgentSnapshotModel(
            profile_id=model["profile_id"],
            provider=model["provider"],
            model=model["model"],
            llm_max_tokens=model["llm_max_tokens"],
        ),
        tool_names=identity["tool_names"],
        delegated_agents=identity["delegated_agents"],
        system_tools=identity["system_tools"],
        limits={str(k): v for k, v in identity["limits"].items()},
        correlation={
            str(key): _redact_value(str(key), value)
            for key, value in correlation.items()
        },
        status=run.status,
        attempt=run.attempt or 0,
        checkpoint_sequence=run.checkpoint_sequence or 0,
        wake_at=run.wake_at,
        lease=AgentSnapshotLease(
            owner=run.lease_owner,
            expires_at=run.lease_expires_at,
            last_progress_at=run.last_progress_at,
        ),
        usage={
            "iterations_used": run.iterations_used or 0,
            "tokens_used": run.tokens_used or 0,
            "duration_ms": run.duration_ms,
        },
        contract=AgentSnapshotContract(
            valid=run.contract_valid,
            errors=list(run.contract_errors or [])
            if run.contract_errors
            else None,
        ),
        completion_event=AgentSnapshotCompletionEvent(
            pending_at=run.completion_event_pending_at,
            emitted_at=run.completion_event_emitted_at,
            attempts=run.completion_event_attempts or 0,
            last_error=run.completion_event_last_error,
        ),
    )


# -----------------------------------------------------------------------------
# Checkpoint summaries (metadata only; state payloads stay server-side)
# -----------------------------------------------------------------------------


def _checkpoint_pending_state(
    state: dict[str, Any],
) -> tuple[bool | None, bool | None, bool | None]:
    """Derive pending engine work from the versioned message codec.

    Checkpoints do not carry ad-hoc ``pending_*`` keys. When the stored codec
    can be decoded, a tool call remains pending exactly until its matching
    tool return is present. A corrupt or legacy shape cannot establish that
    fact, so the public hints remain ``None`` rather than guessing.
    """
    if not isinstance(state.get("messages"), list):
        return None, None, None
    try:
        messages = decode_messages(state)
    except CheckpointDecodeError:  # Codec data is untrusted historical state.
        return None, None, None

    calls: dict[str, str] = {}
    returns: set[str] = set()
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                if not part.tool_call_id or not part.tool_name:
                    return None, None, None
                calls[part.tool_call_id] = part.tool_name
            elif isinstance(part, ToolReturnPart):
                if not part.tool_call_id:
                    return None, None, None
                returns.add(part.tool_call_id)

    pending_names = {
        tool_name for call_id, tool_name in calls.items() if call_id not in returns
    }
    return (
        bool(pending_names),
        "delegate_agents" in pending_names,
        "sleep_until" in pending_names,
    )


def summarize_checkpoint(row: AgentRunCheckpoint) -> AgentCheckpointSummary:
    """Project one checkpoint row to bounded metadata (keys only)."""
    state = row.state or {}
    messages = state.get("messages")
    pending_tools, pending_join, pending_timer = _checkpoint_pending_state(state)
    return AgentCheckpointSummary(
        run_id=row.run_id,
        sequence=row.sequence,
        format_version=row.format_version,
        attempt=row.attempt or 0,
        created_at=row.created_at,
        message_count=len(messages) if isinstance(messages, list) else None,
        has_pending_tool_calls=pending_tools,
        has_pending_join=pending_join,
        has_pending_timer=pending_timer,
    )


async def get_checkpoints(
    session: AsyncSession,
    run_id: UUID,
    user: UserPrincipal,
    *,
    limit: int = DEFAULT_PAGE_LIMIT,
    cursor: str | None = None,
) -> AgentCheckpointPage:
    """Cursor-page checkpoint summaries for ``run_id``, oldest first."""
    requested = await _visible_run(session, run_id, user)
    limit = clamp_limit(limit, MAX_CHECKPOINT_LIMIT)

    after_sequence = 0
    if cursor is not None:
        try:
            after_sequence = decode_debug_cursor(cursor, requested.id)
        except ValueError as exc:
            raise ValueError("cursor is invalid") from exc

    stmt = (
        select(AgentRunCheckpoint)
        .where(
            AgentRunCheckpoint.run_id == requested.id,
            AgentRunCheckpoint.sequence > after_sequence,
        )
        .order_by(AgentRunCheckpoint.sequence)
        .limit(limit + 1)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        next_cursor = encode_debug_cursor(requested.id, rows[-1].sequence)

    return AgentCheckpointPage(
        run_id=requested.id,
        checkpoints=[summarize_checkpoint(row) for row in rows],
        next_cursor=next_cursor,
    )
