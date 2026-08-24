"""Forward migration coverage for persisted Role MCP tool IDs."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_role_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location("role_mcp_name_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_upgrade_preserves_agent_and_platform_mcp_configuration(monkeypatch) -> None:
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

    assert agent_calls == list(MIGRATION.RENAMES)
    assert config_calls == list(MIGRATION.RENAMES)


def test_downgrade_reverses_both_persisted_locations(monkeypatch) -> None:
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

    expected = [(new, old) for old, new in reversed(MIGRATION.RENAMES)]
    assert agent_calls == expected
    assert config_calls == expected


def test_config_rewrite_targets_only_mcp_server_config(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._replace_mcp_config_tool("list_roles", "bifrost_list_roles")

    assert len(statements) == 1
    statement = statements[0]
    assert "category = 'mcp'" in statement
    assert "key = 'server_config'" in statement
    assert '"list_roles"' in statement
    assert '"bifrost_list_roles"' in statement
