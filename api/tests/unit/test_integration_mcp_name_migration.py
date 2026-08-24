"""Forward migration coverage for persisted Integration MCP tool identifiers."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_integration_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "integration_mcp_name_migration",
    MIGRATION_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("list_integrations", "bifrost_list_integrations"),
    ("get_integration", "bifrost_get_integration"),
    ("create_integration", "bifrost_create_integration"),
    ("update_integration", "bifrost_update_integration"),
    ("add_integration_mapping", "bifrost_create_integration_mapping"),
    ("update_integration_mapping", "bifrost_update_integration_mapping"),
]


def test_upgrade_renames_all_persisted_integration_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION,
        "_replace",
        lambda old, new: calls.append((old, new)),
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES


def test_downgrade_reverses_integration_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION,
        "_replace",
        lambda old, new: calls.append((old, new)),
    )

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in EXPECTED_RENAMES]


def test_replace_updates_only_exact_array_members(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._replace("get_integration", "bifrost_get_integration")

    assert statements == [
        "UPDATE agents "
        "SET system_tools = array_replace(system_tools, 'get_integration', "
        "'bifrost_get_integration') "
        "WHERE 'get_integration' = ANY(system_tools)"
    ]
