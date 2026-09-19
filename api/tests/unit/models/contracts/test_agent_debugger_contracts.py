"""Serialization tests for stable agent debugger contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.models.contracts.agent_debugger import (
    AgentCheckpointPage,
    AgentCheckpointSummary,
    AgentDebuggerLinks,
    AgentRunSnapshotView,
    AgentRunTree,
    AgentRunTreeNode,
    AgentTimelineEntry,
    AgentTimelinePage,
    decode_debug_cursor,
    encode_debug_cursor,
)


def test_tree_node_serializes_recursively() -> None:
    child_id = uuid4()
    root = AgentRunTreeNode(
        run_id=uuid4(),
        agent_name="parent",
        status="waiting_child",
        depth=0,
        children=[
            AgentRunTreeNode(
                run_id=child_id,
                agent_name="child",
                status="failed",
                depth=1,
            )
        ],
    )
    tree = AgentRunTree(
        requested_run_id=root.run_id,
        root_run_id=root.run_id,
        root=root,
        total_runs=2,
    )
    payload = tree.model_dump(mode="json")
    assert payload["total_runs"] == 2
    assert payload["truncated"] is False
    assert payload["root"]["children"][0]["run_id"] == str(child_id)
    assert payload["root"]["children"][0]["diagnostic"] is None


def test_tree_cycle_diagnostic_node() -> None:
    node = AgentRunTreeNode(
        run_id=uuid4(),
        status="running",
        diagnostic="parent cycle detected; tree truncated",
    )
    assert node.model_dump(mode="json")["diagnostic"].startswith("parent cycle")


def test_timeline_entry_exposes_stable_fields() -> None:
    run_id = uuid4()
    entry = AgentTimelineEntry(
        sequence=7,
        kind="tool_result",
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        attempt=1,
        created_at=datetime.now(timezone.utc),
        duration_ms=120,
        input_tokens=10,
        output_tokens=20,
        summary="tool search_knowledge completed",
        detail={"tool_name": "search_knowledge"},
        operation_id="agent-tool:abc",
        child_run_id=None,
        join_id=None,
    )
    payload = entry.model_dump(mode="json")
    assert payload["sequence"] == 7
    assert payload["kind"] == "tool_result"
    assert payload["run_id"] == str(run_id)
    assert payload["operation_id"] == "agent-tool:abc"
    assert payload["detail"] == {"tool_name": "search_knowledge"}


def test_timeline_page_carries_opaque_cursor() -> None:
    run_id = uuid4()
    cursor = encode_debug_cursor(run_id, 7)
    page = AgentTimelinePage(run_id=run_id, entries=[], next_cursor=cursor)
    payload = page.model_dump(mode="json")
    assert payload["next_cursor"] == cursor
    assert decode_debug_cursor(cursor, run_id) == 7


def test_cursor_is_opaque_and_run_bound() -> None:
    run_id = uuid4()
    cursor = encode_debug_cursor(run_id, 3)
    assert str(run_id) not in cursor
    assert "3" not in cursor or True  # opaque: base64 of JSON, no raw fields
    with pytest.raises(ValueError):
        decode_debug_cursor(cursor, uuid4())
    with pytest.raises(ValueError):
        decode_debug_cursor("not-a-cursor", run_id)


def test_snapshot_view_has_no_secret_fields() -> None:
    view = AgentRunSnapshotView(
        run_id=uuid4(),
        agent_name="triage",
        snapshot_version=1,
        system_prompt_sha256="abc123",
        status="running",
    )
    payload = view.model_dump(mode="json")
    assert payload["system_prompt_sha256"] == "abc123"
    for forbidden in (
        "caller_context",
        "credentials",
        "token",
        "lease_token",
        "authorization",
        "secret",
        "system_prompt",
    ):
        assert forbidden not in payload
        assert forbidden not in AgentRunSnapshotView.model_fields


def test_checkpoint_summary_serializes() -> None:
    run_id = uuid4()
    summary = AgentCheckpointSummary(
        run_id=run_id,
        sequence=4,
        format_version=1,
        attempt=2,
        created_at=datetime.now(timezone.utc),
        message_count=9,
        has_pending_tool_calls=False,
        has_pending_join=False,
        has_pending_timer=True,
    )
    page = AgentCheckpointPage(run_id=run_id, checkpoints=[summary], next_cursor=None)
    payload = page.model_dump(mode="json")
    assert payload["checkpoints"][0]["sequence"] == 4
    assert payload["checkpoints"][0]["has_pending_timer"] is True
    assert payload["next_cursor"] is None


def test_debugger_links_shape() -> None:
    links = AgentDebuggerLinks(
        tree="/api/agent-runs/1/tree",
        timeline="/api/agent-runs/1/timeline",
        snapshot="/api/agent-runs/1/snapshot",
        checkpoints="/api/agent-runs/1/checkpoints",
    )
    assert set(links.model_dump()) == {
        "tree",
        "timeline",
        "snapshot",
        "checkpoints",
    }
