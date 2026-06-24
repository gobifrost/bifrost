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


def _make_mock_client(captured: dict, body_by_path: dict[str, dict]) -> mock.AsyncMock:
    """Return a mock BifrostClient that records calls and replies per path."""

    async def capturing_post(path, json=None):  # type: ignore[no-untyped-def]
        captured.setdefault("calls", []).append({"path": path, "body": json})
        return _fake_response(body_by_path.get(path, {}))

    client = mock.AsyncMock()
    client.post = capturing_post
    return client


def _invoke(args: list[str], captured: dict, body_by_path: dict[str, dict]):
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


class TestSearch:
    def test_search_posts_query(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["search", "TODO"],
            captured,
            {"/api/files/search": {
                "query": "TODO",
                "total_matches": 0,
                "files_searched": 0,
                "results": [],
                "truncated": False,
                "search_time_ms": 1,
            }},
        )
        assert result.exit_code == 0, result.output
        body = captured["calls"][0]["body"]
        assert body["query"] == "TODO"
        assert body["is_regex"] is False
        assert body["case_sensitive"] is False
        assert body["include_pattern"] == "**/*"
        assert body["max_results"] == 1000

    def test_search_passes_through_flags(self) -> None:
        captured: dict = {}
        _invoke(
            ["search", "f.*o", "--regex", "--case-sensitive",
             "--include", "**/*.py", "--max-results", "50"],
            captured,
            {"/api/files/search": {
                "query": "f.*o",
                "total_matches": 0,
                "files_searched": 0,
                "results": [],
                "truncated": False,
                "search_time_ms": 1,
            }},
        )
        body = captured["calls"][0]["body"]
        assert body["is_regex"] is True
        assert body["case_sensitive"] is True
        assert body["include_pattern"] == "**/*.py"
        assert body["max_results"] == 50

    def test_search_json_output(self) -> None:
        captured: dict = {}
        result = _invoke(
            ["search", "x", "--json"],
            captured,
            {"/api/files/search": {
                "query": "x",
                "total_matches": 1,
                "files_searched": 1,
                "results": [{
                    "file_path": "a.py", "line": 3, "column": 0,
                    "match_text": "x", "context_before": None, "context_after": None,
                }],
                "truncated": False,
                "search_time_ms": 2,
            }},
        )
        assert result.exit_code == 0, result.output
        assert '"total_matches": 1' in result.output
        assert '"file_path": "a.py"' in result.output


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
