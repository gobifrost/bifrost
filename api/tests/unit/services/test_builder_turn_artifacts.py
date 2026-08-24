from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.builder.turn_artifacts import BuilderTurnArtifactStorage


async def _chunks() -> AsyncIterator[bytes]:
    yield b"workspace"


@pytest.mark.asyncio
async def test_tool_workspace_is_execution_scoped() -> None:
    turn_id = uuid4()
    execution_id = uuid4()
    storage = BuilderTurnArtifactStorage(
        turn_id,
        2,
        settings=SimpleNamespace(),
    )
    put = AsyncMock(return_value=("a" * 64, 9))
    storage.storage = SimpleNamespace(put_object_from_chunks=put)

    result = await storage.write_tool_workspace(
        execution_id,
        _chunks(),
        max_bytes=100,
    )

    assert result == ("a" * 64, 9)
    assert storage.tool_workspace_key(execution_id) == (
        f"_solution_builder_turns/{turn_id}/2/tools/{execution_id}/workspace.zip"
    )
    assert put.await_args.args[0] == storage.tool_workspace_key(execution_id)


def test_tool_workspace_rejects_non_uuid_execution_id() -> None:
    storage = BuilderTurnArtifactStorage(
        uuid4(),
        1,
        settings=SimpleNamespace(),
    )

    with pytest.raises(ValueError):
        storage.tool_workspace_key("../another-turn")
