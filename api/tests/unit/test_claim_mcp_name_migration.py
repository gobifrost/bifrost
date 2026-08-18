"""Forward migration coverage for persisted Custom Claim MCP tool IDs."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260818_claim_mcp_tool_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "claim_mcp_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


EXPECTED = [
    ("list_claims", "bifrost_list_claims"),
    ("get_claim", "bifrost_get_claim"),
    ("create_claim", "bifrost_create_claim"),
    ("update_claim", "bifrost_update_claim"),
    ("delete_claim", "bifrost_delete_claim"),
]


def test_upgrade_renames_every_claim_tool_to_the_canonical_name(monkeypatch) -> None:
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
