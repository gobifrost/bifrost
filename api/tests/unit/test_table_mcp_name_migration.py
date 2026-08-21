"""Forward migration coverage for persisted Table MCP tool identifiers."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_table_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location("table_mcp_name_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("list_tables", "bifrost_list_tables"),
    ("get_table", "bifrost_get_table"),
    ("create_table", "bifrost_create_table"),
    ("update_table", "bifrost_update_table"),
    ("delete_table", "bifrost_delete_table"),
]


def test_upgrade_renames_all_persisted_table_crud_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    indexes: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )
    monkeypatch.setattr(
        MIGRATION.op,
        "create_index",
        lambda *args, **kwargs: indexes.append((args, kwargs)),
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES
    assert [args[0] for args, _kwargs in indexes] == [
        "ix_tables_org_name_unique",
        "ix_tables_global_name_unique",
    ]
    assert all(kwargs["unique"] is True for _args, kwargs in indexes)


def test_downgrade_reverses_table_crud_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    dropped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )
    monkeypatch.setattr(
        MIGRATION.op,
        "drop_index",
        lambda name, *, table_name: dropped.append((name, table_name)),
    )

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in EXPECTED_RENAMES]
    assert dropped == [
        ("ix_tables_global_name_unique", "tables"),
        ("ix_tables_org_name_unique", "tables"),
    ]


def test_replace_updates_only_exact_array_members(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._replace("get_table", "bifrost_get_table")

    assert statements == [
        "UPDATE agents "
        "SET system_tools = array_replace(system_tools, 'get_table', "
        "'bifrost_get_table') "
        "WHERE 'get_table' = ANY(system_tools)"
    ]
