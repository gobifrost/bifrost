"""Unit tests for the CLI JSON error contract.

When ``--json`` is passed to an entity command, errors must be emitted as
a single parseable JSON object on stderr. Without ``--json``, errors stay
in the human-readable multi-line format.

Covers:
- HTTP 4xx/5xx errors (JSON and non-JSON response bodies)
- 403 required-role surfacing
- RefNotFoundError / AmbiguousRefError from ``bifrost.refs``
- "Not logged in" RuntimeError

The tests invoke a throwaway Click group wired with the same
``json_output_option`` + ``run_async`` machinery real commands use, so we
exercise the shared error path in ``bifrost.commands.base`` without
depending on any specific entity subgroup.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from bifrost.commands.base import entity_group, run_async
from bifrost.refs import AmbiguousRefError, RefNotFoundError


def _make_group(raise_factory):
    """Build a throwaway entity group with a ``fail`` subcommand that raises."""

    group = entity_group("test", "Test group.")

    @group.command("fail")
    @run_async
    async def _fail() -> None:
        raise raise_factory()

    return group


def _http_error(
    status: int, body: Any, *, as_json: bool = True
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/api/thing")
    if as_json:
        response = httpx.Response(status, json=body, request=request)
    else:
        response = httpx.Response(status, text=str(body), request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestJsonErrorContract:
    """--json flag routes errors through the machine-readable path."""

    def test_http_4xx_json_body_emits_single_line_json(self) -> None:
        group = _make_group(
            lambda: _http_error(400, {"detail": "bad input"})
        )
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        assert result.exit_code == 1
        # One line of JSON on stderr, no pretty indent.
        stderr = result.stderr.strip()
        assert "\n" not in stderr, stderr
        payload = json.loads(stderr)
        assert payload == {
            "error": "http_error",
            "status": 400,
            "reason": "Bad Request",
            "body": {"detail": "bad input"},
        }

    def test_http_4xx_without_json_flag_is_human_readable(self) -> None:
        group = _make_group(
            lambda: _http_error(400, {"detail": "bad input"})
        )
        runner = CliRunner()
        result = runner.invoke(group, ["fail"])

        assert result.exit_code == 1
        # First line is the status; body is a subsequent pretty-printed block.
        lines = result.stderr.splitlines()
        assert lines[0] == "HTTP 400 Bad Request"
        # Pretty-printed JSON spans multiple lines.
        body = "\n".join(lines[1:])
        assert '"detail": "bad input"' in body

    def test_http_403_surfaces_required_role_in_json(self) -> None:
        group = _make_group(
            lambda: _http_error(
                403, {"detail": "denied", "required_role": "admin"}
            )
        )
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        assert result.exit_code == 1
        payload = json.loads(result.stderr.strip())
        assert payload["status"] == 403
        assert payload["required"] == "admin"

    def test_http_5xx_returns_exit_code_3(self) -> None:
        group = _make_group(
            lambda: _http_error(503, {"detail": "overloaded"})
        )
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        assert result.exit_code == 3
        payload = json.loads(result.stderr.strip())
        assert payload["status"] == 503

    def test_guarded_file_conflict_has_dedicated_exit_code_and_shape(self) -> None:
        group = _make_group(
            lambda: _http_error(
                409,
                {
                    "detail": {
                        "reason": "version_conflict",
                        "path": "workflows/contact.py",
                        "expected_version": "sha256:old",
                        "current_version": "sha256:new",
                        "message": "File changed after it was read.",
                    }
                },
            )
        )
        runner = CliRunner()
        result = runner.invoke(group, ["fail", "--json"])

        assert result.exit_code == 4
        payload = json.loads(result.stderr.strip())
        assert payload == {
            "error": "file_conflict",
            "status": 409,
            "reason": "version_conflict",
            "path": "workflows/contact.py",
            "expected_version": "sha256:old",
            "current_version": "sha256:new",
            "message": "File changed after it was read.",
        }

    def test_non_json_error_body_is_carried_as_string(self) -> None:
        group = _make_group(
            lambda: _http_error(502, "upstream crashed", as_json=False)
        )
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        assert result.exit_code == 3
        payload = json.loads(result.stderr.strip())
        assert payload["body"] == "upstream crashed"

    def test_ref_not_found_emits_json(self) -> None:
        group = _make_group(lambda: RefNotFoundError("workflow", "missing"))
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        assert result.exit_code == 2
        stderr = result.stderr.strip()
        assert "\n" not in stderr
        payload = json.loads(stderr)
        assert payload == {
            "error": "ref_not_found",
            "kind": "workflow",
            "value": "missing",
        }

    def test_ref_not_found_without_json_flag_is_human(self) -> None:
        group = _make_group(lambda: RefNotFoundError("workflow", "missing"))
        runner = CliRunner()
        result = runner.invoke(group, ["fail"])

        assert result.exit_code == 2
        assert "Could not find workflow matching 'missing'." in result.stderr
        # Human output is not parseable as JSON.
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.stderr.strip())

    def test_ambiguous_ref_emits_json_with_candidates(self) -> None:
        candidates = [
            {"name": "foo", "uuid": "11111111-1111-1111-1111-111111111111"},
            {
                "name": "foo",
                "uuid": "22222222-2222-2222-2222-222222222222",
                "org_id": "org-1",
            },
        ]
        group = _make_group(
            lambda: AmbiguousRefError("workflow", "foo", candidates)
        )
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        assert result.exit_code == 2
        stderr = result.stderr.strip()
        assert "\n" not in stderr
        payload = json.loads(stderr)
        assert payload["error"] == "ambiguous_ref"
        assert payload["kind"] == "workflow"
        assert payload["candidates"] == candidates

    def test_not_logged_in_runtime_error_emits_json(self) -> None:
        group = _make_group(lambda: RuntimeError("Not logged in; run bifrost login"))
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        assert result.exit_code == 1
        stderr = result.stderr.strip()
        assert "\n" not in stderr
        payload = json.loads(stderr)
        assert payload["error"] == "not_logged_in"
        assert "Not logged in" in payload["message"]

    def test_unrelated_runtime_error_is_not_swallowed(self) -> None:
        """Non-auth RuntimeErrors propagate — ``run_async`` re-raises them.

        This protects us from regressing into a catch-all that swallows
        genuine bugs. Click surfaces them as exit 1 with a traceback.
        """
        group = _make_group(lambda: RuntimeError("something else entirely"))
        runner = CliRunner()
        result = runner.invoke(group, ["--json", "fail"])

        # Not our error — Click catches it as an unhandled exception.
        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)
