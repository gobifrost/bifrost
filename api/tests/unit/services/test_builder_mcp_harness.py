"""Builder MCP bridge tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.builder.mcp_harness import BuilderMCPHarness, BuilderMCPHarnessError


def _harness() -> tuple[BuilderMCPHarness, AsyncMock]:
    db = AsyncMock()
    harness = BuilderMCPHarness(
        db,
        user_id=uuid4(),
    )
    return harness, db


def _turn(*, deploy_job_id=None):
    turn = MagicMock()
    turn.id = uuid4()
    turn.base_revision_id = uuid4()
    turn.output_revision_id = uuid4()
    turn.deploy_job_id = deploy_job_id
    return turn


@pytest.mark.asyncio
async def test_intermediate_mcp_mutation_commits_revision_without_building():
    harness, db = _harness()
    session = MagicMock(id=uuid4(), solution_id=uuid4())
    authorized = SimpleNamespace(session=session)
    turn = _turn()
    turn_service = MagicMock()
    turn_service.run_turn = AsyncMock(return_value=turn)

    with (
        patch.object(harness, "_authorize", new=AsyncMock(return_value=authorized)),
        patch(
            "src.services.builder.mcp_harness.BuilderTurnService",
            return_value=turn_service,
        ),
        patch(
            "src.services.builder.agent_turns.enqueue_builder_turn_deploy",
            new=AsyncMock(),
        ) as enqueue,
    ):
        result = await harness.execute(
            tool_name="write_file",
            builder_session_id=session.id,
            arguments={"path": "README.md", "content": "Hi"},
        )

    assert result["revision_created"] is True
    assert result["finalized"] is False
    assert result["deploy_job_id"] is None
    enqueue.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_mcp_mutation_enqueues_one_build():
    harness, _db = _harness()
    session = MagicMock(id=uuid4(), solution_id=uuid4())
    authorized = SimpleNamespace(session=session)
    deploy_job_id = uuid4()
    turn = _turn(deploy_job_id=deploy_job_id)
    turn_service = MagicMock()
    turn_service.run_turn = AsyncMock(return_value=turn)

    with (
        patch.object(harness, "_authorize", new=AsyncMock(return_value=authorized)),
        patch(
            "src.services.builder.mcp_harness.BuilderTurnService",
            return_value=turn_service,
        ),
        patch(
            "src.services.builder.agent_turns.enqueue_builder_turn_deploy",
            new=AsyncMock(),
        ) as enqueue,
    ):
        result = await harness.execute(
            tool_name="write_file",
            builder_session_id=session.id,
            arguments={
                "path": "README.md",
                "content": "Ready",
                "finalize": True,
            },
        )

    assert result["revision_created"] is True
    assert result["finalized"] is True
    assert result["deploy_job_id"] == str(deploy_job_id)
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_check_is_non_mutating_and_cannot_finalize():
    harness, _db = _harness()
    session = MagicMock(id=uuid4(), solution_id=uuid4())
    authorized = SimpleNamespace(session=session)
    read_only = AsyncMock(return_value={"valid": True})

    with (
        patch.object(harness, "_authorize", new=AsyncMock(return_value=authorized)),
        patch.object(harness, "_read_only", new=read_only),
    ):
        result = await harness.execute(
            tool_name="test_solution_build",
            builder_session_id=session.id,
            arguments={},
        )

        with pytest.raises(
            BuilderMCPHarnessError,
            match="finalize is supported only by mutating Builder tools",
        ):
            await harness.execute(
                tool_name="test_solution_build",
                builder_session_id=session.id,
                arguments={"finalize": True},
            )

    assert result == {"valid": True}
    read_only.assert_awaited_once()
