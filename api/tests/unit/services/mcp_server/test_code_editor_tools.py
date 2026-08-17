"""Contract tests for REST-backed Workspace File MCP tools."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastmcp.tools import ToolResult

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import code_editor


@pytest.fixture
def context() -> MCPContext:
    return MCPContext(
        user_id=uuid4(),
        org_id=None,
        is_platform_admin=True,
        user_email="admin@platform.local",
        user_name="Platform Admin",
    )


def _data(result: ToolResult) -> dict:
    return result.structured_content or {}


def _is_error(result: ToolResult) -> bool:
    return "error" in _data(result)


@pytest.mark.asyncio
async def test_list_files_calls_canonical_rest(context: MCPContext) -> None:
    with patch.object(
        code_editor,
        "call_rest",
        AsyncMock(return_value=(200, {"files": ["workflows/b.py", "apps/a.tsx"]})),
    ) as call:
        result = await code_editor.bifrost_list_files(context, "workflows/")

    assert _data(result) == {
        "files": [{"path": "apps/a.tsx"}, {"path": "workflows/b.py"}],
        "count": 2,
    }
    call.assert_awaited_once_with(
        context,
        "POST",
        "/api/files/list",
        json_body={
            "directory": "workflows/",
            "location": "workspace",
            "mode": "cloud",
        },
    )


@pytest.mark.asyncio
async def test_search_files_maps_rest_results(context: MCPContext) -> None:
    response = {
        "results": [
            {
                "file_path": "workflows/a.py",
                "line": 4,
                "column": 2,
                "match_text": "  async def run():",
                "context_before": "@workflow",
                "context_after": "    pass",
            }
        ],
        "total_matches": 1,
        "files_searched": 3,
        "truncated": False,
        "search_time_ms": 2,
    }
    with patch.object(
        code_editor,
        "call_rest",
        AsyncMock(return_value=(200, response)),
    ) as call:
        result = await code_editor.bifrost_search_files(
            context,
            "async def",
            path="workflows/*.py",
            max_results=5,
        )

    assert _data(result)["matches"][0]["line_number"] == 4
    assert _data(result)["files_searched"] == 3
    assert call.await_args.kwargs["json_body"] == {
        "query": "async def",
        "case_sensitive": True,
        "is_regex": True,
        "include_pattern": "workflows/*.py",
        "max_results": 5,
    }


@pytest.mark.asyncio
async def test_search_files_returns_rest_validation_error(context: MCPContext) -> None:
    with patch.object(
        code_editor,
        "call_rest",
        AsyncMock(
            return_value=(
                400,
                {"detail": "Invalid regex pattern: unterminated character set"},
            )
        ),
    ):
        result = await code_editor.bifrost_search_files(context, "[")

    assert _is_error(result)
    assert "Invalid regex" in _data(result)["error"]


@pytest.mark.asyncio
async def test_read_file_selects_inclusive_line_range(context: MCPContext) -> None:
    with patch.object(
        code_editor,
        "call_rest",
        AsyncMock(return_value=(200, {"content": "one\ntwo\nthree\nfour"})),
    ):
        result = await code_editor.bifrost_read_file(
            context,
            "workflows/a.py",
            start_line=2,
            end_line=3,
        )

    assert _data(result) == {
        "path": "workflows/a.py",
        "start_line": 2,
        "end_line": 3,
        "total_lines": 4,
        "content": "2: two\n3: three",
        "raw_content": "two\nthree",
    }


@pytest.mark.asyncio
async def test_stat_and_exists_use_separate_canonical_routes(context: MCPContext) -> None:
    bridge = AsyncMock(
        side_effect=[
            (200, {"path": "a.txt", "exists": True, "version": "sha256:abc"}),
            (200, {"exists": True}),
        ]
    )
    with patch.object(code_editor, "call_rest", bridge):
        stat = await code_editor.bifrost_stat_file(context, "a.txt")
        exists = await code_editor.bifrost_exists_file(context, "a.txt")

    assert _data(stat)["version"] == "sha256:abc"
    assert _data(exists) == {"path": "a.txt", "exists": True}
    assert bridge.await_args_list[0].args[2] == "/api/files/stat"
    assert bridge.await_args_list[1].args[2] == "/api/files/exists"


@pytest.mark.asyncio
async def test_write_file_forwards_conflict_controls_and_allows_empty_content(
    context: MCPContext,
) -> None:
    bridge = AsyncMock(
        side_effect=[
            (200, {"path": "notes.txt", "exists": False}),
            (204, None),
        ]
    )
    with patch.object(code_editor, "call_rest", bridge):
        result = await code_editor.bifrost_write_file(
            context,
            "notes.txt",
            "",
            create_only=True,
        )

    assert _data(result) == {
        "success": True,
        "path": "notes.txt",
        "created": True,
    }
    assert bridge.await_args_list[1].kwargs["json_body"] == {
        "path": "notes.txt",
        "content": "",
        "binary": False,
        "expected_version": None,
        "create_only": True,
        "location": "workspace",
        "mode": "cloud",
    }


@pytest.mark.asyncio
async def test_write_file_preserves_structured_rest_conflict(context: MCPContext) -> None:
    bridge = AsyncMock(
        side_effect=[
            (200, {"path": "notes.txt", "exists": True}),
            (
                409,
                {
                    "detail": {
                        "reason": "version_conflict",
                        "message": "File changed after it was read.",
                        "current_version": "sha256:new",
                    }
                },
            ),
        ]
    )
    with patch.object(code_editor, "call_rest", bridge):
        result = await code_editor.bifrost_write_file(
            context,
            "notes.txt",
            "new",
            expected_version="sha256:old",
        )

    assert _is_error(result)
    assert _data(result)["status_code"] == 409
    assert _data(result)["body"]["detail"]["reason"] == "version_conflict"


@pytest.mark.asyncio
async def test_patch_file_forwards_deactivation_resolution(context: MCPContext) -> None:
    response = {
        "path": "workflows/a.py",
        "version": "sha256:new",
        "lines_changed": 1,
        "content_modified": False,
        "needs_indexing": False,
        "diagnostics": [],
    }
    with patch.object(
        code_editor,
        "call_rest",
        AsyncMock(return_value=(200, response)),
    ) as call:
        result = await code_editor.bifrost_patch_file(
            context,
            "workflows/a.py",
            "old",
            "new",
            expected_version="sha256:old",
            force_deactivation=True,
            replacements={"workflow-id": "renamed"},
            workflows_to_deactivate=["other-id"],
        )

    assert _data(result)["success"] is True
    assert call.await_args.kwargs["json_body"] == {
        "path": "workflows/a.py",
        "old_string": "old",
        "new_string": "new",
        "expected_version": "sha256:old",
        "force_deactivation": True,
        "replacements": {"workflow-id": "renamed"},
        "workflows_to_deactivate": ["other-id"],
    }


@pytest.mark.asyncio
async def test_delete_file_calls_rest(context: MCPContext) -> None:
    with patch.object(
        code_editor,
        "call_rest",
        AsyncMock(return_value=(204, None)),
    ) as call:
        result = await code_editor.bifrost_delete_file(
            context,
            "notes.txt",
            expected_version="sha256:abc",
        )

    assert _data(result) == {"success": True, "path": "notes.txt"}
    assert call.await_args.kwargs["json_body"] == {
        "path": "notes.txt",
        "expected_version": "sha256:abc",
        "location": "workspace",
        "mode": "cloud",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function", "kwargs", "message"),
    [
        (code_editor.bifrost_read_file, {"path": ""}, "path is required"),
        (code_editor.bifrost_write_file, {"path": "", "content": "x"}, "path is required"),
        (
            code_editor.bifrost_patch_file,
            {"path": "a", "old_string": "", "new_string": "x"},
            "old_string is required",
        ),
        (code_editor.bifrost_delete_file, {"path": ""}, "path is required"),
    ],
)
async def test_required_inputs_fail_before_rest(
    context: MCPContext,
    function,
    kwargs: dict,
    message: str,
) -> None:
    with patch.object(code_editor, "call_rest", AsyncMock()) as call:
        result = await function(context, **kwargs)

    assert _data(result)["error"] == message
    call.assert_not_awaited()


def test_registration_uses_only_canonical_names() -> None:
    assert [tool_id for tool_id, _name, _description in code_editor.TOOLS] == [
        "bifrost_list_files",
        "bifrost_search_files",
        "bifrost_read_file",
        "bifrost_stat_file",
        "bifrost_exists_file",
        "bifrost_write_file",
        "bifrost_patch_file",
        "bifrost_delete_file",
    ]
