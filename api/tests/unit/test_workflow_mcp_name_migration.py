"""Forward migration coverage for persisted Workflow MCP tool identifiers."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_workflow_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location("workflow_mcp_name_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("list_workflows", "bifrost_list_workflows"),
    ("get_workflow", "bifrost_get_workflow"),
    ("validate_workflow", "bifrost_validate_workflow"),
    ("register_workflow", "bifrost_register_workflow"),
    ("execute_workflow", "bifrost_execute_workflow"),
    ("update_workflow", "bifrost_update_workflow"),
    ("delete_workflow", "bifrost_delete_workflow"),
    ("grant_workflow_role", "bifrost_grant_workflow_role"),
    ("revoke_workflow_role", "bifrost_revoke_workflow_role"),
]


def test_upgrade_renames_all_persisted_workflow_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES


def test_downgrade_reverses_workflow_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in EXPECTED_RENAMES]


def test_replace_updates_only_exact_array_members(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION._replace("get_workflow", "bifrost_get_workflow")

    assert statements == [
        "UPDATE agents "
        "SET system_tools = array_replace(system_tools, 'get_workflow', "
        "'bifrost_get_workflow') "
        "WHERE 'get_workflow' = ANY(system_tools)"
    ]
