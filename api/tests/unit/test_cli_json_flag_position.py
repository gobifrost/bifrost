"""Unit tests for the ``--json`` flag accepting either flag position.

Click options only parse at the position they're declared. ``entity_group``
attaches ``--json`` at the group level (so ``bifrost tables --json list``
works), but most users naturally type the flag *after* the subcommand
(``bifrost tables list --json``). ``_EntityGroup.add_command`` appends a
fresh ``--json`` option to every subcommand so both positions parse.

These tests pin the behavior so a future refactor of ``base.py`` can't
silently regress the post-subcommand position again.
"""

from __future__ import annotations

import json

import click
from click.testing import CliRunner

from bifrost.commands.base import entity_group, output_result


def _make_group() -> click.Group:
    group = entity_group("test", "Test group.")

    @group.command("list")
    @click.pass_context
    def list_cmd(ctx: click.Context) -> None:
        # Echo a list payload so we can tell JSON vs human formatting apart.
        output_result(
            [{"id": "a", "name": "alpha"}, {"id": "b", "name": "beta"}],
            ctx=ctx,
        )

    return group


class TestJsonFlagPosition:
    def test_json_before_subcommand(self) -> None:
        runner = CliRunner()
        result = runner.invoke(_make_group(), ["--json", "list"])
        assert result.exit_code == 0, result.output
        # Output is a JSON array (--json takes the json branch in output_result)
        payload = json.loads(result.output)
        assert payload == [
            {"id": "a", "name": "alpha"},
            {"id": "b", "name": "beta"},
        ]

    def test_json_after_subcommand(self) -> None:
        # The fix under test: ``--json`` placed after the subcommand name
        # used to error with "No such option: --json". Now it parses and
        # routes through the same context flag as the group-level position.
        runner = CliRunner()
        result = runner.invoke(_make_group(), ["list", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == [
            {"id": "a", "name": "alpha"},
            {"id": "b", "name": "beta"},
        ]

    def test_no_json_uses_human_format(self) -> None:
        runner = CliRunner()
        result = runner.invoke(_make_group(), ["list"])
        assert result.exit_code == 0, result.output
        # Human format renders list-of-{id,name} dicts as id<TAB>name lines
        assert "alpha" in result.output
        assert "beta" in result.output
        # And it isn't JSON
        with __import__("pytest").raises(json.JSONDecodeError):
            json.loads(result.output)

    def test_json_at_both_positions_is_idempotent(self) -> None:
        # Defensive: passing ``--json`` at both group and subcommand level
        # shouldn't error or flip the flag off. The two callbacks both
        # write True into ``ctx.obj["json_output"]``.
        runner = CliRunner()
        result = runner.invoke(_make_group(), ["--json", "list", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == [
            {"id": "a", "name": "alpha"},
            {"id": "b", "name": "beta"},
        ]

    def test_subcommand_help_advertises_json(self) -> None:
        # If ``--help`` on a subcommand doesn't show ``--json``, users won't
        # discover it. The auto-applied option must show up in the help text.
        runner = CliRunner()
        result = runner.invoke(_make_group(), ["list", "--help"])
        assert result.exit_code == 0, result.output
        assert "--json" in result.output
