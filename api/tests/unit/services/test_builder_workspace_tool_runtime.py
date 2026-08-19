from pathlib import Path

import pytest

from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot
from src.services.builder.workspace_tool_runtime import (
    execute_builder_workspace_tool,
)


@pytest.mark.asyncio
async def test_workspace_tool_runtime_mutates_and_reads_same_workspace(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceRoot(tmp_path, WorkspaceLimits())

    written = await execute_builder_workspace_tool(
        workspace=workspace,
        bundle_path="skills/bifrost-build",
        name="write_file",
        arguments={"path": "apps/portal.tsx", "content": "export default 1;"},
    )
    read = await execute_builder_workspace_tool(
        workspace=workspace,
        bundle_path="skills/bifrost-build",
        name="read_file",
        arguments={"path": "apps/portal.tsx"},
    )

    assert written.structured_content == {"path": "apps/portal.tsx"}
    assert read.structured_content == {
        "path": "apps/portal.tsx",
        "content": "export default 1;",
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_workspace_tool_runtime_reads_skill_from_solution_bundle(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "expense-tracker"
    skill.mkdir(parents=True)
    (skill / "reference.md").write_text("portable instructions", encoding="utf-8")
    workspace = WorkspaceRoot(tmp_path, WorkspaceLimits())

    result = await execute_builder_workspace_tool(
        workspace=workspace,
        bundle_path="skills/expense-tracker",
        name="bifrost_read_agent_skill_file",
        arguments={"path": "reference.md"},
    )

    assert result.structured_content == {
        "path": "reference.md",
        "encoding": "utf-8",
        "content": "portable instructions",
    }


@pytest.mark.asyncio
async def test_workspace_tool_runtime_rejects_skill_traversal(tmp_path: Path) -> None:
    workspace = WorkspaceRoot(tmp_path, WorkspaceLimits())

    result = await execute_builder_workspace_tool(
        workspace=workspace,
        bundle_path="skills/expense-tracker",
        name="bifrost_read_agent_skill_file",
        arguments={"path": "../secret"},
    )

    assert result.structured_content == {
        "error": "path must stay beneath the bundle root"
    }


@pytest.mark.asyncio
async def test_workspace_tool_runtime_returns_model_visible_unknown_tool_error(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceRoot(tmp_path, WorkspaceLimits())

    result = await execute_builder_workspace_tool(
        workspace=workspace,
        bundle_path=None,
        name="shell",
        arguments={},
    )

    assert result.structured_content == {
        "error": "Builder workspace tool 'shell' is unavailable"
    }
