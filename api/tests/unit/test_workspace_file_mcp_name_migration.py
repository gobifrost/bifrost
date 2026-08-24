"""Forward migration coverage for persisted Workspace File MCP tool IDs."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_workspace_file_mcp_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "workspace_file_mcp_name_migration",
    MIGRATION_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_UPGRADE_RENAMES = [
    ("list_content", "bifrost_list_files"),
    ("search_content", "bifrost_search_files"),
    ("read_content_lines", "bifrost_read_file"),
    ("get_content", "bifrost_read_file"),
    ("patch_content", "bifrost_patch_file"),
    ("replace_content", "bifrost_write_file"),
    ("push_files", "bifrost_write_file"),
    ("delete_content", "bifrost_delete_file"),
]


def test_upgrade_renames_and_deduplicates_workspace_file_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    deduplicated: list[bool] = []
    monkeypatch.setattr(
        MIGRATION,
        "_replace",
        lambda old, new: calls.append((old, new)),
    )
    monkeypatch.setattr(
        MIGRATION,
        "_deduplicate_changed_arrays",
        lambda: deduplicated.append(True),
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_UPGRADE_RENAMES
    assert deduplicated == [True]


def test_downgrade_uses_one_legacy_read_and_write_name(monkeypatch) -> None:
    renames: list[tuple[str, str]] = []
    removals: list[str] = []
    monkeypatch.setattr(
        MIGRATION,
        "_replace",
        lambda old, new: renames.append((old, new)),
    )
    monkeypatch.setattr(MIGRATION, "_remove", removals.append)

    MIGRATION.downgrade()

    assert renames == [
        ("bifrost_list_files", "list_content"),
        ("bifrost_search_files", "search_content"),
        ("bifrost_read_file", "get_content"),
        ("bifrost_patch_file", "patch_content"),
        ("bifrost_write_file", "replace_content"),
        ("bifrost_delete_file", "delete_content"),
    ]
    assert removals == ["bifrost_stat_file", "bifrost_exists_file"]


def test_deduplication_preserves_first_tool_position(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._deduplicate_changed_arrays()

    assert len(statements) == 1
    statement = statements[0]
    assert "WITH ORDINALITY" in statement
    assert "min(expanded.position)" in statement
    assert "ORDER BY item.first_position" in statement
