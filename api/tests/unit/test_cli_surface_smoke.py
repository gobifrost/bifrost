"""Smoke test the full CLI surface.

Invokes ``--help`` on every entity subgroup and every sub-command. A failure
here means the CLI can't even boot a command (import error, registration
regression, decorator bug) — a much cheaper signal than per-entity E2E tests.

Covers:
- Every group in :data:`bifrost.commands.ENTITY_GROUPS` responds to ``--help``.
- Every subcommand within every group responds to ``--help``.
- The top-level ``bifrost --help`` renders.
- The top-level ``bifrost <entity>`` (no subcommand) prints usage.

Does NOT exercise the API — these are pure Click invocations against the
in-memory command tree, no network, no DB.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from bifrost.commands import ENTITY_GROUPS


def _group_subcommand_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for group_name, group in ENTITY_GROUPS.items():
        for subcommand_name in sorted(group.commands.keys()):
            pairs.append((group_name, subcommand_name))
    return pairs


@pytest.mark.parametrize("group_name", sorted(ENTITY_GROUPS.keys()))
def test_entity_group_help_renders(group_name: str) -> None:
    """``bifrost <entity> --help`` exits 0 with usage text."""
    group = ENTITY_GROUPS[group_name]
    runner = CliRunner()
    result = runner.invoke(group, ["--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    assert "Commands:" in result.output


@pytest.mark.parametrize("group_name,subcommand", _group_subcommand_pairs())
def test_subcommand_help_renders(group_name: str, subcommand: str) -> None:
    """``bifrost <entity> <subcommand> --help`` exits 0 with usage text.

    A failure here means the subcommand can't be loaded — typically a broken
    DTO flag generator, a bad decorator, or a missing import.
    """
    group = ENTITY_GROUPS[group_name]
    runner = CliRunner()
    result = runner.invoke(group, [subcommand, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_every_group_has_json_flag() -> None:
    """Every entity group surfaces the shared ``--json`` flag on its help.

    This guards against future subgroups skipping :func:`entity_group` and
    inventing their own output convention.
    """
    runner = CliRunner()
    for group_name, group in ENTITY_GROUPS.items():
        result = runner.invoke(group, ["--help"])
        assert result.exit_code == 0, f"{group_name}: {result.output}"
        assert "--json" in result.output, (
            f"{group_name} help does not advertise --json: {result.output}"
        )


EXPECTED_CRUD_COMMANDS: dict[str, set[str]] = {
    "orgs": {"list", "get", "create", "update", "delete"},
    "roles": {"list", "get", "create", "update", "delete"},
    "forms": {"list", "get", "create", "update", "delete"},
    "agents": {"list", "get", "create", "update", "delete"},
    "apps": {"list", "get", "create", "update", "delete"},
    "configs": {"list", "get", "create", "update", "delete"},
    "tables": {"list", "get", "create", "update", "delete"},
    "integrations": {"list", "get", "create", "update"},
    "workflows": {"list", "get", "update", "remap", "delete"},
    "events": {
        "list-sources",
        "get-source",
        "create-source",
        "update-source",
        "list-subscriptions",
        "get-subscription",
        "subscribe",
        "update-subscription",
    },
}


@pytest.mark.parametrize("group_name,expected", sorted(EXPECTED_CRUD_COMMANDS.items()))
def test_expected_crud_commands_exist(
    group_name: str, expected: set[str]
) -> None:
    """Guard against accidentally removing a CRUD command from an entity.

    This is the manifest-parity check: every entity the platform persists
    must be addressable from the CLI. If this test fails, either the command
    was renamed (update this table) or it was removed (re-add it).
    """
    group = ENTITY_GROUPS[group_name]
    actual = set(group.commands.keys())
    missing = expected - actual
    assert not missing, (
        f"{group_name} is missing expected commands: {missing}. "
        f"Actual commands: {sorted(actual)}"
    )
