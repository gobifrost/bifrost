"""Worker handles summarize + tune-chat message types.

Validates the thin RabbitMQ consumer wrappers that delegate to
``run_summarizer`` / ``tuning_service``. The summarizer/tuning logic
itself is implemented in T12 and T15; this task only wires the message
plumbing and the failure-swallowing path.
"""
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.models.orm.agent_runs import AgentRun


@pytest.mark.asyncio
async def test_summarize_message_calls_summarizer():
    """handle_summarize_message extracts run_id and delegates to summarize_run."""
    from src.jobs.summarize_worker import handle_summarize_message

    run_id = uuid4()
    with patch(
        "src.jobs.summarize_worker.summarize_run", new=AsyncMock()
    ) as mock:
        await handle_summarize_message({"run_id": str(run_id)})
        mock.assert_awaited_once()
        called_run_id = mock.await_args.args[0]
        assert isinstance(called_run_id, UUID)
        assert called_run_id == run_id


@pytest.mark.asyncio
async def test_tune_chat_message_appends_and_replies():
    """handle_tune_chat_message extracts run_id+content and delegates."""
    from src.jobs.summarize_worker import handle_tune_chat_message

    run_id = uuid4()
    with patch(
        "src.jobs.summarize_worker.append_user_message_and_reply",
        new=AsyncMock(),
    ) as mock:
        await handle_tune_chat_message(
            {"run_id": str(run_id), "content": "wrong route"}
        )
        mock.assert_awaited_once()
        args = mock.await_args.args
        assert args[0] == run_id
        assert args[1] == "wrong route"


@pytest.mark.asyncio
async def test_summarize_failure_marks_run_failed(
    db_session, async_session_factory, seed_agent
):
    """A failure inside summarize_run is caught, run.summary_status = 'failed', error stored.

    The handler must NOT re-raise — that would land the message in the DLQ
    and burn retries for what is almost always a deterministic failure
    (bad LLM output / missing config). The UI exposes a regenerate button
    for recovery instead.
    """
    from src.jobs.summarize_worker import handle_summarize_message

    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=1,
        summary_status="pending",
    )
    db_session.add(run)
    # Use commit so the handler's separate session sees the row.
    # Cleanup happens at the end of the test below.
    await db_session.commit()

    try:
        with patch(
            "src.jobs.summarize_worker.summarize_run",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            # Should not raise — errors are swallowed to prevent retry loops.
            await handle_summarize_message(
                {"run_id": str(run.id)},
                session_factory=async_session_factory,
            )

        # Verify in a fresh session so we see the handler's commit.
        async with async_session_factory() as verify:
            reloaded = (
                await verify.execute(
                    select(AgentRun).where(AgentRun.id == run.id)
                )
            ).scalar_one()
            assert reloaded.summary_status == "failed"
            assert reloaded.summary_error is not None
            assert "boom" in reloaded.summary_error
    finally:
        # Manual cleanup since we committed past the db_session rollback boundary.
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                AgentRun.__table__.delete().where(AgentRun.id == run.id)
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_enqueue_summarize_publishes_correct_queue():
    """Live path: enqueue_summarize publishes to 'agent-summarization'."""
    from src.services.execution.run_summarizer import enqueue_summarize

    run_id = uuid4()
    with patch(
        "src.services.execution.run_summarizer.publish_message",
        new=AsyncMock(),
    ) as mock:
        await enqueue_summarize(run_id)
        mock.assert_awaited_once()
        args = mock.await_args.args
        assert args[0] == "agent-summarization"
        assert args[1]["run_id"] == str(run_id)


def test_summarize_consumer_uses_prefetch_one():
    """Live consumer must run with prefetch=1 so fleet-wide concurrency
    equals the worker pod count, not pods × max_concurrency.

    Regression guard: bumping this value back up silently melts OpenRouter
    and re-introduces the burst-429 problem that cost ~525 runs in prod
    on 2026-04-24."""
    from src.jobs.summarize_worker import SummarizeBackfillConsumer, SummarizeConsumer

    live = SummarizeConsumer()
    backfill = SummarizeBackfillConsumer()
    assert live.prefetch_count == 1
    assert backfill.prefetch_count == 1
    assert live.queue_name == "agent-summarization"
    assert backfill.queue_name == "agent-summarization-backfill"


@pytest.mark.asyncio
async def test_backfill_publishes_to_backfill_queue(async_session_factory):
    """Regression guard: backfill must route to the dedicated backfill queue,
    not the live ``agent-summarization`` queue that serves just-finished runs.
    Publishing to the live queue causes a 2000-run bulk op to block live
    traffic for the full drain duration."""
    from unittest.mock import patch
    from uuid import uuid4

    from src.core.principal import UserPrincipal
    from src.models.contracts.agent_runs import BackfillSummariesRequest
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.agents import Agent
    from src.routers.agent_runs import backfill_summaries

    agent_id = uuid4()
    run_id = uuid4()

    # Seed an agent + a pending run so the endpoint has something to enqueue.
    async with async_session_factory() as db:
        db.add(
            Agent(
                id=agent_id,
                name=f"Backfill router unit {uuid4().hex[:8]}",
                description="backfill router unit test",
                system_prompt="test",
                access_level="authenticated",
                created_by="test",
            )
        )
        db.add(
            AgentRun(
                id=run_id,
                agent_id=agent_id,
                trigger_type="test",
                status="completed",
                iterations_used=1,
                tokens_used=10,
                summary_status="pending",
            )
        )
        await db.commit()

    admin = UserPrincipal(
        user_id=uuid4(),
        email="admin@test.local",
        organization_id=None,
        name="Test Admin",
        is_superuser=True,
    )

    try:
        async with async_session_factory() as db:
            with patch(
                "src.jobs.rabbitmq.publish_message",
                new=AsyncMock(),
            ) as pub:
                result = await backfill_summaries(
                    request=BackfillSummariesRequest(
                        agent_id=agent_id,
                        statuses=["pending"],
                        limit=100,
                        dry_run=False,
                    ),
                    db=db,
                    user=admin,
                )

            assert result.queued == 1
            assert pub.await_count == 1
            call = pub.await_args_list[0]
            assert call.args[0] == "agent-summarization-backfill"
            assert call.args[1]["run_id"] == str(run_id)
    finally:
        async with async_session_factory() as db:
            await db.execute(
                AgentRun.__table__.delete().where(AgentRun.id == run_id)
            )
            await db.execute(Agent.__table__.delete().where(Agent.id == agent_id))
            await db.commit()


@pytest.mark.asyncio
async def test_enqueue_tune_chat_publishes_correct_queue():
    """enqueue_tune_chat publishes to 'agent-tuning-chat' with content."""
    from src.services.execution.tuning_service import enqueue_tune_chat

    run_id = uuid4()
    with patch(
        "src.services.execution.tuning_service.publish_message",
        new=AsyncMock(),
    ) as mock:
        await enqueue_tune_chat(run_id, "hello")
        mock.assert_awaited_once()
        args = mock.await_args.args
        assert args[0] == "agent-tuning-chat"
        assert args[1]["run_id"] == str(run_id)
        assert args[1]["content"] == "hello"
