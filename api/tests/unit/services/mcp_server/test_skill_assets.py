"""Security and storage-routing tests for the implicit bundle reader."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools.skill_assets import (
    READ_SKILL_ASSET_TOOL_ID,
    SkillAssetPathError,
    read_skill_asset,
    resolve_skill_asset_key,
)


@pytest.mark.parametrize(
    "path",
    ["../secret.txt", "/etc/passwd", "references/../../secret", r"..\\secret"],
)
def test_resolver_rejects_bundle_escape(path: str) -> None:
    with pytest.raises(SkillAssetPathError):
        resolve_skill_asset_key("agents/helper", path)


def test_resolver_keeps_asset_beneath_bundle() -> None:
    assert (
        resolve_skill_asset_key("agents/helper", "references/runbook.md")
        == "agents/helper/references/runbook.md"
    )


def test_asset_reader_is_hidden_from_author_picker_but_kept_for_runtime() -> None:
    from src.routers.tools import get_system_tool_ids
    from src.services.mcp_server.server import get_system_tools
    from src.services.mcp_server.tool_access import MCPToolAccessService

    registered = {
        tool["id"]: tool for tool in get_system_tools()
    }
    assert registered[READ_SKILL_ASSET_TOOL_ID]["hidden"] is True
    assert READ_SKILL_ASSET_TOOL_ID not in get_system_tool_ids()
    assert READ_SKILL_ASSET_TOOL_ID in MCPToolAccessService._SYSTEM_TOOL_MAP


@pytest.mark.asyncio
async def test_reader_fails_closed_without_execution_bundle() -> None:
    result = await read_skill_asset(SimpleNamespace(), "references/runbook.md")
    assert result.structured_content is not None
    assert "unavailable" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_solution_agent_reads_scoped_storage() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"scoped instructions"
    solution_id = uuid4()
    context = SimpleNamespace(
        agent_bundle_path="agents/helper",
        agent_solution_id=solution_id,
    )

    with patch(
        "src.services.solutions.storage.SolutionStorage",
        return_value=storage,
    ) as storage_cls:
        result = await read_skill_asset(context, "references/runbook.md")

    storage_cls.assert_called_once_with(solution_id)
    storage.read.assert_awaited_once_with("agents/helper/references/runbook.md")
    assert result.structured_content is not None
    assert result.structured_content["content"] == "scoped instructions"


@pytest.mark.asyncio
async def test_uploaded_agent_reads_agent_owned_storage() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"uploaded instructions"
    agent_id = uuid4()
    context = SimpleNamespace(
        agent_bundle_path="agents/helper",
        agent_skill_id=agent_id,
        agent_solution_id=None,
    )

    with patch(
        "src.services.agent_skill_storage.AgentSkillStorage",
        return_value=storage,
    ) as storage_cls:
        result = await read_skill_asset(context, "SKILL.md")

    storage_cls.assert_called_once_with(agent_id)
    storage.read.assert_awaited_once_with("agents/helper/SKILL.md")
    assert result.structured_content is not None
    assert result.structured_content["content"] == "uploaded instructions"


@pytest.mark.asyncio
async def test_git_synced_agent_reads_repo_storage() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"git instructions"
    context = SimpleNamespace(
        agent_bundle_path="agents/helper",
        agent_skill_in_repo=True,
        agent_solution_id=None,
    )

    with patch(
        "src.services.repo_storage.RepoStorage",
        return_value=storage,
    ):
        result = await read_skill_asset(context, "references/guide.md")

    storage.read.assert_awaited_once_with("agents/helper/references/guide.md")
    assert result.structured_content is not None
    assert result.structured_content["content"] == "git instructions"


@pytest.mark.asyncio
async def test_reader_rejects_oversized_asset() -> None:
    storage = AsyncMock()
    storage.read.return_value = b"x" * 1_048_577
    context = SimpleNamespace(
        agent_bundle_path="agents/helper",
        agent_skill_id=uuid4(),
        agent_solution_id=None,
    )

    with patch(
        "src.services.agent_skill_storage.AgentSkillStorage",
        return_value=storage,
    ):
        result = await read_skill_asset(context, "assets/large.bin")

    assert result.structured_content is not None
    assert "read limit" in result.structured_content["error"]
