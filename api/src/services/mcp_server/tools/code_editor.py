"""Workspace file MCP tools backed by the canonical REST API.

The public tools in this module are intentionally small adapters.  File
authorization, Solution-managed source protection, conflict handling, entity
indexing, preview builds, cache invalidation, and activity notifications all
belong to ``/api/files/*`` and are shared with the CLI and browser editor.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import (
    error_result,
    format_diff,
    format_file_content,
    format_grep_matches,
    success_result,
)
from src.services.mcp_server.tools._http_bridge import call_rest


MAX_CONTENT_CHARS = 100_000


def _rest_error(action: str, status_code: int, body: Any) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
    else:
        message = detail
    return error_result(
        str(message) if message else f"{action} failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


def _workspace_body(**values: Any) -> dict[str, Any]:
    return {
        **values,
        "location": "workspace",
        "mode": "cloud",
    }


async def bifrost_list_files(
    context: Any,
    path_prefix: str | None = None,
) -> ToolResult:
    """List files in the global source workspace."""
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/list",
        json_body=_workspace_body(directory=path_prefix or ""),
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("List workspace files", status_code, body)
    paths = sorted(str(path) for path in body.get("files", []))
    files = [{"path": path} for path in paths]
    display = "No files found" if not files else (
        f"Found {len(files)} file(s):\n\n"
        + "\n".join(f"  {item['path']}" for item in files)
    )
    return success_result(display, {"files": files, "count": len(files)})


async def bifrost_search_files(
    context: Any,
    pattern: str,
    path: str | None = None,
    case_sensitive: bool = True,
    max_results: int = 20,
) -> ToolResult:
    """Search workspace text with a regular expression."""
    if not pattern:
        return error_result("pattern is required")
    request: dict[str, Any] = {
        "query": pattern,
        "case_sensitive": case_sensitive,
        "is_regex": True,
        "include_pattern": path or "**/*",
        "max_results": max_results,
    }
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/search",
        json_body=request,
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Search workspace files", status_code, body)
    matches = [
        {
            "path": item.get("file_path"),
            "line_number": item.get("line"),
            "match": item.get("match_text"),
            "column": item.get("column"),
            "context_before": item.get("context_before"),
            "context_after": item.get("context_after"),
        }
        for item in body.get("results", [])
        if isinstance(item, dict)
    ]
    return success_result(
        format_grep_matches(matches, pattern),
        {
            "matches": matches,
            "total_matches": body.get("total_matches", len(matches)),
            "files_searched": body.get("files_searched", 0),
            "truncated": bool(body.get("truncated", False)),
            "search_time_ms": body.get("search_time_ms", 0),
        },
    )


async def bifrost_read_file(
    context: Any,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> ToolResult:
    """Read a text file, optionally selecting an inclusive line range."""
    if not path:
        return error_result("path is required")
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/read",
        json_body=_workspace_body(path=path, binary=False),
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Read workspace file", status_code, body)
    content = str(body.get("content", "")).replace("\r\n", "\n").replace("\r", "\n")
    all_lines = content.split("\n")
    total_lines = len(all_lines)
    start = max(1, start_line)
    end = total_lines if end_line is None else min(max(end_line, start), total_lines)
    selected = "\n".join(all_lines[start - 1 : end])
    truncated = False
    warning = None
    if len(selected) > MAX_CONTENT_CHARS:
        truncated = True
        selected = selected[:MAX_CONTENT_CHARS]
        last_newline = selected.rfind("\n")
        if last_newline > 0:
            selected = selected[:last_newline]
        warning = (
            f"Content truncated at {MAX_CONTENT_CHARS:,} characters. "
            "Read a smaller line range to continue."
        )
        end = start + selected.count("\n")
    display = format_file_content(path, selected, start)
    if warning:
        display = f"{warning}\n\n{display}"
    numbered = "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(selected.split("\n"), start=start)
    )
    data: dict[str, Any] = {
        "path": path,
        "start_line": start,
        "end_line": end,
        "total_lines": total_lines,
        "content": numbered,
        "raw_content": selected,
    }
    if truncated:
        data.update({"truncated": True, "warning": warning})
    return success_result(display, data)


async def bifrost_stat_file(context: Any, path: str) -> ToolResult:
    """Get the version, size, and last editor for a workspace file."""
    if not path:
        return error_result("path is required")
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/stat",
        json_body=_workspace_body(path=path, binary=False),
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Stat workspace file", status_code, body)
    state = "exists" if body.get("exists") else "does not exist"
    return success_result(f"{path} {state}", body)


async def bifrost_exists_file(context: Any, path: str) -> ToolResult:
    """Check whether a workspace file exists."""
    if not path:
        return error_result("path is required")
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/exists",
        json_body=_workspace_body(path=path),
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Check workspace file", status_code, body)
    exists = bool(body.get("exists", False))
    return success_result(
        f"{path} {'exists' if exists else 'does not exist'}",
        {"path": path, "exists": exists},
    )


async def bifrost_write_file(
    context: Any,
    path: str,
    content: str,
    expected_version: str | None = None,
    create_only: bool = False,
) -> ToolResult:
    """Create or replace a workspace text file through REST."""
    if not path:
        return error_result("path is required")
    stat_code, stat_body = await call_rest(
        context,
        "POST",
        "/api/files/stat",
        json_body=_workspace_body(path=path, binary=False),
    )
    if stat_code != 200 or not isinstance(stat_body, dict):
        return _rest_error("Stat workspace file", stat_code, stat_body)
    created = not bool(stat_body.get("exists", False))
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/write",
        json_body=_workspace_body(
            path=path,
            content=content.replace("\r\n", "\n").replace("\r", "\n"),
            binary=False,
            expected_version=expected_version,
            create_only=create_only,
        ),
    )
    if status_code != 204:
        return _rest_error("Write workspace file", status_code, body)
    return success_result(
        f"{'Created' if created else 'Updated'} {path}",
        {"success": True, "path": path, "created": created},
    )


async def bifrost_patch_file(
    context: Any,
    path: str,
    old_string: str,
    new_string: str,
    expected_version: str | None = None,
    force_deactivation: bool = False,
    replacements: dict[str, str] | None = None,
    workflows_to_deactivate: list[str] | None = None,
) -> ToolResult:
    """Replace one unique text fragment in a workspace file."""
    if not path:
        return error_result("path is required")
    if not old_string:
        return error_result("old_string is required")
    old_string = old_string.replace("\r\n", "\n").replace("\r", "\n")
    new_string = new_string.replace("\r\n", "\n").replace("\r", "\n")
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/patch",
        json_body={
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
            "expected_version": expected_version,
            "force_deactivation": force_deactivation,
            "replacements": replacements,
            "workflows_to_deactivate": workflows_to_deactivate,
        },
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Patch workspace file", status_code, body)
    return success_result(
        format_diff(path, old_string.split("\n"), new_string.split("\n")),
        {"success": True, **body},
    )


async def bifrost_delete_file(
    context: Any,
    path: str,
    expected_version: str | None = None,
) -> ToolResult:
    """Delete one workspace file through REST."""
    if not path:
        return error_result("path is required")
    status_code, body = await call_rest(
        context,
        "POST",
        "/api/files/delete",
        json_body=_workspace_body(path=path, expected_version=expected_version),
    )
    if status_code != 204:
        return _rest_error("Delete workspace file", status_code, body)
    return success_result(
        f"Deleted {path}",
        {"success": True, "path": path},
    )


TOOLS = [
    ("bifrost_list_files", "List Files", "List files in the global source workspace."),
    ("bifrost_search_files", "Search Files", "Search workspace text with a regular expression."),
    ("bifrost_read_file", "Read File", "Read all or part of a workspace text file."),
    ("bifrost_stat_file", "Stat File", "Get conflict-safe workspace file metadata."),
    ("bifrost_exists_file", "Check File", "Check whether a workspace file exists."),
    ("bifrost_write_file", "Write File", "Create or replace a workspace text file."),
    ("bifrost_patch_file", "Patch File", "Replace one unique fragment in a workspace file."),
    ("bifrost_delete_file", "Delete File", "Delete one workspace file."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register canonical workspace file tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_files": bifrost_list_files,
        "bifrost_search_files": bifrost_search_files,
        "bifrost_read_file": bifrost_read_file,
        "bifrost_stat_file": bifrost_stat_file,
        "bifrost_exists_file": bifrost_exists_file,
        "bifrost_write_file": bifrost_write_file,
        "bifrost_patch_file": bifrost_patch_file,
        "bifrost_delete_file": bifrost_delete_file,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp,
            tool_funcs[tool_id],
            tool_id,
            description,
            get_context_fn,
        )
