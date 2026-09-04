"""
Execution Cleanup Scheduler

Cleans up stuck workflow executions and stale autonomous agent runs that
remain in in-progress states for too long.

Runs every 5 minutes to find and timeout stuck executions.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select

from src.core.database import get_session_factory
from src.core.pubsub import (
    publish_agent_run_update,
    publish_chat_run_event,
    publish_execution_update,
    publish_history_update,
)
from src.models.contracts.agents import ChatStreamChunk
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models import Execution as ExecutionModel, ExecutionLog
from src.models.orm.workflows import Workflow

logger = logging.getLogger(__name__)

# Timeout thresholds
PENDING_TIMEOUT_MINUTES = 10  # If PENDING for 10+ minutes, it's stuck in queue
RUNNING_TIMEOUT_MINUTES = 30  # If RUNNING for 30+ minutes, worker likely crashed
CANCELLING_TIMEOUT_MINUTES = 3  # If CANCELLING for 3+ minutes, worker failed to cancel
DEFAULT_AGENT_RUN_TIMEOUT_SECONDS = 30 * 60
AGENT_RUN_TIMEOUT_GRACE_SECONDS = 5 * 60


async def cleanup_stuck_executions() -> dict[str, Any]:
    """
    Clean up stuck executions.

    Finds executions that have been stuck in PENDING, RUNNING, or CANCELLING
    status for longer than the timeout threshold and marks them as TIMEOUT/CANCELLED.

    Returns:
        Summary of cleanup results
    """
    logger.info("Starting execution cleanup")

    from src.models.enums import ExecutionStatus

    results = {
        "pending_timeouts": 0,
        "running_timeouts": 0,
        "cancelling_timeouts": 0,
        "total_cleaned": 0,
        "errors": [],
        "agent_run_queued_timeouts": 0,
        "agent_run_running_timeouts": 0,
        "agent_run_total_cleaned": 0,
        "agent_run_errors": [],
    }

    now = datetime.now(timezone.utc)

    try:
        # Collect data for WebSocket broadcasts (published after session closes)
        pubsub_updates: list[dict] = []

        session_factory = get_session_factory()
        async with session_factory() as db:
            # Find stuck PENDING executions
            pending_cutoff = now - timedelta(minutes=PENDING_TIMEOUT_MINUTES)
            pending_result = await db.execute(
                select(ExecutionModel).where(
                    and_(
                        ExecutionModel.status == ExecutionStatus.PENDING.value,
                        ExecutionModel.started_at < pending_cutoff,
                    )
                )
            )
            pending_stuck = list(pending_result.scalars().all())

            # Find stuck RUNNING executions — respect per-workflow timeout
            # Join with Workflow to get configured timeout_seconds.
            # Use workflow timeout + 5 min grace (process pool should kill first).
            # Fallback to RUNNING_TIMEOUT_MINUTES if no workflow found.
            running_result = await db.execute(
                select(ExecutionModel, Workflow.timeout_seconds).where(
                    and_(
                        ExecutionModel.status == ExecutionStatus.RUNNING.value,
                    )
                ).outerjoin(Workflow, ExecutionModel.workflow_id == Workflow.id)
            )
            running_stuck = []
            for execution, wf_timeout in running_result.all():
                # timeout_seconds == 0 means no timeout — skip entirely
                if wf_timeout is not None and wf_timeout == 0:
                    continue
                # Use per-workflow timeout + 5 min grace, or fallback
                effective_timeout_s = (wf_timeout + 300) if wf_timeout else (RUNNING_TIMEOUT_MINUTES * 60)
                elapsed = (now - execution.started_at).total_seconds()
                if elapsed > effective_timeout_s:
                    running_stuck.append(execution)

            # Find stuck CANCELLING executions
            cancelling_cutoff = now - timedelta(minutes=CANCELLING_TIMEOUT_MINUTES)
            cancelling_result = await db.execute(
                select(ExecutionModel).where(
                    and_(
                        ExecutionModel.status == ExecutionStatus.CANCELLING.value,
                        ExecutionModel.started_at < cancelling_cutoff,
                    )
                )
            )
            cancelling_stuck = list(cancelling_result.scalars().all())

            all_stuck = pending_stuck + running_stuck + cancelling_stuck
            logger.info(f"Found {len(all_stuck)} stuck executions to clean up")

            for execution in all_stuck:
                try:
                    # Determine timeout reason and final status
                    if execution.status == ExecutionStatus.PENDING.value:
                        timeout_reason = (
                            f"Stuck in PENDING status for {PENDING_TIMEOUT_MINUTES}+ minutes. "
                            "Likely queue processing issue or worker not running."
                        )
                        final_status = ExecutionStatus.TIMEOUT
                        results["pending_timeouts"] += 1

                    elif execution.status == ExecutionStatus.RUNNING.value:
                        elapsed_min = int((now - execution.started_at).total_seconds() / 60) if execution.started_at else RUNNING_TIMEOUT_MINUTES
                        timeout_reason = (
                            f"Stuck in RUNNING status for {elapsed_min}+ minutes. "
                            "Likely worker crash or workflow hang."
                        )
                        final_status = ExecutionStatus.TIMEOUT
                        results["running_timeouts"] += 1

                    elif execution.status == ExecutionStatus.CANCELLING.value:
                        timeout_reason = (
                            f"Stuck in CANCELLING status for {CANCELLING_TIMEOUT_MINUTES}+ minutes. "
                            "Worker likely crashed during cancellation."
                        )
                        final_status = ExecutionStatus.CANCELLED
                        results["cancelling_timeouts"] += 1

                    else:
                        continue

                    # Log orphan execution being swept (before status update, to capture original status)
                    stuck_for_seconds = int((now - execution.started_at).total_seconds()) if execution.started_at else 0
                    logger.warning(
                        "orphan_execution_swept",
                        extra={
                            "execution_id": str(execution.id),
                            "stuck_status": execution.status,
                            "stuck_for_seconds": stuck_for_seconds,
                        },
                    )

                    logger.warning(
                        f"Timing out stuck execution: {execution.id}",
                        extra={
                            "execution_id": str(execution.id),
                            "workflow_name": execution.workflow_name,
                            "status": execution.status,
                            "timeout_reason": timeout_reason,
                        },
                    )

                    # Update execution
                    execution.status = final_status.value  # type: ignore[assignment]
                    execution.error_message = timeout_reason
                    execution.completed_at = now

                    # Add timeout log entry
                    log_entry = ExecutionLog(
                        execution_id=execution.id,
                        level="error",
                        message=timeout_reason,
                        log_metadata={
                            "timeout_type": "automatic_cleanup",
                            "original_status": execution.status,
                        },
                        timestamp=now,
                    )
                    db.add(log_entry)

                    results["total_cleaned"] += 1

                    # Collect data for pubsub (published after session closes)
                    pubsub_updates.append({
                        "execution_id": str(execution.id),
                        "final_status": final_status.value,
                        "timeout_reason": timeout_reason,
                        "executed_by": execution.executed_by,
                        "executed_by_name": execution.executed_by_name,
                        "workflow_name": execution.workflow_name,
                        "org_id": execution.organization_id,
                        "started_at": execution.started_at,
                    })

                except Exception as e:
                    logger.error(
                        f"Error processing execution cleanup for {execution.id}",
                        extra={"error": str(e)},
                        exc_info=True,
                    )
                    results["errors"].append({
                        "execution_id": str(execution.id),
                        "error": str(e),
                    })

            # Commit all changes
            await db.commit()

        # Publish WebSocket updates AFTER session is closed (no DB connection held)
        for update in pubsub_updates:
            try:
                await publish_execution_update(
                    update["execution_id"],
                    update["final_status"],
                    {"error": update["timeout_reason"]},
                )
                await publish_history_update(
                    execution_id=update["execution_id"],
                    status=update["final_status"],
                    executed_by=update["executed_by"],
                    executed_by_name=update["executed_by_name"],
                    workflow_name=update["workflow_name"],
                    org_id=update["org_id"],
                    started_at=update["started_at"],
                    completed_at=now,
                )
            except Exception as e:
                logger.warning(f"Failed to publish update for {update['execution_id']}: {e}")

        logger.info(
            "Execution cleanup completed",
            extra={
                "pending_timeouts": results["pending_timeouts"],
                "running_timeouts": results["running_timeouts"],
                "cancelling_timeouts": results["cancelling_timeouts"],
                "total_cleaned": results["total_cleaned"],
            },
        )

    except Exception as e:
        logger.error("Error in execution cleanup", extra={"error": str(e)}, exc_info=True)
        results["errors"].append({"error": str(e)})
    finally:
        try:
            agent_run_results = await _cleanup_stale_agent_runs(now)
            results.update(agent_run_results)
        except Exception as e:
            logger.error("Error in agent run cleanup", extra={"error": str(e)}, exc_info=True)
            results["agent_run_errors"].append({"error": str(e)})
        else:
            logger.info(
                "Agent run cleanup completed",
                extra={
                    "agent_run_queued_timeouts": results["agent_run_queued_timeouts"],
                    "agent_run_running_timeouts": results["agent_run_running_timeouts"],
                    "agent_run_total_cleaned": results["agent_run_total_cleaned"],
                },
            )

    return results


def _agent_run_timeout_seconds(agent: Agent | None) -> int:
    """Return the configured timeout for an agent, falling back to the shared default."""
    configured = getattr(agent, "max_run_timeout", None)
    if configured is not None and configured > 0:
        return configured
    return DEFAULT_AGENT_RUN_TIMEOUT_SECONDS


async def _cleanup_stale_agent_runs(now: datetime) -> dict[str, Any]:
    """Terminalize stale queued/running AgentRun rows without replaying them."""
    session_factory = get_session_factory()
    updates: list[dict[str, Any]] = []
    results: dict[str, Any] = {
        "agent_run_queued_timeouts": 0,
        "agent_run_running_timeouts": 0,
        "agent_run_total_cleaned": 0,
        "agent_run_errors": [],
    }

    try:
        async with session_factory() as db:
            query = (
                select(AgentRun, Agent)
                .outerjoin(Agent, AgentRun.agent_id == Agent.id)
                .where(AgentRun.status.in_(("queued", "running")))
                .order_by(AgentRun.created_at.asc())
            )
            runs = (await db.execute(query)).all()

            for candidate, agent in runs:
                timeout_seconds = _agent_run_timeout_seconds(agent)
                timeout_with_grace = timeout_seconds + AGENT_RUN_TIMEOUT_GRACE_SECONDS
                reference_time = (
                    candidate.created_at
                    if candidate.status == "queued"
                    else (candidate.started_at or candidate.created_at)
                )
                if reference_time is None:
                    continue

                elapsed = (now - reference_time).total_seconds()
                if elapsed <= timeout_with_grace:
                    continue

                # Lock only the row already identified as stale. The status
                # predicate is a compare-and-set guard against a worker that
                # completed between the candidate read and this lock.
                run = (
                    await db.execute(
                        select(AgentRun)
                        .where(
                            AgentRun.id == candidate.id,
                            AgentRun.status == candidate.status,
                        )
                        .with_for_update(skip_locked=True, of=AgentRun)
                    )
                ).scalar_one_or_none()
                if run is None:
                    continue

                reference_time = (
                    run.created_at
                    if run.status == "queued"
                    else (run.started_at or run.created_at)
                )
                if reference_time is None:
                    continue
                elapsed = (now - reference_time).total_seconds()
                if elapsed <= timeout_with_grace:
                    continue

                agent_name = agent.name if agent is not None else "Chat"
                if run.status == "queued":
                    final_status = "failed"
                    timeout_reason = (
                        f"Agent run timed out waiting in queue after "
                        f"{timeout_with_grace} seconds."
                    )
                    results["agent_run_queued_timeouts"] += 1
                else:
                    final_status = "timeout"
                    timeout_reason = (
                        f"Agent run timed out after {timeout_with_grace} seconds."
                    )
                    results["agent_run_running_timeouts"] += 1

                logger.warning(
                    "agent_run_swept",
                    extra={
                        "agent_run_id": str(run.id),
                        "agent_id": str(run.agent_id),
                        "agent_name": agent_name,
                        "stuck_status": run.status,
                        "stuck_for_seconds": int(elapsed),
                        "timeout_seconds": timeout_seconds,
                        "timeout_with_grace": timeout_with_grace,
                    },
                )

                run.status = final_status
                run.error = timeout_reason
                run.completed_at = now
                updates.append(
                    {
                        "run": run,
                        "agent_name": agent_name,
                        "chat_event": (
                            {
                                "conversation_id": run.conversation_id,
                                "run_id": str(run.id),
                                "status": final_status,
                                "error": timeout_reason,
                            }
                            if run.trigger_type == "chat"
                            and run.conversation_id is not None
                            else None
                        ),
                    }
                )
                results["agent_run_total_cleaned"] += 1

            await db.commit()

        for update in updates:
            try:
                await publish_agent_run_update(update["run"], update["agent_name"])
                chat_event = update["chat_event"]
                if chat_event is not None:
                    await publish_chat_run_event(
                        conversation_id=chat_event["conversation_id"],
                        run_id=chat_event["run_id"],
                        kind="error",
                        status=chat_event["status"],
                        payload=ChatStreamChunk(
                            type="error",
                            error=chat_event["error"],
                            run_status=chat_event["status"],
                        ),
                    )
            except Exception:
                logger.warning(
                    "Failed to publish agent run update",
                    extra={"agent_run_id": str(update["run"].id)},
                    exc_info=True,
                )

        return results
    except Exception as e:
        logger.error("Error in agent run cleanup", extra={"error": str(e)}, exc_info=True)
        results["agent_run_errors"].append({"error": str(e)})
        return results
