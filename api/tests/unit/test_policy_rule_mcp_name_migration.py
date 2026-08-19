"""Forward migration coverage for persisted Policy Rule MCP tool IDs."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260819_policy_rule_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "policy_rule_mcp_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


EXPECTED = [
    ("list_policy_rules", "bifrost_list_policy_rules"),
    ("create_policy_rule", "bifrost_create_policy_rule"),
    ("delete_policy_rule", "bifrost_delete_policy_rule"),
]


def test_revision_chains_to_the_configs_slice() -> None:
    assert MIGRATION.revision == "20260819_policy_rule_names"
    assert MIGRATION.down_revision == "20260819_config_mcp_names"


def test_revision_id_fits_the_alembic_version_column() -> None:
    """``alembic_version.version_num`` is varchar(32)."""
    assert len(MIGRATION.revision) <= 32


def test_only_preexisting_tools_are_renamed() -> None:
    """get / update / usages are new, so nothing persisted can reference them."""
    renamed_old_names = {old for old, _ in MIGRATION.RENAMES}
    assert renamed_old_names == {
        "list_policy_rules",
        "create_policy_rule",
        "delete_policy_rule",
    }
    assert "get_policy_rule" not in renamed_old_names
    assert "update_policy_rule" not in renamed_old_names
    assert "get_policy_rule_usages" not in renamed_old_names


def test_upgrade_renames_every_tool_in_both_locations(monkeypatch) -> None:
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


def test_list_rename_cannot_partially_match_the_singular_tool() -> None:
    """``create_policy_rule`` is a prefix of nothing here, but check the pair.

    ``list_policy_rules`` (plural) and ``create_policy_rule`` (singular) are
    distinct tokens, and the JSON rewrite matches quoted tokens, so neither
    rename can corrupt the other or an already-renamed name.
    """
    for old, new in EXPECTED:
        assert f'"{old}"' not in f'"{new}"'
