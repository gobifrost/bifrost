"""Smoke tests for ``bifrost files`` CLI commands.

Mocks BifrostClient.get_instance so no network or credentials are needed.
Mirrors the pattern in test_cli_tables.py.
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
import pytest
from click.testing import CliRunner

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.files import files_group  # noqa: E402


_DUMMY_REQUEST = httpx.Request("POST", "https://bifrost.test/api/files/read")


def _fake_response(body: dict, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body, request=_DUMMY_REQUEST)


def _make_mock_client(
    captured: dict,
    body_by_path: dict[str, dict | httpx.Response],
) -> mock.AsyncMock:
    """Return a mock BifrostClient that records calls and replies per path.

    File commands reach the API two ways: ``files.*`` SDK methods go through
    ``client.engine_request(method, path, json=...)`` while the stat/write/
    delete verbs post directly. Both are recorded identically so the
    assertions cover whichever transport the command uses.
    """

    def _record(path: str, body: dict | None) -> httpx.Response:
        captured.setdefault("calls", []).append({"path": path, "body": body})
        reply = body_by_path.get(path, {})
        if isinstance(reply, httpx.Response):
            return reply
        return _fake_response(reply)

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        return _record(path, json)

    async def capturing_engine_request(method, path, **kwargs):  # type: ignore[no-untyped-def]
        return _record(path, kwargs.get("json"))

    client = mock.AsyncMock()
    client.post = capturing_post
    client.engine_request = capturing_engine_request
    return client


def _invoke(
    args: list[str],
    captured: dict,
    body_by_path: dict[str, dict | httpx.Response],
):
    client = _make_mock_client(captured, body_by_path)
    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        runner = CliRunner()
        return runner.invoke(files_group, args)


class TestRead:
    def test_reads_workspace_file_by_default(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["read", "data/customers.csv"],
            captured,
            {"/api/files/read": {"content": "id,name\n1,Acme\n"}},
        )
        assert result.exit_code == 0, result.output
        assert "id,name" in result.output
        assert captured["calls"][0]["path"] == "/api/files/read"
        body = captured["calls"][0]["body"]
        assert body["path"] == "data/customers.csv"
        assert body["location"] == "workspace"
        assert body["binary"] is False

    def test_passes_location_flag(self) -> None:
        captured: dict = {}
        _invoke(
            ["read", "form_id/uuid/file.txt", "--location", "uploads"],
            captured,
            {"/api/files/read": {"content": ""}},
        )
        assert captured["calls"][0]["body"]["location"] == "uploads"

    def test_passes_custom_location_flag(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["read", "q1.pdf", "--location", "reports"],
            captured,
            {"/api/files/read": {"content": ""}},
        )
        assert result.exit_code == 0, result.output
        assert captured["calls"][0]["body"]["location"] == "reports"


class TestStat:
    def test_reports_version_without_printing_content(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["stat", "workflows/contact.py", "--json"],
            captured,
            {
                "/api/files/stat": {
                    "path": "workflows/contact.py",
                    "exists": True,
                    "version": "sha256:abc123",
                    "size": 13,
                }
            },
        )
        assert result.exit_code == 0, result.output
        assert '"path": "workflows/contact.py"' in result.output
        assert '"version": "sha256:abc123"' in result.output
        assert "secret source" not in result.output


class TestWrite:
    def test_writes_with_content_flag(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["write", "out.txt", "--content", "hello"],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["path"] == "out.txt"
        assert body["content"] == "hello"
        assert body["binary"] is False

    def test_writes_from_stdin_when_dash(self) -> None:
        captured: dict = {}
        runner = CliRunner()
        client = _make_mock_client(captured, {"/api/files/write": {}})
        with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
            result = runner.invoke(files_group, ["write", "out.txt", "-"], input="from-stdin\n")
        assert result.exit_code == 0, result.output
        assert captured["calls"][0]["body"]["content"] == "from-stdin\n"

    def test_writes_from_file_flag(self, tmp_path) -> None:
        local = tmp_path / "local.txt"
        local.write_text("local-content")
        captured: dict = {}
        result = _invoke(
            ["write", "out.txt", "--from-file", str(local)],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code == 0, result.output
        assert captured["calls"][0]["body"]["content"] == "local-content"

    def test_rejects_multiple_content_sources(self, tmp_path) -> None:
        local = tmp_path / "y.txt"
        local.write_text("y")
        captured: dict = {}
        result = _invoke(
            ["write", "out.txt", "--content", "x", "--from-file", str(local)],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code != 0
        assert "exactly one" in result.output.lower()

    def test_passes_expected_version(self) -> None:
        captured: dict = {}
        result = _invoke(
            [
                "write",
                "out.txt",
                "--content",
                "next",
                "--expected-version",
                "sha256:base",
            ],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["expected_version"] == "sha256:base"
        assert body["create_only"] is False

    def test_passes_create_only(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["write", "out.txt", "--content", "new", "--create-only"],
            captured,
            {"/api/files/write": {}},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["create_only"] is True
        assert body["expected_version"] is None

    def test_rejects_two_concurrency_guards(self) -> None:
        captured: dict = {}
        result = _invoke(
            [
                "write",
                "out.txt",
                "--content",
                "new",
                "--create-only",
                "--expected-version",
                "sha256:base",
            ],
            captured,
            {},
        )
        assert result.exit_code != 0
        assert "cannot be combined" in result.output
        assert "calls" not in captured

    def test_version_conflict_is_machine_readable_and_exit_four(self) -> None:
        request = httpx.Request("POST", "https://bifrost.test/api/files/write")
        conflict = httpx.Response(
            409,
            json={
                "detail": {
                    "reason": "version_conflict",
                    "path": "out.txt",
                    "expected_version": "sha256:base",
                    "current_version": "sha256:other",
                    "message": "File changed after it was read.",
                }
            },
            request=request,
        )
        captured: dict = {}
        result = _invoke(
            [
                "write",
                "out.txt",
                "--content",
                "next",
                "--expected-version",
                "sha256:base",
                "--json",
            ],
            captured,
            {"/api/files/write": conflict},
        )
        assert result.exit_code == 4
        payload = __import__("json").loads(result.stderr)
        assert payload["error"] == "file_conflict"
        assert payload["reason"] == "version_conflict"
        assert payload["current_version"] == "sha256:other"


class TestList:
    def test_list_default_directory(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["list"],
            captured,
            {"/api/files/list": {"files": ["a.txt", "b/"]}},
        )
        assert result.exit_code == 0, result.output
        assert "a.txt" in result.output
        assert captured["calls"][0]["body"]["directory"] == ""

    def test_list_with_prefix(self) -> None:
        captured: dict = {}
        _invoke(
            ["list", "uploads"],
            captured,
            {"/api/files/list": {"files": []}},
        )
        assert captured["calls"][0]["body"]["directory"] == "uploads"


class TestDelete:
    def test_delete_posts_to_endpoint(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["delete", "old.txt"],
            captured,
            {"/api/files/delete": {}},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["path"] == "old.txt"

    def test_passes_expected_version(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["delete", "old.txt", "--expected-version", "sha256:base"],
            captured,
            {"/api/files/delete": {}},
        )
        assert result.exit_code == 0, result.output
        assert captured["calls"][0]["body"]["expected_version"] == "sha256:base"


class TestExists:
    def test_exists_true(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["exists", "x.txt"],
            captured,
            {"/api/files/exists": {"exists": True}},
        )
        assert result.exit_code == 0, result.output
        assert "true" in result.output.lower()

    def test_exists_false_exits_nonzero(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["exists", "missing.txt"],
            captured,
            {"/api/files/exists": {"exists": False}},
        )
        assert result.exit_code == 1
        assert "false" in result.output.lower()


def _search_page(*, matches=(), files=(), has_more=False, cursor=None, mode="content") -> dict:
    return {
        "query": "q", "output_mode": mode, "matches": list(matches), "files": list(files),
        "returned": len(matches) or len(files), "has_more_matches": has_more,
        "response_complete": not has_more, "next_cursor": cursor,
        "guidance": "server guidance", "search_time_ms": 1,
    }


_WS = {"kind": "workspace", "editable": True}
_SOL = {"kind": "solution", "solution_slug": "covi-psa", "solution_id": None, "editable": False}


class TestSearch:
    def test_search_defaults_send_a_small_first_page(self) -> None:
        captured: dict = {}
        result = _invoke(["search", "TODO"], captured, {"/api/files/search": _search_page()})
        assert result.exit_code == 0, result.output
        assert captured["calls"][0]["body"] == {
            "query": "TODO", "is_regex": False, "case_sensitive": False, "source": "all",
            "output_mode": "content", "context_lines": 1, "limit": 25,
        }

    def test_search_passes_paging_scope_and_mode(self) -> None:
        captured: dict = {}
        _invoke(
            ["search", "f.*o", "--regex", "--case-sensitive", "--include", "*.py", "--source", "workspace",
             "--files", "-C", "0", "--limit", "5", "--cursor", "c1"],
            captured,
            {"/api/files/search": _search_page()},
        )
        body = captured["calls"][0]["body"]
        assert (body["is_regex"], body["case_sensitive"], body["include_pattern"], body["source"]) == (
            True, True, "*.py", "workspace",
        )
        assert (body["output_mode"], body["context_lines"], body["limit"], body["cursor"]) == ("files", 0, 5, "c1")

    def test_solution_flag_resolves_install_and_targets_solution_source(self) -> None:
        captured: dict = {}
        sid = "0f6b1c2e-0000-4000-8000-000000000000"
        _invoke(["search", "x", "--solution", sid], captured, {"/api/files/search": _search_page()})
        assert captured["calls"][0]["body"]["solution_id"] == sid

    def test_human_output_is_grep_style_with_copy_paste_next_page(self) -> None:
        page = _search_page(
            matches=[
                {"file_path": "modules/halo.py", "line": 12, "column": 4, "text": "def halo():",
                 "context_before": ["# api"], "context_after": [], "source": _SOL},
                {"file_path": "modules/halo.py", "line": 30, "column": 0, "text": "halo()",
                 "context_before": [], "context_after": [], "source": _SOL},
                {"file_path": "workflows/t.py", "line": 2, "column": 0, "text": "halo = 1",
                 "context_before": [], "context_after": [], "source": _WS},
            ],
            has_more=True, cursor="abc",
        )
        result = _invoke(["search", "halo", "--include", "*.py"], {}, {"/api/files/search": page})
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[:6] == [
            "covi-psa:modules/halo.py  (read-only Solution source)",
            "  11- # api",
            "  12: def halo():",
            "  30: halo()",
            "workflows/t.py",
            "  2: halo = 1",
        ]
        assert "More results: bifrost files search halo --include '*.py' --cursor abc" in result.output

    def test_human_output_files_mode_and_completion(self) -> None:
        page = _search_page(
            files=[{"file_path": "a.py", "match_count": 3, "first_line": 7, "source": _WS}], mode="files",
        )
        result = _invoke(["search", "x", "--files"], {}, {"/api/files/search": page})
        assert "a.py  (3 matches, first at line 7)" in result.output
        assert "1 result — complete." in result.output

    def test_json_output_is_the_raw_page(self) -> None:
        result = _invoke(["search", "x", "--json"], {}, {"/api/files/search": _search_page(cursor=None)})
        assert result.exit_code == 0, result.output
        assert '"response_complete": true' in result.output and '"guidance": "server guidance"' in result.output


# ---------------------------------------------------------------------------
# Fix 5: _resolve_solution_install_id slug ambiguity
# ---------------------------------------------------------------------------

class TestResolveSolutionInstallId:
    """_resolve_solution_install_id must error on multi-org slug ambiguity."""

    def _make_get_client(self, response_body: dict) -> mock.AsyncMock:
        """Return a mock client whose .get() returns the given body."""
        dummy_req = httpx.Request("GET", "https://bifrost.test/api/solutions")
        client = mock.AsyncMock()
        client.get = mock.AsyncMock(
            return_value=httpx.Response(200, json=response_body, request=dummy_req)
        )
        return client

    def test_single_match_returns_id(self) -> None:
        import asyncio
        from bifrost.commands.files import _resolve_solution_install_id

        client = self._make_get_client({
            "solutions": [
                {"id": "aaaa-1111", "slug": "my-sol"},
            ]
        })
        result = asyncio.get_event_loop().run_until_complete(
            _resolve_solution_install_id(client, "my-sol")
        )
        assert result == "aaaa-1111"

    def test_no_match_raises(self) -> None:
        client = self._make_get_client({"solutions": []})
        with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
            runner = CliRunner()
            # Invoke a real read command; slug won't resolve → ClickException
            result = runner.invoke(
                files_group, ["read", "--solution", "missing-slug", "notes.txt"]
            )
        assert result.exit_code != 0

    def test_ambiguous_slug_raises_click_exception(self) -> None:
        """Fix 5: when the same slug appears in multiple orgs, an unambiguous
        ClickException must be raised instead of silently picking the first match.
        """
        import asyncio
        import click
        from bifrost.commands.files import _resolve_solution_install_id

        client = self._make_get_client({
            "solutions": [
                {"id": "aaaa-1111", "slug": "shared-sol"},
                {"id": "bbbb-2222", "slug": "shared-sol"},
            ]
        })
        with pytest.raises(click.ClickException, match="multiple orgs"):
            asyncio.get_event_loop().run_until_complete(
                _resolve_solution_install_id(client, "shared-sol")
            )

    def test_uuid_is_returned_unchanged(self) -> None:
        """A valid UUID is returned directly without hitting the API."""
        import asyncio
        from bifrost.commands.files import _resolve_solution_install_id

        client = mock.AsyncMock()  # .get should NOT be called
        result = asyncio.get_event_loop().run_until_complete(
            _resolve_solution_install_id(client, "00000000-0000-0000-0000-000000000001")
        )
        assert result == "00000000-0000-0000-0000-000000000001"
        client.get.assert_not_called()
