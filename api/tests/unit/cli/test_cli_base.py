"""Tests for shared CLI command infrastructure in ``bifrost.commands.base``.

Covers the contract from plan Task 4:

* ``--json`` output bypasses human formatting.
* ``RefNotFoundError`` surfaces as exit code 2.
* ``AmbiguousRefError`` surfaces as exit code 2 with the candidate list.
* HTTP 4xx surfaces as exit code 1 with the server body.
* HTTP 403 surfaces the ``required`` hint if present in the body.
* HTTP 5xx surfaces as exit code 3 with a retry hint.
"""

from __future__ import annotations

import json
import click
import httpx
from click.testing import CliRunner

from bifrost.commands.base import (
    entity_group,
    output_result,
    run_async,
)
from bifrost.refs import AmbiguousRefError, RefNotFoundError


# ---------------------------------------------------------------------------
# output_result / --json flag routing
# ---------------------------------------------------------------------------


class TestOutputResult:
    def test_human_dict_sorted_key_value(self) -> None:
        runner = CliRunner()

        @click.command()
        @click.pass_context
        def cmd(ctx: click.Context) -> None:
            output_result({"b": 2, "a": 1}, ctx=ctx)

        result = runner.invoke(cmd, [])
        assert result.exit_code == 0, result.output
        # Keys sorted alphabetically in human mode.
        assert result.output.splitlines() == ["a: 1", "b: 2"]

    def test_human_list_of_entities(self) -> None:
        runner = CliRunner()

        @click.command()
        @click.pass_context
        def cmd(ctx: click.Context) -> None:
            output_result(
                [
                    {"id": "11111111-1111-1111-1111-111111111111", "name": "foo"},
                    {"id": "22222222-2222-2222-2222-222222222222", "name": "bar"},
                ],
                ctx=ctx,
            )

        result = runner.invoke(cmd, [])
        assert result.exit_code == 0, result.output
        lines = result.output.strip().split("\n")
        assert "foo" in lines[0]
        assert "bar" in lines[1]

    def test_json_output_via_group_flag(self) -> None:
        """``--json`` on the entity group switches all sub-command output."""
        runner = CliRunner()
        group = entity_group("fake", "fake commands")

        @group.command("show")
        @click.pass_context
        def show(ctx: click.Context) -> None:
            output_result({"b": 2, "a": 1}, ctx=ctx)

        result = runner.invoke(group, ["--json", "show"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# run_async error surfacing
# ---------------------------------------------------------------------------


class TestRunAsyncErrorSurfacing:
    def _wrap(self, exc: BaseException) -> click.Command:
        @click.command()
        @run_async
        async def cmd() -> None:
            raise exc

        return cmd

    def test_ref_not_found_exits_2(self) -> None:
        runner = CliRunner()
        cmd = self._wrap(RefNotFoundError("org", "bogus"))
        result = runner.invoke(cmd, [])
        assert result.exit_code == 2
        # click stderr is merged into output by default in CliRunner.
        assert "bogus" in result.output
        assert "org" in result.output

    def test_ambiguous_ref_exits_2_with_candidates(self) -> None:
        runner = CliRunner()
        candidates = [
            {
                "name": "Acme",
                "uuid": "11111111-1111-1111-1111-111111111111",
                "org_id": None,
            },
            {
                "name": "Acme",
                "uuid": "22222222-2222-2222-2222-222222222222",
                "org_id": "33333333-3333-3333-3333-333333333333",
            },
        ]
        cmd = self._wrap(AmbiguousRefError("org", "Acme", candidates))
        result = runner.invoke(cmd, [])
        assert result.exit_code == 2
        assert "pass the UUID" in result.output
        assert "11111111-1111-1111-1111-111111111111" in result.output
        assert "22222222-2222-2222-2222-222222222222" in result.output
        assert "33333333-3333-3333-3333-333333333333" in result.output

    def _http_error(self, status: int, body: dict | str) -> httpx.HTTPStatusError:
        req = httpx.Request("POST", "http://test/api/x")
        if isinstance(body, dict):
            resp = httpx.Response(status, json=body, request=req)
        else:
            resp = httpx.Response(status, text=body, request=req)
        return httpx.HTTPStatusError("boom", request=req, response=resp)

    def test_http_422_exits_1_with_body(self) -> None:
        runner = CliRunner()
        cmd = self._wrap(
            self._http_error(422, {"detail": "name already exists"})
        )
        result = runner.invoke(cmd, [])
        assert result.exit_code == 1
        assert "422" in result.output
        assert "name already exists" in result.output

    def test_http_403_surfaces_required_hint(self) -> None:
        runner = CliRunner()
        cmd = self._wrap(
            self._http_error(
                403,
                {"detail": "forbidden", "required_role": "platform_admin"},
            )
        )
        result = runner.invoke(cmd, [])
        assert result.exit_code == 1
        assert "403" in result.output
        assert "platform_admin" in result.output
        assert "Required:" in result.output

    def test_http_500_exits_3_with_retry_hint(self) -> None:
        runner = CliRunner()
        cmd = self._wrap(self._http_error(500, {"detail": "boom"}))
        result = runner.invoke(cmd, [])
        assert result.exit_code == 3
        assert "retry" in result.output.lower()

    def test_not_logged_in_exits_1(self) -> None:
        runner = CliRunner()
        cmd = self._wrap(RuntimeError("Not logged in. Run 'bifrost login' to authenticate."))
        result = runner.invoke(cmd, [])
        assert result.exit_code == 1
        assert "Not logged in" in result.output


# ---------------------------------------------------------------------------
# Subgroup registration (sanity check for dispatch_entity_subgroup)
# ---------------------------------------------------------------------------


class TestSubgroupRegistration:
    def test_all_entity_subgroups_registered(self) -> None:
        from bifrost.commands import ENTITY_GROUPS

        assert set(ENTITY_GROUPS) == {
            "orgs",
            "roles",
            "workflows",
            "forms",
            "agents",
            "apps",
            "integrations",
            "configs",
            "tables",
            "claims",
            "events",
            "files",
            "requirements",
        }

    def test_dispatch_unknown_subgroup_exits_1(self) -> None:
        from bifrost.commands import dispatch_entity_subgroup

        code = dispatch_entity_subgroup("nonexistent", ["list"])
        assert code == 1
