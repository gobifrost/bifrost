"""Agent debugger contract models.

Stable, versioned read surface over the durable agent runtime journal.
Backend and CLI use the same run, checkpoint, tool, and completion
vocabulary (spec R3 — stable language).

V1 is inspection-only: tree, timeline, snapshot, and checkpoint reads.
Historical fork/replay is explicitly out of scope and has no contract here.

Security: no model in this module carries ``caller_context``, credentials,
authorization tokens, lease tokens, or unredacted secret-bearing values.
Snapshots expose configuration identity and hashes, never secrets.
Cursors are opaque and stable for ``(run_id, sequence)`` pagination.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# -----------------------------------------------------------------------------
# Opaque cursor helpers (stable for ``(run_id, sequence)`` pagination)
# -----------------------------------------------------------------------------


def encode_debug_cursor(run_id: UUID, sequence: int) -> str:
    """Encode an opaque page cursor for ``(run_id, sequence)`` pagination."""
    payload = json.dumps({"run_id": str(run_id), "sequence": sequence}).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_debug_cursor(cursor: str, expected_run_id: UUID) -> int:
    """Decode a page cursor, returning the ``after_sequence`` position.

    Raises ``ValueError`` when the cursor is malformed or bound to a
    different run. Binding the cursor to the run keeps pages stable and
    prevents cross-run cursor reuse from skipping or duplicating entries.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        cursor_run_id = UUID(str(payload["run_id"]))
        sequence = int(payload["sequence"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise ValueError("cursor is invalid") from exc
    if cursor_run_id != expected_run_id:
        raise ValueError("cursor is invalid")
    if sequence < 0:
        raise ValueError("cursor is invalid")
    return sequence


# -----------------------------------------------------------------------------
# Run tree
# -----------------------------------------------------------------------------


class AgentRunTreeNode(BaseModel):
    """One run in a delegation tree. Recursive via ``children``."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    agent_id: UUID | None = None
    agent_name: str | None = None
    status: str
    parent_run_id: UUID | None = None
    depth: int = 0
    attempt: int = 0
    created_at: datetime | None = None
    completed_at: datetime | None = None
    # Set only on the bounded diagnostic node emitted when a corrupt parent
    # cycle is detected; the tree never recurses forever.
    diagnostic: str | None = None
    children: list[AgentRunTreeNode] = Field(default_factory=list)


class AgentRunTree(BaseModel):
    """Delegation tree rooted at the requested run's root run."""

    requested_run_id: UUID
    root_run_id: UUID
    root: AgentRunTreeNode
    total_runs: int
    # True when depth capping cut the tree; join the timeline for the rest.
    truncated: bool = False


# -----------------------------------------------------------------------------
# Journal timeline (the journal is the canonical timeline)
# -----------------------------------------------------------------------------


class AgentTimelineEntry(BaseModel):
    """One ordered journal event projected for debugging."""

    model_config = ConfigDict(from_attributes=True)

    sequence: int
    kind: str
    run_id: UUID
    root_run_id: UUID | None = None
    parent_run_id: UUID | None = None
    attempt: int | None = None
    created_at: datetime
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # One-line human-readable description of the event.
    summary: str
    # Redacted event payload (allowlisted keys only; secret-bearing values
    # replaced with ``[REDACTED]``).
    detail: dict[str, Any] = Field(default_factory=dict)
    operation_id: str | None = None
    child_run_id: UUID | None = None
    join_id: UUID | None = None


class AgentTimelinePage(BaseModel):
    """One cursor page of the run (and optionally descendant) timeline."""

    run_id: UUID
    include_descendants: bool = False
    entries: list[AgentTimelineEntry] = Field(default_factory=list)
    next_cursor: str | None = None


# -----------------------------------------------------------------------------
# Execution snapshot view (identity and hashes, never secrets)
# -----------------------------------------------------------------------------


class AgentSnapshotModel(BaseModel):
    """Model identity pinned at enqueue. No credentials travel here."""

    profile_id: str | None = None
    provider: str | None = None
    model: str | None = None
    llm_max_tokens: int | None = None


class AgentSnapshotLease(BaseModel):
    """Crash-detection lease state. The lease token is never exposed."""

    owner: str | None = None
    expires_at: datetime | None = None
    last_progress_at: datetime | None = None


class AgentSnapshotContract(BaseModel):
    """Invocation-owned output-contract outcome."""

    valid: bool | None = None
    errors: list[str] | None = None


class AgentSnapshotCompletionEvent(BaseModel):
    """At-least-once terminal-event publication state."""

    pending_at: datetime | None = None
    emitted_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None


class AgentRunSnapshotView(BaseModel):
    """Immutable configuration identity plus live lifecycle/lease state.

    Never returns ``caller_context``, decrypted credentials, stored
    authorization tokens, lease tokens, or unredacted secret-bearing tool
    arguments. The system prompt is identified by hash, not content.
    """

    run_id: UUID
    agent_id: UUID | None = None
    agent_name: str | None = None
    snapshot_version: int | None = None
    agent_updated_at: datetime | None = None
    system_prompt_sha256: str | None = None
    model: AgentSnapshotModel = Field(default_factory=AgentSnapshotModel)
    tool_names: list[str] = Field(default_factory=list)
    delegated_agents: list[dict[str, str]] = Field(default_factory=list)
    system_tools: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    correlation: dict[str, Any] = Field(default_factory=dict)
    status: str
    attempt: int = 0
    checkpoint_sequence: int = 0
    wake_at: datetime | None = None
    lease: AgentSnapshotLease = Field(default_factory=AgentSnapshotLease)
    usage: dict[str, Any] = Field(default_factory=dict)
    contract: AgentSnapshotContract = Field(
        default_factory=AgentSnapshotContract
    )
    completion_event: AgentSnapshotCompletionEvent = Field(
        default_factory=AgentSnapshotCompletionEvent
    )


# -----------------------------------------------------------------------------
# Checkpoints
# -----------------------------------------------------------------------------


class AgentCheckpointSummary(BaseModel):
    """Bounded checkpoint metadata. State payloads stay server-side."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    sequence: int
    format_version: int
    attempt: int
    created_at: datetime
    # Derived shape hints from the stored state (keys only, no content).
    message_count: int | None = None
    has_pending_tool_calls: bool | None = None
    has_pending_join: bool | None = None
    has_pending_timer: bool | None = None


class AgentCheckpointPage(BaseModel):
    """One cursor page of checkpoint summaries, oldest first."""

    run_id: UUID
    checkpoints: list[AgentCheckpointSummary] = Field(default_factory=list)
    next_cursor: str | None = None


# -----------------------------------------------------------------------------
# Shared navigation links (attached additively to run detail responses)
# -----------------------------------------------------------------------------


class AgentDebuggerLinks(BaseModel):
    """Relative debugger URLs for a run. Additive navigation only."""

    tree: str
    timeline: str
    snapshot: str
    checkpoints: str
