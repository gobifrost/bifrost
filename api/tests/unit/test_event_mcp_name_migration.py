"""Forward migration coverage for persisted Event MCP tool identifiers."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_event_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location("event_mcp_name_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("list_event_sources", "bifrost_list_event_sources"),
    ("get_event_source", "bifrost_get_event_source"),
    ("create_event_source", "bifrost_create_event_source"),
    ("update_event_source", "bifrost_update_event_source"),
    ("delete_event_source", "bifrost_delete_event_source"),
    ("list_event_subscriptions", "bifrost_list_event_subscriptions"),
    ("create_event_subscription", "bifrost_create_event_subscription"),
    ("update_event_subscription", "bifrost_update_event_subscription"),
    ("delete_event_subscription", "bifrost_delete_event_subscription"),
    ("list_webhook_adapters", "bifrost_list_event_webhook_adapters"),
]


def test_upgrade_renames_all_persisted_event_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES


def test_downgrade_reverses_event_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in EXPECTED_RENAMES]


def test_replace_updates_only_exact_array_members(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._replace("get_event_source", "bifrost_get_event_source")

    assert statements == [
        "UPDATE agents "
        "SET system_tools = array_replace(system_tools, 'get_event_source', "
        "'bifrost_get_event_source') "
        "WHERE 'get_event_source' = ANY(system_tools)"
    ]
