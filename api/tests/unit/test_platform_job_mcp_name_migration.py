"""Forward migration coverage for the persisted platform-job MCP tool ID."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_platform_job_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "platform_job_mcp_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_upgrade_renames_the_app_scoped_name_to_the_canonical_one(monkeypatch) -> None:
    agent_calls: list[tuple[str, str]] = []
    config_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION,
        "_replace_agent_tool",
        lambda old, new: agent_calls.append((old, new)),
    )
    monkeypatch.setattr(
        MIGRATION,
        "_replace_mcp_config_tool",
        lambda old, new: config_calls.append((old, new)),
    )

    MIGRATION.upgrade()

    expected = [("bifrost_get_app_publish_status", "bifrost_get_platform_job")]
    assert agent_calls == expected
    assert config_calls == expected


def test_downgrade_restores_the_previous_name_in_both_locations(monkeypatch) -> None:
    agent_calls: list[tuple[str, str]] = []
    config_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION,
        "_replace_agent_tool",
        lambda old, new: agent_calls.append((old, new)),
    )
    monkeypatch.setattr(
        MIGRATION,
        "_replace_mcp_config_tool",
        lambda old, new: config_calls.append((old, new)),
    )

    MIGRATION.downgrade()

    expected = [("bifrost_get_platform_job", "bifrost_get_app_publish_status")]
    assert agent_calls == expected
    assert config_calls == expected
