"""Forward migration coverage for persisted Config MCP tool IDs."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260819_config_mcp_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "config_mcp_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


EXPECTED = [
    ("list_configs", "bifrost_list_configs"),
    ("get_config", "bifrost_get_config"),
    ("create_config", "bifrost_create_config"),
    ("update_config", "bifrost_update_config"),
    ("delete_config", "bifrost_delete_config"),
]


def test_revision_chains_to_the_current_head() -> None:
    """The chain must be linear; a wrong parent silently forks the history."""
    assert MIGRATION.revision == "20260819_config_mcp_names"
    assert MIGRATION.down_revision == "20260818_file_policy_names"


def test_revision_id_fits_the_alembic_version_column() -> None:
    """``alembic_version.version_num`` is varchar(32)."""
    assert len(MIGRATION.revision) <= 32


def test_upgrade_renames_every_config_tool_to_the_canonical_name(monkeypatch) -> None:
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

    assert agent_calls == EXPECTED
    assert config_calls == EXPECTED


def test_downgrade_restores_every_previous_name_in_both_locations(monkeypatch) -> None:
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

    expected_reversed = [(new, old) for old, new in reversed(EXPECTED)]
    assert agent_calls == expected_reversed
    assert config_calls == expected_reversed


def test_renaming_get_config_cannot_corrupt_an_already_renamed_tool() -> None:
    """``get_config`` is a substring of ``bifrost_get_config``.

    The JSON rewrite matches the quoted token, so a tool renamed earlier in the
    same pass is not re-matched and mangled into ``bifrost_bifrost_get_config``.
    """
    quoted_old = '"get_config"'
    already_renamed = '"bifrost_get_config"'
    assert quoted_old not in already_renamed
