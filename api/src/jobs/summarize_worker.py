"""RabbitMQ consumer wrappers for post-run summarization and per-flag tuning chat.

Two queues, intentionally separate from ``agent-runs``:

- ``agent-summarization`` — short, deterministic LLM call to extract
  asked/did/confidence/metadata from a completed run. Failures are
  swallowed (run.summary_status = "failed", error stored) to prevent
  RabbitMQ retry loops on bad LLM output; the UI exposes a regenerate
  button for recovery.
- ``agent-tuning-chat`` — appends a user turn to a flagged-run
  conversation and asks the tuning model for a reply.

Both handlers are exposed as standalone async functions so they can be
unit-tested without a RabbitMQ connection. The ``_Consumer`` classes wrap
them in :class:`BaseConsumer` for the worker bootstrap to register.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.core.database import get_session_factory
from src.jobs.queue_names import (
    SUMMARIZE_BACKFILL_QUEUE as SUMMARIZE_BACKFILL_QUEUE,
    SUMMARIZE_QUEUE as SUMMARIZE_QUEUE,
    TUNE_CHAT_QUEUE as TUNE_CHAT_QUEUE,
)
from src.jobs.rabbitmq import BaseConsumer
from src.models.orm.agent_runs import AgentRun

if TYPE_CHECKING:
    from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation

logger = logging.getLogger(__name__)

# Re-export for the worker bootstrap; keeps queue-name knowledge local
# to the message-handling module.
__all__ = [
    "SUMMARIZE_BACKFILL_QUEUE",
    "SUMMARIZE_QUEUE",
    "TUNE_CHAT_QUEUE",
    "handle_summarize_message",
    "handle_tune_chat_message",
    "SummarizeBackfillConsumer",
    "SummarizeConsumer",
    "TuneChatConsumer",
]


async def summarize_run(
    run_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Lazy forwarding wrapper for the heavyweight summarization service.

    Importing the real service is intentionally deferred until a summary
    message is handled. After the first real job the service stays resident in
    the worker process, which is fine: the memory win is before summary/tuning
    traffic arrives.
    """
    from src.services.execution.run_summarizer import summarize_run as _summarize_run

    await _summarize_run(run_id, session_factory)


async def append_user_message_and_reply(
    run_id: UUID,
    content: str,
    db: AsyncSession,
) -> AgentRunFlagConversation:
    """Lazy forwarding wrapper for the heavyweight tuning service."""
    from src.services.execution.tuning_service import (
        append_user_message_and_reply as _append_user_message_and_reply,
    )

    return await _append_user_message_and_reply(run_id, content, db)


async def handle_summarize_message(
    message: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Consume ``{"run_id": "...", "backfill_job_id"?: "..."}`` and summarize.

    On failure the exception is *intentionally* swallowed: a permanent
    failure (bad LLM output, missing config) should not cycle through
    RabbitMQ retries forever. Instead we mark ``summary_status='failed'``
    and store the error so the UI can offer a regenerate button.

    If a ``backfill_job_id`` is present the worker additionally increments
    the SummaryBackfillJob counters and broadcasts progress.
    """
    from src.services.execution.backfill_tracker import record_backfill_outcome

    factory = session_factory or get_session_factory()
    run_id = UUID(message["run_id"])
    raw_job_id = message.get("backfill_job_id")
    job_id: UUID | None = UUID(raw_job_id) if raw_job_id else None
    succeeded = True
    try:
        await summarize_run(run_id, factory)
    except Exception as exc:
        logger.exception("Summarization failed for run %s", run_id)
        succeeded = False
        try:
            async with factory() as db:
                run = (
                    await db.execute(
                        select(AgentRun).where(AgentRun.id == run_id)
                    )
                ).scalar_one_or_none()
                if run is not None:
                    run.summary_status = "failed"
                    run.summary_error = str(exc)[:500]
                    await db.commit()
        except Exception:
            logger.exception(
                "Failed to record summary failure for run %s", run_id
            )
    else:
        # `summarize_run` may have marked the run as failed without raising
        # (invalid JSON, non-dict response). Mirror that into the job counters.
        try:
            async with factory() as db:
                run = (
                    await db.execute(
                        select(AgentRun).where(AgentRun.id == run_id)
                    )
                ).scalar_one_or_none()
                if run is not None and run.summary_status == "failed":
                    succeeded = False
        except Exception:
            logger.exception(
                "Failed to re-check summary status for run %s", run_id
            )

    if job_id is not None:
        await record_backfill_outcome(
            job_id=job_id,
            run_id=run_id,
            succeeded=succeeded,
            session_factory=factory,
        )


async def handle_tune_chat_message(
    message: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Consume ``{"run_id": "...", "content": "..."}`` and append a turn.

    Unlike ``handle_summarize_message``, this re-raises on failure so the
    message lands in the DLQ — the user is actively waiting on a chat
    reply and silently dropping it would be worse than surfacing the
    error in observability.
    """
    factory = session_factory or get_session_factory()
    run_id = UUID(message["run_id"])
    content = message["content"]
    async with factory() as db:
        await append_user_message_and_reply(run_id, content, db)


class SummarizeConsumer(BaseConsumer):
    """Consumer for live ``agent-summarization`` traffic.

    ``prefetch_count=1`` so each pod processes one message at a time.
    Fleet-wide concurrency equals the number of worker pods — the admin
    controls burst load against the LLM provider by scaling pods, not by
    tuning a hidden per-pod concurrency knob. A 6-pod fleet hits the LLM
    at most 6 requests-in-flight concurrently, which for a post-run
    summarizer is well inside any reasonable provider RPM headroom.
    """

    def __init__(self) -> None:
        super().__init__(
            queue_name=SUMMARIZE_QUEUE,
            prefetch_count=1,
        )

    async def process_message(self, body: dict[str, Any]) -> None:
        await handle_summarize_message(body)


class SummarizeBackfillConsumer(BaseConsumer):
    """Consumer for the dedicated backfill queue.

    Backfills use a separate queue so a 2000-run bulk operation can't
    starve live traffic on ``agent-summarization``. Same prefetch=1 rule
    applies: pod count caps parallelism. Both consumers call the same
    handler — only the queue routing differs.
    """

    def __init__(self) -> None:
        super().__init__(
            queue_name=SUMMARIZE_BACKFILL_QUEUE,
            prefetch_count=1,
        )

    async def process_message(self, body: dict[str, Any]) -> None:
        await handle_summarize_message(body)


class TuneChatConsumer(BaseConsumer):
    """BaseConsumer wrapper that routes ``agent-tuning-chat`` to the handler."""

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(
            queue_name=TUNE_CHAT_QUEUE,
            prefetch_count=settings.max_concurrency,
        )

    async def process_message(self, body: dict[str, Any]) -> None:
        await handle_tune_chat_message(body)
