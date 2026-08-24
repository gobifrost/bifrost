"""Forward migration coverage for persisted Agent MCP tool identifiers."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_agent_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "agent_mcp_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("list_agents", "bifrost_list_agents"),
    ("get_agent", "bifrost_get_agent"),
    ("create_agent", "bifrost_create_agent"),
    ("update_agent", "bifrost_update_agent"),
    ("delete_agent", "bifrost_delete_agent"),
]


def test_upgrade_renames_all_persisted_agent_crud_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES


def test_downgrade_reverses_agent_crud_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in EXPECTED_RENAMES]


def test_replace_updates_only_exact_array_members(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._replace("get_agent", "bifrost_get_agent")

    assert statements == [
        "UPDATE agents "
        "SET system_tools = array_replace(system_tools, 'get_agent', "
        "'bifrost_get_agent') "
        "WHERE 'get_agent' = ANY(system_tools)"
    ]
