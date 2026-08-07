"""Hidden, execution-scoped filesystem tools for the Solution builder Agent."""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.builder.fs_tools import WorkspaceRoot, WorkspaceViolation
from src.services.builder.scaffold import validate_workspace
from src.services.mcp_server.tool_result import error_result, success_result

BUILDER_WORKSPACE_TOOL_IDS = (
    "list_files",
    "read_file",
    "search_text",
    "write_file",
    "apply_patch",
    "delete_file",
    "make_directory",
    "validate_solution",
)
HIDDEN_TOOL_IDS = frozenset(BUILDER_WORKSPACE_TOOL_IDS)


def _workspace(context: Any) -> WorkspaceRoot:
    workspace = getattr(context, "builder_workspace", None)
    if not isinstance(workspace, WorkspaceRoot):
        raise WorkspaceViolation(
            "builder workspace tools are unavailable outside a builder turn"
        )
    return workspace


async def list_files(context: Any) -> ToolResult:
    try:
        paths = _workspace(context).list_files()
        return success_result(
            "\n".join(paths) if paths else "(workspace is empty)",
            {"paths": paths},
        )
    except WorkspaceViolation as exc:
        return error_result(str(exc))


async def read_file(context: Any, path: str) -> ToolResult:
    try:
        data, truncated = _workspace(context).read_file(path)
        text = data.decode("utf-8", errors="replace")
        return success_result(
            text + ("\n\n[truncated at read limit]" if truncated else ""),
            {"path": path, "content": text, "truncated": truncated},
        )
    except WorkspaceViolation as exc:
        return error_result(str(exc))


async def search_text(
    context: Any,
    pattern: str,
    glob: str = "**/*",
) -> ToolResult:
    try:
        hits = _workspace(context).search_text(pattern, rel_glob=glob)
        rows = [
            {"path": hit.path, "line_number": hit.line_number, "line": hit.line}
            for hit in hits
        ]
        display = "\n".join(
            f"{hit.path}:{hit.line_number}: {hit.line}" for hit in hits
        )
        return success_result(display or "No matches.", {"matches": rows})
    except WorkspaceViolation as exc:
        return error_result(str(exc))


async def write_file(context: Any, path: str, content: str) -> ToolResult:
    try:
        _workspace(context).write_file(path, content.encode("utf-8"))
        return success_result(f"Wrote {path}.", {"path": path})
    except WorkspaceViolation as exc:
        return error_result(str(exc))


async def apply_patch(
    context: Any,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolResult:
    try:
        if not old_string:
            raise WorkspaceViolation("old_string must not be empty")
        workspace = _workspace(context)
        data, truncated = workspace.read_file(path)
        if truncated:
            raise WorkspaceViolation("file is too large to patch; rewrite it instead")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceViolation("file is not valid UTF-8 text") from exc
        occurrences = text.count(old_string)
        if occurrences == 0:
            raise WorkspaceViolation("old_string not found in file")
        if occurrences > 1 and not replace_all:
            raise WorkspaceViolation(
                f"old_string matches {occurrences} times; make it unique or pass replace_all"
            )
        count = -1 if replace_all else 1
        workspace.write_file(
            path,
            text.replace(old_string, new_string, count).encode("utf-8"),
        )
        replaced = occurrences if replace_all else 1
        return success_result(
            f"Patched {path} ({replaced} replacement{'s' if replaced != 1 else ''}).",
            {"path": path, "replacements": replaced},
        )
    except WorkspaceViolation as exc:
        return error_result(str(exc))


async def delete_file(context: Any, path: str) -> ToolResult:
    try:
        _workspace(context).delete_file(path)
        return success_result(f"Deleted {path}.", {"path": path})
    except WorkspaceViolation as exc:
        return error_result(str(exc))


async def make_directory(context: Any, path: str) -> ToolResult:
    try:
        _workspace(context).make_directory(path)
        return success_result(f"Created directory {path}.", {"path": path})
    except WorkspaceViolation as exc:
        return error_result(str(exc))


async def validate_solution(context: Any) -> ToolResult:
    try:
        workspace = _workspace(context)
        errors = validate_workspace(workspace.root)
        if errors:
            return error_result(
                "workspace is not a valid Solution",
                {"validation_errors": errors},
            )
        paths = workspace.list_files()
        return success_result(
            f"Valid Solution workspace ({len(paths)} files).",
            {"valid": True, "file_count": len(paths)},
        )
    except WorkspaceViolation as exc:
        return error_result(str(exc))


TOOLS = [
    ("list_files", "List Builder Files", "List every file in the current builder workspace."),
    ("read_file", "Read Builder File", "Read a UTF-8 file from the current builder workspace."),
    ("search_text", "Search Builder Files", "Regex-search files in the current builder workspace."),
    ("write_file", "Write Builder File", "Create or replace a file in the current builder workspace."),
    ("apply_patch", "Patch Builder File", "Replace an exact string in a builder workspace file."),
    ("delete_file", "Delete Builder File", "Delete one file from the current builder workspace."),
    ("make_directory", "Create Builder Directory", "Create a directory in the current builder workspace."),
    ("validate_solution", "Validate Solution", "Validate the current builder workspace as a Solution."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    functions = {
        name: globals()[name]
        for name in BUILDER_WORKSPACE_TOOL_IDS
    }
    descriptions = {tool_id: description for tool_id, _, description in TOOLS}
    for tool_id, function in functions.items():
        register_tool_with_context(
            mcp,
            function,
            tool_id,
            descriptions[tool_id],
            get_context_fn,
        )
