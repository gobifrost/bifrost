"""Forward migration coverage for persisted execution-history MCP tool IDs."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_execution_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location("execution_mcp_name_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("list_executions", "bifrost_list_workflow_executions"),
    ("get_execution", "bifrost_get_workflow_execution"),
]


def test_upgrade_renames_persisted_execution_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(MIGRATION, "_replace", lambda old, new: calls.append((old, new)))

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES


def test_downgrade_reverses_execution_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(MIGRATION, "_replace", lambda old, new: calls.append((old, new)))

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in EXPECTED_RENAMES]
