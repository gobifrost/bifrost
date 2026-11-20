"""Stored documentation assignments and allow/block policy survive the rename."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.agents import Agent
from src.models.orm.config import SystemConfig

SPEC = importlib.util.spec_from_file_location(
    "docs_mcp_name_migration",
    Path(__file__).resolve().parents[2] / "alembic/versions/20261114_docs_mcp_name.py",
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


@pytest.mark.parametrize("filters,expected_filters", [
    (
        {"allowed_tool_ids": ["get_docs", "bifrost_execute_code"], "blocked_tool_ids": ["get_docs"]},
        {"allowed_tool_ids": ["bifrost_get_docs", "bifrost_execute_code"], "blocked_tool_ids": ["bifrost_get_docs"]},
    ),
    ({"allowed_tool_ids": None, "blocked_tool_ids": []}, {"allowed_tool_ids": None, "blocked_tool_ids": []}),
    ({}, {}),
])
async def test_upgrade_and_downgrade_preserve_assignments_and_filter_policy(
    db_session, monkeypatch, filters, expected_filters,
):
    await db_session.execute(delete(SystemConfig).where(
        SystemConfig.category == "mcp", SystemConfig.key == "server_config",
        SystemConfig.organization_id.is_(None),
    ))
    agent_id = uuid4()
    agent = Agent(
        id=agent_id, name=f"docs-rename-{agent_id}", system_prompt="Use the docs.",
        system_tools=["get_docs", "bifrost_execution_get"], created_by="test",
    )
    original = {"enabled": False, **filters, "configured_by": "operator"}
    config = SystemConfig(
        category="mcp", key="server_config", value_json=original,
        created_by="test", updated_by="test",
    )
    db_session.add_all([agent, config])
    await db_session.flush()
    statements = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION.upgrade()
    for statement in statements:
        await db_session.execute(statement)
    stored_tools = await db_session.scalar(select(Agent.system_tools).where(Agent.id == agent_id))
    stored_config = await db_session.scalar(select(SystemConfig.value_json).where(SystemConfig.id == config.id))
    assert stored_tools == ["bifrost_get_docs", "bifrost_execution_get"]
    assert stored_config == {"enabled": False, **expected_filters, "configured_by": "operator"}

    statements.clear()
    MIGRATION.downgrade()
    for statement in statements:
        await db_session.execute(statement)
    assert await db_session.scalar(select(Agent.system_tools).where(Agent.id == agent_id)) == [
        "get_docs", "bifrost_execution_get",
    ]
    assert await db_session.scalar(select(SystemConfig.value_json).where(SystemConfig.id == config.id)) == original
