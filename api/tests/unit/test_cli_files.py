"""Smoke tests for ``bifrost files`` CLI commands.

Mocks BifrostClient.get_instance so no network or credentials are needed.
Mirrors the pattern in test_cli_tables.py.
"""

from __future__ import annotations

import pathlib
import sys
import unittest.mock as mock

import httpx
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
