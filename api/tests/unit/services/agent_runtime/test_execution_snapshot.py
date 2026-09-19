"""Unit tests for immutable execution snapshots."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.agent_runtime.execution_snapshot import (
    EXECUTION_SNAPSHOT_VERSION,
    SnapshotError,
    build_execution_snapshot,
    snapshot_tool_targets,
    validate_snapshot,
)
from src.services.llm.base import ToolDefinition


def _agent(**overrides):
    agent_id = uuid4()
    base = {
        "id": agent_id,
        "name": "Ticket Agent",
        "delegated_agents": [],
        "system_tools": ["search"],
        "knowledge_sources": [],
        "roles": [],
        "max_iterations": 25,
        "max_token_budget": 8000,
        "max_run_timeout": 600,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_snapshot_pins_prompt_model_tools_and_limits():
    tool_id = uuid4()
    snapshot = build_execution_snapshot(
        agent=_agent(),
        system_prompt="Pinned instructions.",
        llm_profile_id=None,
        llm_provider="openai",
        llm_model="gpt-5",
        llm_max_tokens=512,
        tool_definitions=[
            ToolDefinition(
                name="lookup",
                description="Look up tickets",
                parameters={"type": "object"},
            )
        ],
        tool_id_map={"lookup": tool_id},
    )
    assert snapshot["format_version"] == EXECUTION_SNAPSHOT_VERSION
    assert snapshot["system_prompt"] == "Pinned instructions."
    assert snapshot["model"]["model"] == "gpt-5"
    assert snapshot["model"]["llm_max_tokens"] == 512
    assert snapshot["tools"][0]["target_id"] == str(tool_id)
    assert snapshot["limits"] == {
        "max_iterations": 25,
        "max_token_budget": 8000,
        "max_run_timeout": 600,
    }
    assert snapshot_tool_targets(snapshot) == {"lookup": tool_id}


def test_validate_snapshot_rejects_missing_and_versioned():
    with pytest.raises(SnapshotError, match="predates durable"):
        validate_snapshot(None)
    with pytest.raises(SnapshotError, match="Unsupported execution snapshot"):
        validate_snapshot({"format_version": 999})
    snapshot = {"format_version": EXECUTION_SNAPSHOT_VERSION}
    assert validate_snapshot(snapshot) is snapshot
