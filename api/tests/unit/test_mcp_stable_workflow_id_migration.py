import importlib.util
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import pytest

from src.models.orm.config import SystemConfig
from src.models.orm.workflows import Workflow

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/20261021_mcp_stable_workflow_ids.py"
)
SPEC = importlib.util.spec_from_file_location("mcp_stable_workflow_ids", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_ambiguous_legacy_allow_alias_becomes_explicit_empty_denylist():
    migrated = MIGRATION._migrate_config(
        {"enabled": True, "allowed_tool_ids": ["same_name"], "blocked_tool_ids": []},
        [(str(uuid4()), "Same Name"), (str(uuid4()), "same-name")],
    )
    assert migrated["allowed_tool_ids"] == []


def test_ambiguous_legacy_block_alias_blocks_every_candidate():
    first, second = str(uuid4()), str(uuid4())
    migrated = MIGRATION._migrate_config(
        {
            "enabled": True,
            "allowed_tool_ids": None,
            "blocked_tool_ids": ["same_name_abc"],
        },
        [(first, "Same Name"), (second, "same-name")],
    )
    assert set(migrated["blocked_tool_ids"]) == {first, second}


def test_unknown_legacy_block_disables_mcp_for_administrator_review():
    migrated = MIGRATION._migrate_config(
        {
            "enabled": True,
            "allowed_tool_ids": None,
            "blocked_tool_ids": ["removed_workflow"],
        },
        [],
    )
    assert migrated["enabled"] is False
    assert migrated["blocked_tool_ids"] == ["removed_workflow"]
    assert (
        migrated["legacy_mcp_workflow_ids_review_required"]
        ["unresolved_blocked_tool_ids"]
        == ["removed_workflow"]
    )


def test_native_and_uuid_identifiers_remain_unchanged():
    workflow_id = str(uuid4())
    migrated = MIGRATION._migrate_config(
        {
            "enabled": True,
            "allowed_tool_ids": ["bifrost_workflow_list", workflow_id],
            "blocked_tool_ids": ["get_docs", workflow_id],
        },
        [],
    )
    assert migrated["allowed_tool_ids"] == ["bifrost_workflow_list", workflow_id]
    assert migrated["blocked_tool_ids"] == ["get_docs", workflow_id]


@pytest.mark.parametrize(
    "native_id",
    [
        "search_knowledge",
        "bifrost_agent_list",
        "bifrost_execution_get",
        "bifrost_platform_job_get",
    ],
)
def test_former_native_identifiers_remain_unchanged_without_live_assignments(native_id):
    migrated = MIGRATION._migrate_config(
        {
            "enabled": True,
            "allowed_tool_ids": [native_id],
            "blocked_tool_ids": [native_id],
        },
        [],
    )
    assert migrated["allowed_tool_ids"] == [native_id]
    assert migrated["blocked_tool_ids"] == [native_id]


def test_former_native_name_collision_uses_workflow_suffix_alias():
    workflow_id = str(uuid4())
    migrated = MIGRATION._migrate_config(
        {
            "enabled": True,
            "allowed_tool_ids": ["search_knowledge_workflow"],
            "blocked_tool_ids": [],
        },
        [(workflow_id, "search_knowledge")],
    )
    assert migrated["allowed_tool_ids"] == [workflow_id]


def test_discovered_system_identifier_remains_unchanged():
    migrated = MIGRATION._migrate_config(
        {
            "enabled": True,
            "allowed_tool_ids": ["contoso_lookup"],
            "blocked_tool_ids": ["contoso_lookup"],
        },
        [],
        frozenset({"contoso_lookup"}),
    )
    assert migrated["allowed_tool_ids"] == ["contoso_lookup"]
    assert migrated["blocked_tool_ids"] == ["contoso_lookup"]


@pytest.mark.asyncio
async def test_upgrade_rewrites_existing_database_configuration(db_session):
    workflow_id = uuid4()
    config = SystemConfig(
        category="mcp",
        key="server_config",
        organization_id=None,
        value_json={
            "enabled": True,
            "allowed_tool_ids": ["migration_echo"],
            "blocked_tool_ids": [],
        },
    )
    db_session.add_all([
        Workflow(
            id=workflow_id,
            name="Migration Echo",
            function_name="migration_echo",
            type="tool",
            path="workflows/migration_echo.py",
        ),
        config,
    ])
    await db_session.flush()
    connection = await db_session.connection()

    def upgrade(sync_connection):
        with patch.object(MIGRATION.op, "get_bind", return_value=sync_connection):
            MIGRATION.upgrade()

    await connection.run_sync(upgrade)
    await db_session.refresh(config)
    assert config.value_json == {
        "enabled": True,
        "allowed_tool_ids": [str(workflow_id)],
        "blocked_tool_ids": [],
    }
