"""Forward migration coverage for persisted Organization MCP tool identifiers."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260817_organization_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "organization_mcp_name_migration",
    MIGRATION_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("list_organizations", "bifrost_list_organizations"),
    ("get_organization", "bifrost_get_organization"),
    ("create_organization", "bifrost_create_organization"),
    ("update_organization", "bifrost_update_organization"),
    ("delete_organization", "bifrost_delete_organization"),
]


def test_upgrade_renames_all_persisted_organization_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION,
        "_replace",
        lambda old, new: calls.append((old, new)),
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES


def test_downgrade_reverses_organization_tool_names(monkeypatch) -> None:
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

    MIGRATION._replace("get_organization", "bifrost_get_organization")

    assert statements == [
        "UPDATE agents "
        "SET system_tools = array_replace(system_tools, 'get_organization', "
        "'bifrost_get_organization') "
        "WHERE 'get_organization' = ANY(system_tools)"
    ]
