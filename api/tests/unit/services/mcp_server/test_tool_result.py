# api/tests/unit/services/mcp_server/test_tool_result.py
"""Tests for MCP tool result helpers."""

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import (
    direct_result,
    error_result,
    format_diff,
    format_file_content,
    format_grep_matches,
    success_result,
)


class TestSuccessResult:
    def test_creates_tool_result_with_text(self):
        result = success_result("Hello world", None)
        assert isinstance(result, ToolResult)
        assert result.structured_content is None

    def test_creates_tool_result_with_data(self):
        result = success_result("Found items", {"count": 5, "items": ["a", "b"]})
        assert isinstance(result, ToolResult)
        assert result.structured_content == {"count": 5, "items": ["a", "b"]}


class TestDirectResult:
    def test_returns_array_as_the_only_content(self):
        result = direct_result([{"echo": "hello"}])

        assert result.structured_content is None
        assert len(result.content) == 1
        assert result.content[0].text == '[{"echo":"hello"}]'

    def test_returns_object_without_a_gateway_envelope(self):
        result = direct_result({"echo": "hello"})

        assert result.structured_content is None
        assert len(result.content) == 1
        assert result.content[0].text == '{"echo":"hello"}'

    def test_serializes_null_result(self):
        result = direct_result(None)

        assert result.structured_content is None
        assert result.content[0].text == "null"


class TestErrorResult:
    def test_creates_error_result(self):
        result = error_result("Something went wrong")
        assert isinstance(result, ToolResult)
        assert result.structured_content["error"] == "Something went wrong"

    def test_includes_extra_data(self):
        result = error_result("Failed", {"code": 500})
        assert result.structured_content["error"] == "Failed"
        assert result.structured_content["code"] == 500


class TestFormatGrepMatches:
    def test_formats_matches(self):
        matches = [
            {"path": "file.py", "line_number": 10, "match": "def foo():"},
        ]
        result = format_grep_matches(matches, "def")
        assert "Found 1 match for 'def'" in result
        assert "file.py:10: def foo():" in result

    def test_handles_empty_matches(self):
        result = format_grep_matches([], "pattern")
        assert "No matches found" in result


class TestFormatDiff:
    def test_formats_diff(self):
        result = format_diff("file.py", ["old line"], ["new line"])
        assert "Updated file.py" in result
        assert "-  old line" in result
        assert "+  new line" in result


class TestFormatFileContent:
    def test_formats_with_line_numbers(self):
        result = format_file_content("file.py", "line1\nline2")
        assert "file.py" in result
        assert "1: line1" in result
        assert "2: line2" in result
