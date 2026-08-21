"""Framework-neutral execution core for Builder workspace tools.

The MCP server and the optional Cloudflare runner both call this module. It
deliberately has no database, FastMCP, or API imports so the isolated runner can
ship the exact filesystem behavior without embedding another Bifrost server.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from src.services.builder.fs_tools import WorkspaceRoot, WorkspaceViolation
from src.services.builder.scaffold import validate_workspace

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
CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID = "execute_command"
TEST_SOLUTION_BUILD_TOOL_ID = "test_solution_build"
READ_SKILL_ASSET_TOOL_ID = "bifrost_read_agent_skill_file"
SANDBOX_BUILDER_TOOL_IDS = frozenset(BUILDER_WORKSPACE_TOOL_IDS) | {
    CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID,
    READ_SKILL_ASSET_TOOL_ID
}
MAX_SKILL_ASSET_BYTES = 1_048_576


@dataclass(frozen=True)
class BuilderWorkspaceToolResult:
    """Portable equivalent of the two channels in an MCP ToolResult."""

    content: str
    structured_content: dict[str, Any] | None = None
    display_content: str | None = None

    def runner_payload(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "structured_content": self.structured_content,
        }


def success_result(
    display_text: str,
    data: dict[str, Any] | None = None,
) -> BuilderWorkspaceToolResult:
    content = display_text
    if data:
        content = f"{display_text}\n\n{json.dumps(data, indent=2, default=str)}"
    return BuilderWorkspaceToolResult(
        content=content,
        structured_content=data,
        display_content=display_text,
    )


def error_result(
    message: str,
    extra_data: dict[str, Any] | None = None,
) -> BuilderWorkspaceToolResult:
    data = {"error": message, **(extra_data or {})}
    return BuilderWorkspaceToolResult(
        content=f"Error: {message}\n\n{json.dumps(data, indent=2, default=str)}",
        structured_content=data,
        display_content=f"Error: {message}",
    )


def _relative_parts(value: str, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorkspaceViolation(f"{label} must be a non-empty relative path")
    if "\\" in value:
        raise WorkspaceViolation(f"{label} contains a platform path separator")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise WorkspaceViolation(f"{label} must stay beneath the bundle root")
    return pure.parts


def resolve_workspace_skill_asset(bundle_path: str, asset_path: str) -> str:
    """Resolve an asset beneath a workspace-relative skill bundle."""

    bundle_parts = _relative_parts(bundle_path, label="bundle_path")
    asset_parts = _relative_parts(asset_path, label="path")
    return PurePosixPath(*bundle_parts, *asset_parts).as_posix()


async def execute_builder_workspace_tool(
    *,
    workspace: WorkspaceRoot,
    bundle_path: str | None,
    name: str,
    arguments: dict[str, Any],
) -> BuilderWorkspaceToolResult:
    """Execute one fixed Builder tool against one contained workspace."""

    try:
        if name == "list_files":
            paths = workspace.list_files()
            return success_result(
                "\n".join(paths) if paths else "(workspace is empty)",
                {"paths": paths},
            )
        if name == "read_file":
            path = str(arguments.get("path", ""))
            data, truncated = workspace.read_file(path)
            content = data.decode("utf-8", errors="replace")
            return success_result(
                content + ("\n\n[truncated at read limit]" if truncated else ""),
                {"path": path, "content": content, "truncated": truncated},
            )
        if name == "search_text":
            pattern = str(arguments.get("pattern", ""))
            glob = str(arguments.get("glob", "**/*"))
            hits = workspace.search_text(pattern, rel_glob=glob)
            rows = [
                {
                    "path": hit.path,
                    "line_number": hit.line_number,
                    "line": hit.line,
                }
                for hit in hits
            ]
            display = "\n".join(
                f"{hit.path}:{hit.line_number}: {hit.line}" for hit in hits
            )
            return success_result(display or "No matches.", {"matches": rows})
        if name == "write_file":
            path = str(arguments.get("path", ""))
            content = str(arguments.get("content", ""))
            workspace.write_file(path, content.encode("utf-8"))
            return success_result(f"Wrote {path}.", {"path": path})
        if name == "apply_patch":
            path = str(arguments.get("path", ""))
            old_string = str(arguments.get("old_string", ""))
            new_string = str(arguments.get("new_string", ""))
            replace_all = arguments.get("replace_all", False) is True
            if not old_string:
                raise WorkspaceViolation("old_string must not be empty")
            data, truncated = workspace.read_file(path)
            if truncated:
                raise WorkspaceViolation(
                    "file is too large to patch; rewrite it instead"
                )
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkspaceViolation("file is not valid UTF-8 text") from exc
            occurrences = text.count(old_string)
            if occurrences == 0:
                raise WorkspaceViolation("old_string not found in file")
            if occurrences > 1 and not replace_all:
                raise WorkspaceViolation(
                    f"old_string matches {occurrences} times; make it unique or "
                    "pass replace_all"
                )
            workspace.write_file(
                path,
                text.replace(
                    old_string,
                    new_string,
                    -1 if replace_all else 1,
                ).encode("utf-8"),
            )
            replaced = occurrences if replace_all else 1
            suffix = "s" if replaced != 1 else ""
            return success_result(
                f"Patched {path} ({replaced} replacement{suffix}).",
                {"path": path, "replacements": replaced},
            )
        if name == "delete_file":
            path = str(arguments.get("path", ""))
            workspace.delete_file(path)
            return success_result(f"Deleted {path}.", {"path": path})
        if name == "make_directory":
            path = str(arguments.get("path", ""))
            workspace.make_directory(path)
            return success_result(f"Created directory {path}.", {"path": path})
        if name == "validate_solution":
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
        if name == READ_SKILL_ASSET_TOOL_ID:
            if not bundle_path:
                return error_result(
                    "bifrost_read_agent_skill_file is unavailable without an agent bundle"
                )
            requested_path = str(arguments.get("path", ""))
            resolved_path = resolve_workspace_skill_asset(
                bundle_path,
                requested_path,
            )
            content, truncated = workspace.read_file(resolved_path)
            if truncated or len(content) > MAX_SKILL_ASSET_BYTES:
                return error_result(
                    f"skill asset exceeds the {MAX_SKILL_ASSET_BYTES}-byte read limit"
                )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                return success_result(
                    f"Read binary skill asset: {requested_path}",
                    {
                        "path": requested_path,
                        "encoding": "base64",
                        "content": base64.b64encode(content).decode("ascii"),
                    },
                )
            return success_result(
                f"Read skill asset: {requested_path}",
                {
                    "path": requested_path,
                    "encoding": "utf-8",
                    "content": text,
                },
            )
        if name == CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID:
            return error_result(
                "workspace command execution requires an isolated sandbox"
            )
        return error_result(f"Builder workspace tool {name!r} is unavailable")
    except WorkspaceViolation as exc:
        return error_result(str(exc))


__all__ = [
    "BUILDER_WORKSPACE_TOOL_IDS",
    "BuilderWorkspaceToolResult",
    "CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID",
    "MAX_SKILL_ASSET_BYTES",
    "READ_SKILL_ASSET_TOOL_ID",
    "SANDBOX_BUILDER_TOOL_IDS",
    "execute_builder_workspace_tool",
    "resolve_workspace_skill_asset",
]
