"""Forward migration coverage for the persisted Knowledge MCP tool ID."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_knowledge_mcp_tool_name.py"
)
SPEC = importlib.util.spec_from_file_location("knowledge_mcp_name_migration", MIGRATION_PATH)
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

    expected = [("search_knowledge", "bifrost_search_knowledge")]
    assert agent_calls == expected
    assert config_calls == expected


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

    expected = [("bifrost_search_knowledge", "search_knowledge")]
    assert agent_calls == expected
    assert config_calls == expected


def test_config_rewrite_targets_only_mcp_server_config(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._replace_mcp_config_tool(
        "search_knowledge",
        "bifrost_search_knowledge",
    )

    assert len(statements) == 1
    statement = statements[0]
    assert "category = 'mcp'" in statement
    assert "key = 'server_config'" in statement
    assert '"search_knowledge"' in statement
    assert '"bifrost_search_knowledge"' in statement
