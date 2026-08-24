"""Hidden, execution-scoped filesystem tools for the Solution builder Agent."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult

from src.services.builder.fs_tools import WorkspaceRoot, WorkspaceViolation
from src.services.builder.workspace_tool_runtime import (
    BUILDER_WORKSPACE_TOOL_IDS,
    TEST_SOLUTION_BUILD_TOOL_ID,
    error_result,
    execute_builder_workspace_tool,
    success_result,
)

BUILDER_BIFROST_TOOL_IDS = frozenset({TEST_SOLUTION_BUILD_TOOL_ID})
HIDDEN_TOOL_IDS = frozenset(BUILDER_WORKSPACE_TOOL_IDS) | BUILDER_BIFROST_TOOL_IDS


def _workspace(context: Any) -> WorkspaceRoot:
    workspace = getattr(context, "builder_workspace", None)
    if not isinstance(workspace, WorkspaceRoot):
        raise WorkspaceViolation(
            "builder workspace tools are unavailable outside a builder turn"
        )
    return workspace


async def list_files(context: Any) -> ToolResult:
    return await _execute(context, "list_files", {})


async def read_file(context: Any, path: str) -> ToolResult:
    return await _execute(context, "read_file", {"path": path})


async def search_text(
    context: Any,
    pattern: str,
    glob: str = "**/*",
) -> ToolResult:
    return await _execute(
        context,
        "search_text",
        {"pattern": pattern, "glob": glob},
    )


async def write_file(context: Any, path: str, content: str) -> ToolResult:
    return await _execute(
        context,
        "write_file",
        {"path": path, "content": content},
    )


async def apply_patch(
    context: Any,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolResult:
    return await _execute(
        context,
        "apply_patch",
        {
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        },
    )


async def delete_file(context: Any, path: str) -> ToolResult:
    return await _execute(context, "delete_file", {"path": path})


async def make_directory(context: Any, path: str) -> ToolResult:
    return await _execute(context, "make_directory", {"path": path})


async def validate_solution(context: Any) -> ToolResult:
    return await _execute(context, "validate_solution", {})


async def test_solution_build(context: Any) -> ToolResult:
    """Compile every source app without deploying the Solution."""

    from src.services.builder.build_plane import BuildPlaneUnavailable
    from src.services.builder.build_requests import BuildFailed
    from src.services.builder.solution_build_check import (
        SolutionBuildCheckError,
        bounded_build_log_excerpt,
        model_visible_build_failure,
        test_solution_workspace_build,
    )

    solution_id = getattr(context, "agent_solution_id", None)
    requested_by = getattr(context, "user_id", None)
    if not isinstance(solution_id, UUID) or not isinstance(requested_by, UUID):
        result = error_result(
            "production build testing is unavailable outside a Solution Builder turn"
        )
    else:
        try:
            checked = await test_solution_workspace_build(
                _workspace(context).root,
                solution_id=solution_id,
                requested_by=requested_by,
            )
        except BuildFailed as exc:
            result = error_result(
                model_visible_build_failure(exc),
                {
                    "build_job_id": str(exc.job_id),
                    "build_status": exc.status,
                    "build_log_excerpt": bounded_build_log_excerpt(exc),
                },
            )
        except (BuildPlaneUnavailable, SolutionBuildCheckError, TimeoutError) as exc:
            result = error_result(str(exc))
        else:
            data = checked.as_dict()
            result = success_result(
                (
                    f"Production build passed for {data['compiled_app_count']} "
                    f"source app(s)."
                ),
                data,
            )
    return ToolResult(
        content=result.content,
        structured_content=result.structured_content,
    )


async def _execute(
    context: Any,
    name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    result = await execute_builder_workspace_tool(
        workspace=_workspace(context),
        bundle_path=getattr(context, "agent_bundle_path", None),
        name=name,
        arguments=arguments,
    )
    return ToolResult(
        content=result.content,
        structured_content=result.structured_content,
    )


TOOLS = [
    ("list_files", "List Builder Files", "List every file in the current builder workspace."),
    ("read_file", "Read Builder File", "Read a UTF-8 file from the current builder workspace."),
    ("search_text", "Search Builder Files", "Regex-search files in the current builder workspace."),
    ("write_file", "Write Builder File", "Create or replace a file in the current builder workspace."),
    ("apply_patch", "Patch Builder File", "Replace an exact string in a builder workspace file."),
    ("delete_file", "Delete Builder File", "Delete one file from the current builder workspace."),
    ("make_directory", "Create Builder Directory", "Create a directory in the current builder workspace."),
    ("validate_solution", "Validate Solution", "Validate the current builder workspace as a Solution."),
    (
        TEST_SOLUTION_BUILD_TOOL_ID,
        "Test Solution Build",
        "Compile every source app with the production build plane without deploying it.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    functions = {
        name: globals()[name]
        for name in (*BUILDER_WORKSPACE_TOOL_IDS, *BUILDER_BIFROST_TOOL_IDS)
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
