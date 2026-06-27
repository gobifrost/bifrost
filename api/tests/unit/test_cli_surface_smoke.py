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

from bifrost.cli import main
from bifrost.commands import ENTITY_GROUPS


TOP_LEVEL_COMMANDS = {
    "sync",
    "run",
    "git",
    "push",
    "pull",
    "solution",
    "deploy",
    "watch",
    "api",
    "migrate-imports",
    "skill",
    "login",
    "logout",
    "auth",
    "help",
}


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


def test_top_level_help_lists_every_entity_group(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand-written top-level help must not drift from registered groups."""
    monkeypatch.setattr("bifrost.cli._check_cli_version", lambda: None)
    code = main(["--help"])
    captured = capsys.readouterr()
    assert code == 0
    for group_name in sorted(ENTITY_GROUPS):
        assert f"  {group_name}" in captured.out, (
            f"top-level help omits registered entity group {group_name!r}"
        )


def test_top_level_help_lists_every_top_level_command(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top-level command help must stay aligned with main() dispatch."""
    monkeypatch.setattr("bifrost.cli._check_cli_version", lambda: None)
    code = main(["--help"])
    captured = capsys.readouterr()
    assert code == 0
    command_section = captured.out.split("Commands:", 1)[1].split("Flags:", 1)[0]
    for command_name in sorted(TOP_LEVEL_COMMANDS):
        assert f"  {command_name}" in command_section, (
            f"top-level help omits command {command_name!r}"
        )


def test_top_level_help_explains_repo_and_solution_file_targets(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Users should be able to discover _repo vs Solution CLI access."""
    monkeypatch.setattr("bifrost.cli._check_cli_version", lambda: None)
    code = main(["--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "_repo source files" in captured.out
    assert "Solution source files" in captured.out
    assert "Solution runtime files" in captured.out
    assert "bifrost files list --solution" in captured.out
    assert "Push vs files write" in captured.out
    assert "same relative" in captured.out
    assert "path under _repo" in captured.out
    assert "writes exactly one" in captured.out
    assert "arbitrary local-to-remote path" in captured.out


def test_nested_help_does_not_check_cli_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested help must render offline without compatibility probes."""
    monkeypatch.setattr(
        "bifrost.cli._check_cli_version",
        lambda: pytest.fail("help should not check CLI/server compatibility"),
    )
    code = main(["files", "--help"])
    assert code == 0


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
