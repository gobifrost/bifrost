"""Security and storage-routing tests for the implicit Agent Skill reader."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools.skill_assets import (
    READ_SKILL_ASSET_TOOL_ID,
    SkillAssetPathError,
    bifrost_read_agent_skill_file,
    resolve_skill_asset_key,
)


@pytest.mark.parametrize(
    "path",
    ["../secret.txt", "/etc/passwd", "references/../../secret", r"..\secret"],
)
def test_resolver_rejects_bundle_escape(path: str) -> None:
    with pytest.raises(SkillAssetPathError):
        resolve_skill_asset_key("skills/helper", path)


def test_resolver_keeps_asset_beneath_bundle() -> None:
    assert (
        resolve_skill_asset_key("skills/helper", "references/runbook.md")
        == "skills/helper/references/runbook.md"
    )


def test_asset_reader_is_hidden_from_author_picker_but_kept_for_runtime() -> None:
    from src.routers.tools import get_system_tool_ids
    from src.services.mcp_server.tool_access import MCPToolAccessService

    assert READ_SKILL_ASSET_TOOL_ID not in get_system_tool_ids()
    assert READ_SKILL_ASSET_TOOL_ID in get_system_tool_ids(include_hidden=True)
    assert READ_SKILL_ASSET_TOOL_ID in MCPToolAccessService._SYSTEM_TOOL_MAP


def test_asset_reader_is_injected_only_for_agent_scoped_bundle_execution() -> None:
    from src.services.mcp_server.tool_access import MCPToolAccessService

    agent = SimpleNamespace(
        system_tools=[],
        knowledge_sources=[],
        bundle_path="skills/helper",
    )

    assert READ_SKILL_ASSET_TOOL_ID not in MCPToolAccessService._effective_system_tool_ids(
        agent, include_bundle=False
    )
    assert READ_SKILL_ASSET_TOOL_ID in MCPToolAccessService._effective_system_tool_ids(
        agent, include_bundle=True
    )


@pytest.mark.asyncio
async def test_reader_fails_closed_without_execution_bundle() -> None:
    result = await bifrost_read_agent_skill_file(SimpleNamespace(), "references/runbook.md")
    assert result.structured_content is not None
    assert "unavailable" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_maintained_profile_reads_platform_owned_skill_asset(tmp_path) -> None:
    references = tmp_path / "references"
    references.mkdir()
    (references / "runbook.md").write_text("maintained instructions")
    context = SimpleNamespace(
        agent_bundle_path="skills/bifrost-build",
        agent_skill_root=tmp_path,
    )

    result = await bifrost_read_agent_skill_file(context, "references/runbook.md")

    assert result.structured_content is not None
    assert result.structured_content["content"] == "maintained instructions"


@pytest.mark.asyncio
async def test_maintained_profile_rejects_platform_skill_escape(tmp_path) -> None:
    context = SimpleNamespace(
        agent_bundle_path="skills/bifrost-build",
        agent_skill_root=tmp_path,
    )

    result = await bifrost_read_agent_skill_file(context, "../secret.txt")

    assert result.structured_content is not None
    assert "beneath the bundle root" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_solution_agent_reads_scoped_storage() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"scoped instructions"
    solution_id = uuid4()
    context = SimpleNamespace(
        agent_bundle_path="skills/helper",
        agent_solution_id=solution_id,
    )

    with patch(
        "src.services.solutions.storage.SolutionStorage",
        return_value=storage,
    ) as storage_cls:
        result = await bifrost_read_agent_skill_file(context, "references/runbook.md")

    storage_cls.assert_called_once_with(solution_id)
    storage.read.assert_awaited_once_with("skills/helper/references/runbook.md")
    assert result.structured_content is not None
    assert result.structured_content["content"] == "scoped instructions"


@pytest.mark.asyncio
async def test_uploaded_agent_reads_agent_owned_storage() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"uploaded instructions"
    agent_id = uuid4()
    context = SimpleNamespace(
        agent_bundle_path="skills/helper",
        agent_skill_id=agent_id,
        agent_solution_id=None,
    )

    with patch(
        "src.services.agent_skill_storage.AgentSkillStorage",
        return_value=storage,
    ) as storage_cls:
        result = await bifrost_read_agent_skill_file(context, "SKILL.md")

    storage_cls.assert_called_once_with(agent_id)
    storage.read.assert_awaited_once_with("skills/helper/SKILL.md")
    assert result.structured_content is not None
    assert result.structured_content["content"] == "uploaded instructions"


@pytest.mark.asyncio
async def test_git_synced_agent_reads_repo_storage() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"git instructions"
    context = SimpleNamespace(
        agent_bundle_path="skills/helper",
        agent_skill_in_repo=True,
        agent_solution_id=None,
    )

    with patch("src.services.repo_storage.RepoStorage", return_value=storage):
        result = await bifrost_read_agent_skill_file(context, "references/guide.md")

    storage.read.assert_awaited_once_with("skills/helper/references/guide.md")
    assert result.structured_content is not None
    assert result.structured_content["content"] == "git instructions"


@pytest.mark.asyncio
async def test_reader_rejects_oversized_asset() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"x" * 1_048_577
    context = SimpleNamespace(
        agent_bundle_path="skills/helper",
        agent_skill_id=uuid4(),
        agent_solution_id=None,
    )

    with patch(
        "src.services.agent_skill_storage.AgentSkillStorage",
        return_value=storage,
    ):
        result = await bifrost_read_agent_skill_file(context, "assets/large.bin")

    assert result.structured_content is not None
    assert "read limit" in result.structured_content["error"]
