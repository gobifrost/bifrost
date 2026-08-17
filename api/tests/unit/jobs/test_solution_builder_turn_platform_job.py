from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.solution_builder_turn import (
    SolutionBuilderTurnPayload,
    cancel_solution_builder_turn,
)


@pytest.mark.asyncio
async def test_builder_turn_cancellation_targets_projection_from_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_job_id = uuid4()
    turn_id = uuid4()
    platform_job = SimpleNamespace(id=platform_job_id)
    db = AsyncMock()
    db.get.return_value = platform_job

    @asynccontextmanager
    async def db_context():
        yield db

    cancel_projection = AsyncMock()
    cancel_external = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.solution_builder_turn.get_db_context", db_context
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_builder_turn._cancel_turn_projection",
        cancel_projection,
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_builder_turn.cancel_external_sandbox_run",
        cancel_external,
    )

    await cancel_solution_builder_turn(
        platform_job_id,
        SolutionBuilderTurnPayload(
            solution_id=uuid4(),
            session_id=uuid4(),
            turn_id=turn_id,
            base_revision_id=uuid4(),
            message="stop",
        ),
    )

    cancel_projection.assert_awaited_once_with(turn_id)
    cancel_external.assert_awaited_once_with(platform_job)
