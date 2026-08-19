"""Forward migration coverage for the Agent Skill file-reader tool rename."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260819_skill_file_tool_name.py"
)
SPEC = importlib.util.spec_from_file_location(
    "skill_file_tool_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


EXPECTED = [("read_skill_asset", "bifrost_read_agent_skill_file")]


def test_revision_chains_to_the_skill_revision_migration() -> None:
    assert MIGRATION.revision == "20260819_skill_file_tool"
    assert MIGRATION.down_revision == "20260819_agent_skill_rev"


def test_revision_id_fits_the_alembic_version_column() -> None:
    assert len(MIGRATION.revision) <= 32


def test_upgrade_renames_in_both_locations(monkeypatch) -> None:
    agent_calls: list[tuple[str, str]] = []
    config_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace_agent_tool", lambda o, n: agent_calls.append((o, n))
    )
    monkeypatch.setattr(
        MIGRATION, "_replace_mcp_config_tool", lambda o, n: config_calls.append((o, n))
    )

    MIGRATION.upgrade()

    assert agent_calls == EXPECTED
    assert config_calls == EXPECTED


def test_downgrade_restores_the_previous_name(monkeypatch) -> None:
    agent_calls: list[tuple[str, str]] = []
    config_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace_agent_tool", lambda o, n: agent_calls.append((o, n))
    )
    monkeypatch.setattr(
        MIGRATION, "_replace_mcp_config_tool", lambda o, n: config_calls.append((o, n))
    )

    MIGRATION.downgrade()

    assert agent_calls == [(new, old) for old, new in reversed(EXPECTED)]
    assert config_calls == [(new, old) for old, new in reversed(EXPECTED)]


def test_the_runtime_constant_matches_the_migration_target() -> None:
    """The migration must rename to whatever the code actually registers.

    A drift here would leave persisted grants pointing at a tool id no runtime
    serves, which fails silently rather than loudly.
    """
    from src.services.builder.workspace_tool_runtime import (
        READ_SKILL_ASSET_TOOL_ID as builder_id,
    )
    from src.services.mcp_server.tools.skill_assets import (
        READ_SKILL_ASSET_TOOL_ID as mcp_id,
    )

    target = EXPECTED[0][1]
    assert mcp_id == target
    assert builder_id == target, "Builder and MCP must agree on the tool id"
