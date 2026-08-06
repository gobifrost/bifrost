from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobDeferred
from src.jobs.platform.solution_build import (
    SolutionBuildPayload,
    run_solution_build,
)


@pytest.mark.asyncio
async def test_solution_build_dispatches_exact_job_then_releases_slot() -> None:
    build_job_id = uuid4()
    context = AsyncMock()
    with patch(
        "src.jobs.rabbitmq.publish_message",
        AsyncMock(),
    ) as publish, pytest.raises(PlatformJobDeferred, match="isolated build runner"):
        await run_solution_build(
            context,
            SolutionBuildPayload(build_job_id=build_job_id),
        )

    publish.assert_awaited_once_with(
        "solution-builds",
        {"job_id": str(build_job_id)},
    )
    context.report.assert_awaited_once()
