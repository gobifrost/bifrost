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
import logging
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

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
        .order_by(AgentRun.created_at, AgentRun.id)
    )
    rows = list(result.scalars().unique().all())
    by_id = {row.id: row for row in rows}
    if root_id not in by_id:
        # The root itself is invisible: reveal nothing about the tree.
        raise DebuggerNotFoundError(f"Agent run {run_id} not found")

    children: dict[UUID, list[AgentRun]] = {row.id: [] for row in rows}
    for row in rows:
        parent_id = row.parent_run_id
        if parent_id is not None and parent_id in by_id and parent_id != row.id:
            children[parent_id].append(row)

    truncated = len(rows) >= MAX_TREE_NODES

    def build(node_run: AgentRun, depth: int, path: set[UUID]) -> AgentRunTreeNode:
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
            node.diagnostic = "maximum tree depth reached; subtree truncated"
            return node
        path = path | {node_run.id}
        for child in children.get(node_run.id, []):
            if child.id in path:
                node.children.append(
                    AgentRunTreeNode(
                        run_id=child.id,
                        agent_id=child.agent_id,
                        agent_name=(
                            child.agent.name if child.agent else None
                        ),
                        status=child.status,
                        parent_run_id=child.parent_run_id,
                        depth=depth + 1,
                        attempt=child.attempt or 0,
                        created_at=child.created_at,
                        completed_at=child.completed_at,
                        diagnostic=(
                            "parent cycle detected; "
                            "subtree truncated to break the loop"
                        ),
                    )
                )
                continue
            node.children.append(build(child, depth + 1, path))
        return node

    # Visible runs whose parent is invisible (or self-parented) attach under
    # the root so no visible run silently disappears from the tree.
    root = by_id[root_id]
    extra_roots = [
        row
        for row in rows
        if row.id != root_id
        and (
            row.parent_run_id is None
            or row.parent_run_id not in by_id
            or row.parent_run_id == row.id
        )
    ]
    root_node = build(root, 0, set())
    for orphan in extra_roots:
        if orphan.parent_run_id == orphan.id:
            root_node.children.append(
                AgentRunTreeNode(
                    run_id=orphan.id,
                    agent_id=orphan.agent_id,
                    agent_name=(
                        orphan.agent.name if orphan.agent else None
                    ),
                    status=orphan.status,
                    parent_run_id=orphan.parent_run_id,
                    depth=1,
                    attempt=orphan.attempt or 0,
                    created_at=orphan.created_at,
                    completed_at=orphan.completed_at,
                    diagnostic=(
                        "run lists itself as its own parent; "
                        "shown here instead of recursing"
                    ),
                )
            )
        else:
            root_node.children.append(build(orphan, 1, {root_id}))

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
    scope_run_id: UUID, run_id: UUID, sequence: int, created_at: datetime
) -> str:
    """Encode an opaque timeline cursor.

    ``scope_run_id`` is the requested run the page belongs to (binds the
    cursor to its scope); ``(run_id, sequence, created_at)`` is the position
    of the last returned entry and yields a total order across descendants.
    """
    payload = (
        f"{scope_run_id}:{run_id}:{sequence}:{created_at.isoformat()}"
    ).encode()
    return urlsafe_b64encode(payload).decode().rstrip("=")


def decode_timeline_cursor(cursor: str, scope_run_id: UUID) -> tuple[
    UUID | None,
    int,
    datetime | None,
]:
    """Decode a cursor into ``(last_run_id, last_sequence, last_created_at)``.

    Raises ``ValueError`` for malformed cursors or cursors bound to a
    different scope run.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        scope_raw, run_raw, seq_raw, created_raw = (
            urlsafe_b64decode(padded).decode().split(":", 3)
        )
        if UUID(scope_raw) != scope_run_id:
            raise ValueError("cursor is invalid")
        return UUID(run_raw), int(seq_raw), datetime.fromisoformat(created_raw)
    except (ValueError, BinasciiError) as exc:
        raise ValueError("cursor is invalid") from exc


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
        root_id = requested.root_run_id or requested.id
        desc_result = await session.execute(
            select(AgentRun)
            .options(joinedload(AgentRun.agent))
            .where(
                (AgentRun.id == root_id) | (AgentRun.root_run_id == root_id),
                *agent_run_visibility_conditions(user),
            )
        )
        for row in desc_result.scalars().unique().all():
            scope_ids.add(row.id)
            run_meta[row.id] = row

    after_run: UUID | None = None
    after_sequence = 0
    after_created: datetime | None = None
    if cursor is not None:
        after_run, after_sequence, after_created = decode_timeline_cursor(
            cursor, requested.id
        )

    stmt = select(AgentRunJournalEntry).where(
        AgentRunJournalEntry.run_id.in_(scope_ids)
    )
    if kind is not None:
        stmt = stmt.where(AgentRunJournalEntry.kind == kind)
    if attempt is not None:
        stmt = stmt.where(
            func.coalesce(
                (AgentRunJournalEntry.data.op("->>")("attempt")), ""
            )
            == str(attempt)
        )
    if after_created is not None and include_descendants:
        stmt = stmt.where(
            (
                AgentRunJournalEntry.created_at,
                AgentRunJournalEntry.run_id,
                AgentRunJournalEntry.sequence,
            )
            > (after_created, after_run, after_sequence)
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

    entries = list((await session.execute(stmt)).scalars().all())
    has_more = len(entries) > limit
    entries = entries[:limit]

    # Descendant runs referenced by delegation entries may not be visible;
    # resolve child metadata only inside the visible scope.
    visible_children = scope_ids | {entry.run_id for entry in entries}

    projected: list[AgentTimelineEntry] = []
    for entry in entries:
        meta = run_meta.get(entry.run_id)
        data = entry.data or {}
        child_run_id = None
        if isinstance(data.get("child_run_id"), str):
            try:
                candidate = UUID(str(data["child_run_id"]))
            except ValueError:
                candidate = None
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
                attempt=(
                    int(data["attempt"])
                    if isinstance(data.get("attempt"), int)
                    else None
                ),
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
                summary=summarize_entry(entry.kind, data),
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
            requested.id, last.run_id, last.sequence, last.created_at
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
        agent_id=run.agent_id,
        agent_name=run.agent.name if run.agent else None,
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
        correlation={str(k): _redact_any(v) for k, v in correlation.items()},
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


def summarize_checkpoint(row: AgentRunCheckpoint) -> AgentCheckpointSummary:
    """Project one checkpoint row to bounded metadata (keys only)."""
    state = row.state or {}
    messages = state.get("messages")
    pending_calls = state.get("pending_tool_calls")
    return AgentCheckpointSummary(
        run_id=row.run_id,
        sequence=row.sequence,
        format_version=row.format_version,
        attempt=row.attempt or 0,
        created_at=row.created_at,
        message_count=len(messages) if isinstance(messages, list) else None,
        has_pending_tool_calls=(
            bool(pending_calls) if pending_calls is not None else None
        ),
        has_pending_join=(
            ("pending_join" in state or "join" in state)
            if isinstance(state, dict)
            else None
        ),
        has_pending_timer=(
            ("pending_timer" in state or "wake_at" in state)
            if isinstance(state, dict)
            else None
        ),
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

