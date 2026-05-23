"""
Code Editor MCP Tools - Precision Editing

Tools for searching, reading, and editing code stored in the workspace:
- Workflows (Python workflow code)
- Modules (Python helper modules)
- App files (TSX/TypeScript for App Builder)
- Text files (Markdown, README, documentation, config files)

All files are accessed via their path in the file_index / S3 _repo/ store.

These tools mirror Claude Code's precision editing workflow:
1. list_content - List files, optionally filtered by path prefix
2. search_content - Find code with regex
3. read_content_lines - Read specific line ranges
4. get_content - Full content read (fallback)
5. patch_content - Surgical old->new replacement
6. replace_content - Full content write (fallback)
7. delete_content - Delete a file
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

from fastmcp.tools import ToolResult
from sqlalchemy import select

from src.services.mcp_server.tools.db import get_tool_db
from src.models.orm.file_index import FileIndex
from src.services.file_storage import FileStorageService
from src.services.repo_storage import RepoStorage
from src.services.mcp_server.tool_result import (
    error_result,
    format_diff,
    format_file_content,
    format_grep_matches,
    success_result,
)


def _format_deactivation_result(
    path: str,
    pending_deactivations: list[dict[str, Any]],
    available_replacements: list[dict[str, Any]] | None,
) -> ToolResult:
    """
    Format a deactivation protection result for Claude to handle.

    Returns a ToolResult that tells Claude:
    1. Which workflows would be deactivated
    2. What entities (forms, agents) reference them
    3. Available replacement functions (for renames)
    4. How to proceed (apply replacements or force deactivation)
    """
    lines = [
        f"Warning: Saving {path} would deactivate {len(pending_deactivations)} workflow(s):",
        "",
    ]

    for pd in pending_deactivations:
        lines.append(f"  * {pd['function_name']} ({pd['decorator_type']})")
        if pd["has_executions"]:
            lines.append(f"    - Has execution history (last: {pd['last_execution_at'] or 'unknown'})")
        if pd["endpoint_enabled"]:
            lines.append("    - Has API endpoint enabled")
        if pd["affected_entities"]:
            lines.append("    - Referenced by:")
            for ae in pd["affected_entities"][:5]:  # Limit to 5
                lines.append(f"      - {ae['entity_type']}: {ae['name']} ({ae['reference_type']})")
            if len(pd["affected_entities"]) > 5:
                lines.append(f"      - ... and {len(pd['affected_entities']) - 5} more")

    if available_replacements:
        lines.append("")
        lines.append("Possible renames detected:")
        for ar in available_replacements[:3]:  # Top 3 suggestions
            lines.append(f"  * {ar['function_name']} (similarity: {ar['similarity_score']:.0%})")

    lines.extend([
        "",
        "To proceed, you can:",
        "1. Apply replacements: Re-call with replacements={\"<old_workflow_id>\": \"<new_function_name>\"}",
        "2. Force deactivation: Re-call with force_deactivation=true",
        "3. Abort: Do not save this file",
    ])

    return ToolResult(
        content="\n".join(lines),
        structured_content={
            "status": "pending_deactivations",
            "path": path,
            "pending_deactivations": pending_deactivations,
            "available_replacements": available_replacements or [],
            "resolution_options": {
                "apply_replacements": "Re-call with replacements parameter mapping old workflow IDs to new function names",
                "force_deactivation": "Re-call with force_deactivation=true to proceed anyway",
                "abort": "Do not save the file",
            },
        },
    )

logger = logging.getLogger(__name__)

# Maximum content size before truncation (characters)
MAX_CONTENT_CHARS = 100_000  # ~100KB, similar to Claude Code's Read tool


# =============================================================================
# Helper Functions
# =============================================================================


def _require_platform_admin(context: Any) -> ToolResult | None:
    if not getattr(context, "is_platform_admin", False):
        return error_result("Platform administrator privileges are required for code editor tools.")
    return None


def _normalize_line_endings(content: str) -> str:
    """Normalize line endings to \\n for consistent matching."""
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _get_lines_with_context(
    content: str, line_number: int, context_lines: int = 3
) -> tuple[list[str], list[str]]:
    """Get context lines before and after a given line number (1-indexed)."""
    lines = content.split("\n")
    idx = line_number - 1  # Convert to 0-indexed

    start_before = max(0, idx - context_lines)
    end_after = min(len(lines), idx + context_lines + 1)

    before = [f"{i + 1}: {lines[i]}" for i in range(start_before, idx)]
    after = [f"{i + 1}: {lines[i]}" for i in range(idx + 1, end_after)]

    return before, after


def _find_match_locations(content: str, search_string: str) -> list[dict[str, Any]]:
    """Find all locations where search_string appears in content."""
    locations = []
    lines = content.split("\n")

    for i, line in enumerate(lines):
        if search_string in line:
            # Get a preview (truncated if too long)
            preview = line.strip()
            if len(preview) > 60:
                preview = preview[:57] + "..."
            locations.append({"line": i + 1, "preview": preview})

    return locations


async def _read_from_s3(path: str) -> str | None:
    """Read file content directly from S3 (source of truth)."""
    try:
        repo = RepoStorage()
        content_bytes = await repo.read(path)
    except Exception:
        return None
    try:
        return content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None  # Binary file


async def _get_content_by_path(
    path: str,
    context: Any = None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """
    Get content for a file by path.

    All files are in file_index + S3 _repo/.

    Returns:
        Tuple of (content, metadata_dict, error_message)
        - If successful: (content_str, {"path": ...}, None)
        - If error: (None, None, "error message")
    """
    content = await _read_from_s3(path)
    if content is None:
        return None, None, f"File not found: {path}"
    return content, {"path": path}, None


@dataclass
class WorkspaceWriteResult:
    """Result of writing a workspace file."""

    created: bool
    pending_deactivations: list[dict[str, Any]] | None = None
    available_replacements: list[dict[str, Any]] | None = None


async def _replace_workspace_file(
    context: Any,
    path: str,
    content: str,
    force_deactivation: bool = False,
    replacements: dict[str, str] | None = None,
) -> WorkspaceWriteResult:
    """
    Replace or create a workspace file via FileStorageService.

    All files (workflows, modules, app files, text) go through this path.

    Args:
        context: Request context
        path: File path
        content: File content
        force_deactivation: If True, bypass deactivation protection
        replacements: Mapping of old_workflow_id -> new_function_name for renames

    Returns:
        WorkspaceWriteResult with created status and any pending deactivations
    """
    async with get_tool_db(context) as db:
        service = FileStorageService(db)

        # Check if file exists to determine created status
        try:
            await service.read_file(path)
            created = False
        except FileNotFoundError:
            created = True

        # Write through FileStorageService with deactivation protection
        write_result = await service.write_file(
            path=path,
            content=content.encode("utf-8"),
            updated_by=context.user_email or "mcp",
            force_deactivation=force_deactivation,
            replacements=replacements,
        )

        # Check for pending deactivations
        if write_result.pending_deactivations:
            pending = [
                {
                    "id": pd.id,
                    "name": pd.name,
                    "function_name": pd.function_name,
                    "path": pd.path,
                    "description": pd.description,
                    "decorator_type": pd.decorator_type,
                    "has_executions": pd.has_executions,
                    "last_execution_at": pd.last_execution_at,
                    "endpoint_enabled": pd.endpoint_enabled,
                    "affected_entities": pd.affected_entities,
                }
                for pd in write_result.pending_deactivations
            ]
            available = [
                {
                    "function_name": ar.function_name,
                    "name": ar.name,
                    "decorator_type": ar.decorator_type,
                    "similarity_score": ar.similarity_score,
                }
                for ar in (write_result.available_replacements or [])
            ]
            return WorkspaceWriteResult(
                created=created,
                pending_deactivations=pending,
                available_replacements=available,
            )

        return WorkspaceWriteResult(created=created)


# =============================================================================
# list_content Tool
# =============================================================================


async def list_content(
    context: Any,
    organization_id: str | None = None,
    path_prefix: str | None = None,
) -> ToolResult:
    """List files in the workspace. Optionally filter by path prefix."""
    logger.info(f"MCP list_content: path_prefix={path_prefix}")
    if denied := _require_platform_admin(context):
        return denied

    try:
        repo = RepoStorage()
        paths = await repo.list(path_prefix or "")
        files = [{"path": p} for p in sorted(paths)]

        if not files:
            display = "No files found"
        else:
            lines = [f"Found {len(files)} file(s):", ""]
            for f in files:
                lines.append(f"  {f['path']}")
            display = "\n".join(lines)

        return success_result(display, {"files": files, "count": len(files)})
    except Exception as e:
        logger.exception(f"Error in list_content: {e}")
        return error_result(f"List failed: {str(e)}")


# =============================================================================
# search_content Tool
# =============================================================================


async def search_content(
    context: Any,
    pattern: str,
    path: str | None = None,
    organization_id: str | None = None,
    context_lines: int = 3,
    max_results: int = 20,
) -> ToolResult:
    """Search for regex patterns across all workspace files."""
    logger.info(f"MCP search_content: pattern={pattern}")
    if denied := _require_platform_admin(context):
        return denied

    if not pattern:
        return error_result("pattern is required")

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return error_result(f"Invalid regex pattern: {e}")

    matches: list[dict[str, Any]] = []

    try:
        async with get_tool_db(context) as db:
            query = select(FileIndex.path, FileIndex.content).where(
                FileIndex.content.isnot(None),
            )
            if path:
                query = query.where(FileIndex.path == path)

            result = await db.execute(query)
            all_files = result.all()

            for row in all_files:
                content = _normalize_line_endings(row.content)
                file_lines = content.split("\n")
                for i, line in enumerate(file_lines):
                    if regex.search(line):
                        before, after = _get_lines_with_context(content, i + 1, context_lines)
                        matches.append({
                            "path": row.path,
                            "line_number": i + 1,
                            "match": line,
                            "context_before": before,
                            "context_after": after,
                        })
                        if len(matches) >= max_results:
                            break
                if len(matches) >= max_results:
                    break

        truncated = len(matches) >= max_results
        display = format_grep_matches(matches, pattern)
        return success_result(display, {
            "matches": matches,
            "total_matches": len(matches),
            "truncated": truncated,
        })
    except Exception as e:
        logger.exception(f"Error in search_content: {e}")
        return error_result(f"Search failed: {str(e)}")


# =============================================================================
# read_content_lines Tool
# =============================================================================


async def read_content_lines(
    context: Any,
    path: str,
    organization_id: str | None = None,
    start_line: int = 1,
    end_line: int | None = None,
) -> ToolResult:
    """Read a specific range of lines from a file."""
    logger.info(
        f"MCP read_content_lines: path={path}, lines={start_line}-{end_line}"
    )
    if denied := _require_platform_admin(context):
        return denied

    if not path:
        return error_result("path is required")

    content_result, metadata_result, error = await _get_content_by_path(path, context)

    if error:
        return error_result(error)
    if content_result is None or metadata_result is None:
        return error_result("Failed to retrieve content")

    # Now we know these are not None
    content_str = _normalize_line_endings(content_result)
    lines = content_str.split("\n")
    total_lines = len(lines)

    # Apply defaults
    if start_line < 1:
        start_line = 1
    if end_line is None:
        end_line = min(start_line + 100, total_lines)
    if end_line > total_lines:
        end_line = total_lines

    # Extract requested lines (1-indexed)
    selected_lines_raw: list[str] = []
    selected_lines_numbered: list[str] = []
    for i in range(start_line - 1, end_line):
        if i < len(lines):
            selected_lines_raw.append(lines[i])
            selected_lines_numbered.append(f"{i + 1}: {lines[i]}")

    # Format display with line numbers
    display = format_file_content(
        metadata_result["path"],
        "\n".join(selected_lines_raw),
        start_line,
    )

    return success_result(
        display,
        {
            "path": metadata_result["path"],
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "content": "\n".join(selected_lines_numbered),
        },
    )


# =============================================================================
# get_content Tool
# =============================================================================


async def get_content(
    context: Any,
    path: str,
    organization_id: str | None = None,
) -> ToolResult:
    """Get the entire content of a file."""
    logger.info(f"MCP get_content: path={path}")
    if denied := _require_platform_admin(context):
        return denied

    if not path:
        return error_result("path is required")

    content_result, metadata_result, error = await _get_content_by_path(path, context)

    if error:
        return error_result(error)
    if content_result is None or metadata_result is None:
        return error_result("Failed to retrieve content")

    content_str = _normalize_line_endings(content_result)
    lines = content_str.split("\n")
    total_lines = len(lines)
    total_chars = len(content_str)
    truncated = False
    warning = None

    # Truncate if content exceeds limit
    if total_chars > MAX_CONTENT_CHARS:
        truncated = True
        # Find last complete line within limit
        truncated_content = content_str[:MAX_CONTENT_CHARS]
        last_newline = truncated_content.rfind("\n")
        if last_newline > 0:
            content_str = truncated_content[:last_newline]
        else:
            content_str = truncated_content
        lines_shown = content_str.count("\n") + 1
        warning = (
            f"Content truncated: {total_chars:,} chars exceeds {MAX_CONTENT_CHARS:,} limit. "
            f"Showing {lines_shown:,} of {total_lines:,} lines. "
            f"Use read_content_lines with start_line/end_line for specific sections."
        )

    # Format display with line numbers
    display = format_file_content(metadata_result["path"], content_str)
    if warning:
        display = f"{warning}\n\n{display}"

    result_data: dict[str, Any] = {
        "path": metadata_result["path"],
        "total_lines": total_lines,
        "content": content_str,
    }

    if truncated:
        result_data["truncated"] = True
        result_data["warning"] = warning

    return success_result(display, result_data)


# =============================================================================
# patch_content Tool
# =============================================================================


async def patch_content(
    context: Any,
    path: str,
    old_string: str,
    new_string: str,
    organization_id: str | None = None,
    force_deactivation: bool = False,
    replacements: dict[str, str] | None = None,
) -> ToolResult:
    """
    Make a surgical edit by replacing a unique string.

    For Python files, if the edit would deactivate existing workflows (e.g., function
    was renamed or removed), the tool returns information about affected workflows
    instead of proceeding. See replace_content for resolution options.

    Args:
        context: Request context
        path: File path
        old_string: String to find and replace (must be unique in file)
        new_string: Replacement string
        organization_id: Organization ID
        force_deactivation: If True, proceed even if workflows would be deactivated
        replacements: Mapping of old_workflow_id -> new_function_name for renames
    """
    logger.info(f"MCP patch_content: path={path}")
    if denied := _require_platform_admin(context):
        return denied

    if not path:
        return error_result("path is required")
    if not old_string:
        return error_result("old_string is required")

    content_result, metadata_result, error = await _get_content_by_path(path, context)

    if error:
        return error_result(error)
    if content_result is None or metadata_result is None:
        return error_result("Failed to retrieve content")

    content_str = _normalize_line_endings(content_result)
    old_string = _normalize_line_endings(old_string)
    new_string = _normalize_line_endings(new_string)

    # Check uniqueness
    match_count = content_str.count(old_string)

    if match_count == 0:
        return error_result("old_string not found in file")

    if match_count > 1:
        locations = _find_match_locations(content_str, old_string)
        return error_result(
            f"old_string matches {match_count} locations. Include more context to make it unique.",
            {"match_locations": locations},
        )

    # Perform replacement
    new_content = content_str.replace(old_string, new_string, 1)

    # Count lines changed
    old_lines = old_string.count("\n") + 1
    new_lines = new_string.count("\n") + 1
    lines_changed = max(old_lines, new_lines)

    # Persist the change — all files go through _replace_workspace_file
    try:
        result = await _replace_workspace_file(
            context,
            path,
            new_content,
            force_deactivation=force_deactivation,
            replacements=replacements,
        )

        # Check for pending deactivations
        if result.pending_deactivations:
            return _format_deactivation_result(
                path,
                result.pending_deactivations,
                result.available_replacements,
            )

        # Format diff-style display
        display = format_diff(
            metadata_result["path"],
            old_string.split("\n"),
            new_string.split("\n"),
        )

        return success_result(
            display,
            {
                "success": True,
                "path": metadata_result["path"],
                "lines_changed": lines_changed,
            },
        )

    except Exception as e:
        logger.exception(f"Error persisting patch: {e}")
        return error_result(f"Failed to save changes: {str(e)}")


# =============================================================================
# replace_content Tool
# =============================================================================


async def replace_content(
    context: Any,
    path: str,
    content: str,
    organization_id: str | None = None,
    force_deactivation: bool = False,
    replacements: dict[str, str] | None = None,
) -> ToolResult:
    """
    Replace entire file content or create a new file.

    For Python files, if the save would deactivate existing workflows (e.g., function
    was renamed or removed), the tool returns information about affected workflows
    instead of proceeding. The caller can then:
    1. Apply replacements: Pass replacements={old_workflow_id: new_function_name}
    2. Force deactivation: Pass force_deactivation=True
    3. Abort: Don't save the file

    Args:
        context: Request context
        path: File path
        content: New file content
        organization_id: Organization ID
        force_deactivation: If True, proceed even if workflows would be deactivated
        replacements: Mapping of old_workflow_id -> new_function_name for renames
    """
    logger.info(f"MCP replace_content: path={path}")
    if denied := _require_platform_admin(context):
        return denied

    if not path:
        return error_result("path is required")
    if not content:
        return error_result("content is required")

    content = _normalize_line_endings(content)

    try:
        # All files go through _replace_workspace_file (unified path)
        result = await _replace_workspace_file(
            context,
            path,
            content,
            force_deactivation=force_deactivation,
            replacements=replacements,
        )

        # Check for pending deactivations
        if result.pending_deactivations:
            return _format_deactivation_result(
                path,
                result.pending_deactivations,
                result.available_replacements,
            )

        action = "Created" if result.created else "Updated"
        return success_result(
            f"{action} {path}",
            {
                "success": True,
                "path": path,
                "created": result.created,
            },
        )

    except Exception as e:
        logger.exception(f"Error in replace_content: {e}")
        return error_result(str(e))


# =============================================================================
# delete_content Tool
# =============================================================================


async def delete_content(
    context: Any,
    path: str,
    organization_id: str | None = None,
) -> ToolResult:
    """Delete a file."""
    logger.info(f"MCP delete_content: path={path}")
    if denied := _require_platform_admin(context):
        return denied

    if not path:
        return error_result("path is required")

    try:
        async with get_tool_db(context) as db:
            # Verify file exists in S3 before deleting
            repo = RepoStorage()
            if not await repo.exists(path):
                return error_result(f"File not found: {path}")

            # Use FileStorageService.delete_file() for all deletions
            # It handles: S3 cleanup, file_index cleanup, app pubsub,
            # metadata removal (workflow deactivation, form/agent deletion),
            # and module cache invalidation
            service = FileStorageService(db)
            await service.delete_file(path)

        return success_result(
            f"Deleted {path}",
            {
                "success": True,
                "path": path,
            },
        )

    except Exception as e:
        logger.exception(f"Error in delete_content: {e}")
        return error_result(str(e))


# =============================================================================
# Tool Registration
# =============================================================================

# Tool metadata for registration
TOOLS = [
    ("list_content", "List Content", "List files in the workspace. Optionally filter by path prefix."),
    ("search_content", "Search Content", "Search for patterns in code files. Returns matching lines with context."),
    ("read_content_lines", "Read Content Lines", "Read specific line range from a file."),
    ("get_content", "Get Content", "Get entire file content."),
    ("patch_content", "Patch Content", "Surgical edit: replace old_string with new_string."),
    ("replace_content", "Replace Content", "Replace entire file content or create new file."),
    ("delete_content", "Delete Content", "Delete a file."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all code editor tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_content": list_content,
        "search_content": search_content,
        "read_content_lines": read_content_lines,
        "get_content": get_content,
        "patch_content": patch_content,
        "replace_content": replace_content,
        "delete_content": delete_content,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
