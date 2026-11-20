"""Receipt-backed AI usage persistence remains exactly-once across retries."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.ai_usage import AIUsage
from src.services.ai_usage_service import (
    persist_ai_usage_receipt,
    record_ai_usage,
)


@pytest.fixture
def priced_usage(monkeypatch):
    """Keep receipt tests about receipts rather than the pricing catalog."""
    pricing = (
        Decimal("5"),
        Decimal("15"),
        None,
        None,
    )
    monkeypatch.setattr(
        "src.services.ai_usage_service.get_cached_pricing",
        AsyncMock(return_value=pricing),
    )


async def _record(
    session: AsyncSession,
    mock_redis,
    *,
    receipt_id: UUID | None,
    agent_run_id: UUID,
) -> None:
    await record_ai_usage(
        session=session,
        redis_client=mock_redis,
        provider="openai",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=25,
        agent_run_id=agent_run_id,
        receipt_id=receipt_id,
    )


@pytest.mark.asyncio
async def test_duplicate_receipt_inserts_one_usage_row(
    db_session: AsyncSession, mock_redis, priced_usage
):
    receipt_id = uuid4()
    agent_run_id = uuid4()

    await _record(
        db_session, mock_redis, receipt_id=receipt_id, agent_run_id=agent_run_id
    )
    await _record(
        db_session, mock_redis, receipt_id=receipt_id, agent_run_id=agent_run_id
    )

    rows = list(
        await db_session.scalars(
            select(AIUsage).where(AIUsage.receipt_id == receipt_id)
        )
    )

    assert len(rows) == 1
    assert rows[0].agent_run_id == agent_run_id


@pytest.mark.asyncio
async def test_distinct_receipts_insert_distinct_usage_rows(
    db_session: AsyncSession, mock_redis, priced_usage
):
    agent_run_id = uuid4()
    receipts = (uuid4(), uuid4())

    for receipt_id in receipts:
        await _record(
            db_session, mock_redis, receipt_id=receipt_id, agent_run_id=agent_run_id
        )

    rows = list(
        await db_session.scalars(
            select(AIUsage).where(AIUsage.receipt_id.in_(receipts))
        )
    )

    assert {row.receipt_id for row in rows} == set(receipts)


@pytest.mark.asyncio
async def test_usage_without_receipt_keeps_legacy_multiple_row_behavior(
    db_session: AsyncSession, mock_redis, priced_usage
):
    agent_run_id = uuid4()

    await _record(db_session, mock_redis, receipt_id=None, agent_run_id=agent_run_id)
    await _record(db_session, mock_redis, receipt_id=None, agent_run_id=agent_run_id)

    rows = list(
        await db_session.scalars(
            select(AIUsage).where(AIUsage.agent_run_id == agent_run_id)
        )
    )

    assert len(rows) == 2
    assert {row.receipt_id for row in rows} == {None}


@pytest.mark.asyncio
async def test_receipt_persistence_error_reaches_caller():
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("usage insert rejected")
    redis_client = AsyncMock()

    with patch(
        "src.services.ai_usage_service.get_cached_pricing",
        new=AsyncMock(return_value=(Decimal("5"), Decimal("15"), None, None)),
    ):
        with pytest.raises(RuntimeError, match="usage insert rejected"):
            await record_ai_usage(
                session=session,
                redis_client=redis_client,
                provider="openai",
                model="gpt-4o",
                input_tokens=100,
                output_tokens=25,
                agent_run_id=uuid4(),
                receipt_id=uuid4(),
            )


@pytest.mark.asyncio
async def test_persist_receipt_commits_retry_unit():
    session = AsyncMock()
    session_factory = MagicMock()
    redis_client = AsyncMock()
    receipt_id = uuid4()
    recorded = AsyncMock()

    async def retry(_factory, unit, **_kwargs) -> None:
        await unit(session)

    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=redis_client)),
        patch("src.core.database.get_session_factory", return_value=session_factory),
        patch("src.core.database_retry.run_with_database_retry", new=retry),
        patch("src.services.ai_usage_service.record_ai_usage", new=recorded),
    ):
        await persist_ai_usage_receipt(
            receipt_id,
            provider="openai",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=25,
            agent_run_id=uuid4(),
        )

    assert recorded.await_args.kwargs["receipt_id"] == receipt_id
    assert recorded.await_args.kwargs["redis_client"] is redis_client
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_receipt_write_drains_before_propagating():
    session = AsyncMock()
    session_factory = MagicMock()
    redis_client = AsyncMock()
    recorded = AsyncMock()
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def retry(_factory, unit, **_kwargs) -> None:
        await unit(session)
        started.set()
        await release.wait()
        completed.set()

    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=redis_client)),
        patch("src.core.database.get_session_factory", return_value=session_factory),
        patch("src.core.database_retry.run_with_database_retry", new=retry),
        patch("src.services.ai_usage_service.record_ai_usage", new=recorded),
    ):
        pending = asyncio.create_task(
            persist_ai_usage_receipt(
                uuid4(),
                provider="openai",
                model="gpt-4o",
                input_tokens=100,
                output_tokens=25,
                agent_run_id=uuid4(),
            )
        )
        await started.wait()
        pending.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await pending

    assert completed.is_set()
    session.commit.assert_awaited_once()
