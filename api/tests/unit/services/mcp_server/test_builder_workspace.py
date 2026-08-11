from types import SimpleNamespace

import pytest

from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot
from src.services.mcp_server.tools.builder_workspace import (
    BUILDER_WORKSPACE_TOOL_IDS,
    apply_patch,
    read_file,
    write_file,
)


@pytest.mark.asyncio
async def test_workspace_tools_mutate_only_the_supplied_root(tmp_path) -> None:
    root = WorkspaceRoot(tmp_path / "workspace", WorkspaceLimits())
    root.root.mkdir()
    context = SimpleNamespace(builder_workspace=root)

    written = await write_file(context, "apps/demo.tsx", "export const n = 1;")
    patched = await apply_patch(
        context,
        "apps/demo.tsx",
        "n = 1",
        "n = 2",
    )
    read = await read_file(context, "apps/demo.tsx")

    assert written.structured_content == {"path": "apps/demo.tsx"}
    assert patched.structured_content == {
        "path": "apps/demo.tsx",
        "replacements": 1,
    }
    assert read.structured_content is not None
    assert read.structured_content["content"] == "export const n = 2;"


@pytest.mark.asyncio
async def test_workspace_tools_fail_closed_without_builder_scope() -> None:
    result = await write_file(
        SimpleNamespace(builder_workspace=None),
        "outside.txt",
        "no",
    )
    assert result.structured_content is not None
    assert "unavailable outside a builder turn" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_workspace_tools_reject_traversal(tmp_path) -> None:
    root = WorkspaceRoot(tmp_path / "workspace", WorkspaceLimits())
    root.root.mkdir()
    result = await write_file(
        SimpleNamespace(builder_workspace=root),
        "../escape.txt",
        "no",
    )
    assert result.structured_content is not None
    assert ".." in result.structured_content["error"]
    assert not (tmp_path / "escape.txt").exists()


def test_builder_workspace_tool_set_is_exact() -> None:
    assert BUILDER_WORKSPACE_TOOL_IDS == (
        "list_files",
        "read_file",
        "search_text",
        "write_file",
        "apply_patch",
        "delete_file",
        "make_directory",
        "validate_solution",
    )
