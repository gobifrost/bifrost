"""Agent completion-event outbox scanner.

Terminal AgentRuns stay discoverable as needing publication until their
built-in event is emitted. This pass claims a bounded batch with row-level
fencing, emits each through the standard ``emit_event`` pipeline (after the
terminal transaction committed), and records success or a retryable failure.
Duplicate delivery is permitted and safe: coordinator workflows key on
run/correlation IDs. Lost terminal events are not.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.core.database import get_session_factory
from src.services.agent_runtime import run_store
from src.services.events.builtins import agent_completion_payload

logger = logging.getLogger(__name__)

OUTBOX_BATCH_SIZE = 50


async def publish_pending_agent_completions(
    batch_size: int = OUTBOX_BATCH_SIZE,
) -> dict[str, Any]:
    """Emit one outbox pass; returns counts for scheduler diagnostics."""
    from src.services.events import emit_event

    results: dict[str, Any] = {
        "claimed": 0,
        "emitted": 0,
        "failed": 0,
        "errors": [],
    }
    session_factory = get_session_factory()
    async with session_factory() as db:
        pending = await run_store.claim_completion_events(
            db, batch_size=batch_size
        )
    results["claimed"] = len(pending)
    max_lag_seconds = 0.0
    from datetime import datetime, timezone

    for run in pending:
        run_id = run.id
        if run.completion_event_pending_at is not None:
            pending_at = run.completion_event_pending_at
            if pending_at.tzinfo is None:
                pending_at = pending_at.replace(tzinfo=timezone.utc)
            lag = (
                datetime.now(timezone.utc) - pending_at
            ).total_seconds()
            max_lag_seconds = max(max_lag_seconds, lag)
        try:
            topic, body = agent_completion_payload(run)
            await emit_event(
                topic,
                body,
                organization_id=run.org_id,
            )
        except Exception as exc:
            logger.warning(
                "Agent completion emit failed for %s: %s",
                run_id,
                exc,
                exc_info=True,
            )
            async with session_factory() as db:
                await run_store.resolve_completion_event(
                    db, UUID(str(run_id)), emitted=False, error=str(exc)
                )
            results["failed"] += 1
            results["errors"].append(
                {"run_id": str(run_id), "error": str(exc)}
            )
            continue
        async with session_factory() as db:
            await run_store.resolve_completion_event(
                db, UUID(str(run_id)), emitted=True
            )
        results["emitted"] += 1
    results["max_lag_seconds"] = round(max_lag_seconds, 1)
    if results["claimed"]:
        logger.info(
            "agent_completion_outbox",
            extra={
                "claimed": results["claimed"],
                "emitted": results["emitted"],
                "failed": results["failed"],
                "max_lag_seconds": results["max_lag_seconds"],
            },
        )
    return results
