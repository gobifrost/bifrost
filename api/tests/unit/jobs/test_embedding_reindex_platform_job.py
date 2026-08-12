from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.jobs.platform.embedding_reindex import (
    EmbeddingReindexPayload,
    run_embedding_reindex,
)
from src.services.embeddings.reindex import ReindexOutcome


def _context() -> AsyncMock:
    context = AsyncMock()
    context.job_id = uuid4()
    return context


@pytest.mark.asyncio
async def test_embedding_platform_job_returns_measured_outcome() -> None:
    context = _context()
    outcome = ReindexOutcome("succeeded", 9, 10, 1)
    with patch(
        "src.services.embeddings.reindex.run_reindex",
        AsyncMock(return_value=outcome),
    ):
        result = await run_embedding_reindex(
            context,
            EmbeddingReindexPayload(notification_id="notification-1"),
        )

    assert result == {
        "notification_id": "notification-1",
        "processed": 9,
        "total": 10,
        "failed_batches": 1,
    }
    assert context.report.await_count == 2


@pytest.mark.asyncio
async def test_embedding_platform_job_propagates_failed_outcome() -> None:
    with patch(
        "src.services.embeddings.reindex.run_reindex",
        AsyncMock(return_value=ReindexOutcome("failed", 0, 3, 3)),
    ), pytest.raises(PlatformJobFailure, match="0/3"):
        await run_embedding_reindex(
            _context(),
            EmbeddingReindexPayload(notification_id="notification-1"),
        )


@pytest.mark.asyncio
async def test_embedding_platform_job_propagates_cancelled_outcome() -> None:
    with patch(
        "src.services.embeddings.reindex.run_reindex",
        AsyncMock(return_value=ReindexOutcome("cancelled", 1, 3, 0)),
    ), pytest.raises(PlatformJobCancelled):
        await run_embedding_reindex(
            _context(),
            EmbeddingReindexPayload(notification_id="notification-1"),
        )
