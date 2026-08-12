# api/src/services/mcp_server/tool_result.py
"""
MCP Tool Result Helpers

Helpers for building FastMCP ToolResult objects with proper
content (human-readable) and structured_content (machine-parseable).

Note: structured data is also appended as JSON to the display text
so that MCP clients using older protocol versions (before 2025-06-18)
that don't support structuredContent can still parse the full data.
"""

import json
from typing import Any

from fastmcp.tools import ToolResult


def direct_result(data: Any) -> ToolResult:
    """Return a tool payload without adding display or metadata envelopes.

    MCP ``structuredContent`` only accepts JSON objects, so using it would
    either duplicate object payloads or require wrapping arrays and scalars.
    Direct execution results instead use the required content channel for
    every JSON value, preserving the selected tool's exact return shape.
    """
    return ToolResult(content="null" if data is None else data)


def success_result(display_text: str, data: dict[str, Any] | None = None) -> ToolResult:
    """
    Create a successful tool result with display text and structured data.

    Args:
        display_text: Human-readable text for display in CLI/UI
        data: Optional structured data dict for LLM parsing

    Returns:
        ToolResult with content and structured_content per MCP spec.
    """
    content = display_text
    if data:
        json_str = json.dumps(data, indent=2, default=str)
        content = f"{display_text}\n\n{json_str}"
    return ToolResult(
        content=content,
        structured_content=data,
    )


def error_result(error_message: str, extra_data: dict[str, Any] | None = None) -> ToolResult:
    """
    Create an error tool result.

    Args:
        error_message: Human-readable error description
        extra_data: Optional additional data to include in structured_content

    Returns:
        ToolResult with error information.
    """
    display_text = f"Error: {error_message}"
    data = {"error": error_message}
    if extra_data:
        data.update(extra_data)

    json_str = json.dumps(data, indent=2, default=str)
    content = f"{display_text}\n\n{json_str}"

    return ToolResult(
        content=content,
        structured_content=data,
    )


def format_grep_matches(matches: list[dict[str, Any]], pattern: str) -> str:
    """
    Format search results in grep style (file:line: match).

    This format is recognized by Claude Code for automatic syntax highlighting.

    Args:
        matches: List of match dicts with path, line_number, match keys
        pattern: The search pattern (for display in header)

    Returns:
        Grep-style formatted string
    """
    if not matches:
        return f"No matches found for pattern: {pattern}"

    count_word = "match" if len(matches) == 1 else "matches"
    lines = [f"Found {len(matches)} {count_word} for '{pattern}'", ""]

    for m in matches:
        # Format: path:line: match_content (grep style)
        match_text = m.get("match", "").strip()
        lines.append(f"{m['path']}:{m['line_number']}: {match_text}")

    return "\n".join(lines)


def format_diff(path: str, old_lines: list[str], new_lines: list[str]) -> str:
    """
    Format changes in diff style.

    Uses +/- prefixes recognized by Claude Code for automatic coloring.

    Args:
        path: File path that was modified
        old_lines: Lines that were removed
        new_lines: Lines that were added

    Returns:
        Diff-style formatted string
    """
    lines = [f"Updated {path}", ""]

    for line in old_lines:
        lines.append(f"-  {line}")

    for line in new_lines:
        lines.append(f"+  {line}")

    return "\n".join(lines)


def format_file_content(path: str, content: str, start_line: int = 1) -> str:
    """
    Format file content with line numbers.

    Uses the same numbered format as Claude Code's Read tool.

    Args:
        path: File path
        content: File content
        start_line: Starting line number (default 1)

    Returns:
        Content with line numbers
    """
    lines = content.split("\n")
    total_lines = start_line + len(lines) - 1
    width = len(str(total_lines))

    numbered = []
    for i, line in enumerate(lines):
        line_num = start_line + i
        numbered.append(f"{line_num:>{width}}: {line}")

    return f"{path}\n" + "\n".join(numbered)
