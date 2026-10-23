"""Post-run summarization message handling.

Two dispatch handlers, intentionally separate from agent runs:

- ``agent.summarize`` — short, deterministic LLM call to extract
  asked/did/confidence/metadata from a completed run. Failures are
  swallowed (run.summary_status = "failed", error stored) rather than
  retried on bad LLM output; the UI exposes a regenerate button for
  recovery.
- ``agent.summarize_backfill`` — the same work for bulk backfills.

Summaries are a work-dispatch family (``summary_dispatch``). Executors claim
the dispatches from PostgreSQL and call :func:`handle_summarize_message`,
which claims the summary itself before it runs.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.database import get_session_factory
from src.models.orm.agent_runs import AgentRun

logger = logging.getLogger(__name__)

__all__ = ["handle_summarize_message"]


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


async def handle_summarize_message(
    message: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Claim ``{"run_id": "...", "dispatch_id": "..."}`` and summarize.

    The summary is claimed first; a run that moved on finds nothing to claim
    and is dropped.

    On failure the exception is *intentionally* swallowed: a permanent
    failure (bad LLM output, missing config) should not be retried. Instead
    we mark ``summary_status='failed'`` and store the error so the UI can
    offer a regenerate button.

    If the run belongs to a backfill (``summary_backfill_job_id``) the
    outcome is counted against it.
    """
    from src.services.execution.backfill_tracker import record_backfill_outcome
    from src.services.execution.summary_dispatch import claim_summary

    factory = session_factory or get_session_factory()
    run_id = UUID(message["run_id"])
    job_id: UUID | None = None
    async with factory() as db:
        claimed = await claim_summary(db, run_id, UUID(message["dispatch_id"]))
        await db.commit()
    if not claimed:
        return
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
                    job_id = run.summary_backfill_job_id
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
                if run is not None:
                    if run.summary_status == "failed":
                        succeeded = False
                    job_id = run.summary_backfill_job_id
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
