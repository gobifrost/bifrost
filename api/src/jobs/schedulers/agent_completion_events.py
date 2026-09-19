"""Publish durable AgentRun completions and reconcile their parent wakes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.core.database import get_session_factory
from src.models.orm.agent_runs import AgentRun
from src.services.agent_runtime import run_store
from src.services.events.builtins import agent_completion_payload

logger = logging.getLogger(__name__)
OUTBOX_BATCH_SIZE = 50


async def publish_pending_agent_completions(
    batch_size: int = OUTBOX_BATCH_SIZE,
) -> dict[str, Any]:
    """Keep each claim locked until its emission result is durable.

    Parent wakes happen before taking the child outbox lock. They are
    idempotent and are retried if a worker died after terminalization. A
    per-row claim avoids holding a whole tree's locks during parent wake.
    """
    from src.services.agent_runtime.delegation import notify_parent_of_completion
    from src.services.events import emit_event

    results: dict[str, Any] = {
        "claimed": 0, "emitted": 0, "failed": 0, "errors": [],
    }
    session_factory = get_session_factory()
    async with session_factory() as db:
        pending_ids = list((await db.scalars(
            select(AgentRun.id)
            .where(
                AgentRun.completion_event_pending_at.is_not(None),
                AgentRun.completion_event_emitted_at.is_(None),
            )
            .order_by(
                AgentRun.completion_event_attempts,
                AgentRun.completion_event_pending_at,
                AgentRun.id,
            )
            .limit(batch_size)
        )).all())
    max_lag_seconds = 0.0
    for run_id in pending_ids:
        parent_error: str | None = None
        try:
            await notify_parent_of_completion(session_factory, run_id)
        except Exception as exc:
            # Keep the outbox pending until the durable parent result/wake
            # succeeds too; a public event must not hide a stranded parent.
            logger.warning("Agent completion parent wake failed for %s", run_id, exc_info=True)
            parent_error = f"Parent wake failed: {exc}"
        async with session_factory() as db:
            run = await db.scalar(
                select(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.completion_event_pending_at.is_not(None),
                    AgentRun.completion_event_emitted_at.is_(None),
                )
                .with_for_update(skip_locked=True, of=AgentRun)
            )
            if run is None:
                continue
            results["claimed"] += 1
            if parent_error is not None:
                await run_store.resolve_completion_event(
                    db, run_id, emitted=False, error=parent_error,
                )
                results["failed"] += 1
                results["errors"].append({"run_id": str(run_id), "error": parent_error})
                continue
            pending_at = run.completion_event_pending_at
            if pending_at is not None:
                if pending_at.tzinfo is None:
                    pending_at = pending_at.replace(tzinfo=timezone.utc)
                max_lag_seconds = max(
                    max_lag_seconds,
                    (datetime.now(timezone.utc) - pending_at).total_seconds(),
                )
            try:
                topic, body = agent_completion_payload(run)
                await emit_event(topic, body, organization_id=run.org_id)
            except Exception as exc:
                logger.warning("Agent completion emit failed for %s", run_id, exc_info=True)
                await run_store.resolve_completion_event(
                    db, run_id, emitted=False, error=str(exc),
                )
                results["failed"] += 1
                results["errors"].append({"run_id": str(run_id), "error": str(exc)})
            else:
                await run_store.resolve_completion_event(db, run_id, emitted=True)
                results["emitted"] += 1
    results["max_lag_seconds"] = round(max_lag_seconds, 1)
    if results["claimed"]:
        logger.info(
            "agent_completion_outbox",
            extra={key: results[key] for key in ("claimed", "emitted", "failed", "max_lag_seconds")},
        )
    return results
