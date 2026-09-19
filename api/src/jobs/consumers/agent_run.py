"""RabbitMQ consumer for autonomous agent runs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.core.cache.keys import agent_run_steps_stream_key
from src.core.cache.redis_client import get_redis
from src.core.database import get_session_factory
from src.core.principal import UserPrincipal
from src.core.pubsub import publish_agent_run_update, publish_chat_run_event
from src.jobs.rabbitmq import BaseConsumer
from src.models.contracts.agents import ChatStreamChunk
from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation
from src.models.orm.agent_runs import AgentRun
from src.services.agent_runtime import run_store
from src.services.agent_runtime import types as runtime_types
from src.services.agent_runtime.resume import active_seconds_used, prepare_resume

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"
DEFAULT_RUN_TIMEOUT = 1800  # 30 minutes
CANCEL_CHECK_INTERVAL = 2  # seconds between cancel flag checks
AGENT_RUN_LEASE_TTL_SECONDS = 120  # crash-detection lease, not a user limit
AGENT_RUN_LEASE_HEARTBEAT_SECONDS = 30  # renewal cadence while executing

# Executor result statuses mapped onto the durable lifecycle.
_TERMINAL_STATUS_MAP = {
    "completed": runtime_types.COMPLETED,
    "failed": runtime_types.FAILED,
    "cancelled": runtime_types.CANCELLED,
    "timeout": runtime_types.TIMEOUT,
    "budget_exceeded": runtime_types.BUDGET_EXCEEDED,
    "contract_failed": runtime_types.CONTRACT_FAILED,
}


def _worker_owner() -> str:
    """Stable-enough owner label for lease debugging."""
    try:
        host = socket.gethostname()
    except Exception:
        host = "worker"
    return f"{host}:{os.getpid()}"


async def _publish_sync_result(run_id: str, result: dict) -> None:
    """Release a synchronous caller waiting on this run."""
    result_key = f"{REDIS_PREFIX}:{run_id}:result"
    async with get_redis() as redis:
        await redis.lpush(  # pyright: ignore[reportGeneralTypeIssues]
            result_key,
            json.dumps(result),
        )
        await redis.expire(result_key, 300)


def _chat_chunk_status(chunk_type: str) -> str:
    if chunk_type in {
        "message_start",
        "delta",
        "assistant_message_end",
        "tool_call",
        "tool_progress",
        "tool_result",
        "artifact_started",
        "artifact_ready",
        "artifact_failed",
        "agent_switch",
        "context_warning",
    }:
        return "running"
    if chunk_type in {"done", "title_update"}:
        return "completed"
    if chunk_type == "cancelled":
        return "cancelled"
    return "failed"


def _caller_to_principal(caller: dict[str, Any]) -> UserPrincipal:
    user_id_raw = caller.get("user_id")
    email = caller.get("email")
    if not user_id_raw or not email:
        raise ValueError("Chat caller context is incomplete")

    organization_id = caller.get("organization_id")
    return UserPrincipal(
        user_id=UUID(str(user_id_raw)),
        email=str(email),
        organization_id=UUID(str(organization_id)) if organization_id else None,
        name=str(caller.get("name") or ""),
        is_active=True,
        is_superuser=bool(caller.get("is_platform_admin", caller.get("is_superuser", False))),
        is_verified=True,
        is_external=bool(caller.get("is_external", False)),
        is_provider_org=bool(caller.get("is_provider_org", False)),
        roles=list(caller.get("roles") or []),
    )


async def _generate_conversation_title(
    db,
    conversation: Conversation,
    user_message: str,
) -> str | None:
    from src.services.llm import LLMMessage, get_llm_client

    try:
        llm_client = await get_llm_client(db)
        response = await llm_client.complete(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "Generate a very short, concise title (3-6 words max) for a "
                        "conversation that starts with the following message. "
                        "Respond with ONLY the title, no quotes or punctuation at the end."
                    ),
                ),
                LLMMessage(role="user", content=user_message),
            ],
            max_tokens=1024,
        )

        if response.content:
            title = response.content.strip().strip('"\'')
            if len(title) > 100:
                title = title[:97] + "..."
            return title
    except Exception as exc:
        logger.warning(
            "Failed to generate title for conversation %s: %s",
            conversation.id,
            exc,
        )

    return None


class AgentRunConsumer(BaseConsumer):
    def __init__(self):
        settings = get_settings()
        super().__init__(
            queue_name=QUEUE_NAME,
            prefetch_count=settings.max_concurrency,
        )
        self._session_factory = get_session_factory()

    async def process_message(self, body: dict) -> None:
        run_id = body["run_id"]

        logger.info(f"Processing agent run {run_id}")

        # PostgreSQL is authoritative: the admitted row carries the immutable
        # execution snapshot, invocation input, output schema, and caller
        # context. The queue message is only a nudge. Legacy publishers may
        # still attach agent_id/trigger_type/sync; the row wins when present.
        start_time = time.time()
        agent_run: AgentRun | None = None
        agent: Agent | None = None
        executor = None
        context: dict[str, Any] | None = None
        execution_snapshot: dict[str, Any] | None = None
        trigger_type: str | None = body.get("trigger_type")
        sync = body.get("sync", False)

        try:
            # Atomically claim the durable queued row and load the agent.
            async with self._session_factory() as db:
                agent_run = await db.get(
                    AgentRun,
                    UUID(run_id),
                    # AgentRun.agent is joined-eager, so an unrestricted
                    # FOR UPDATE would also target the nullable side of that
                    # outer join, which PostgreSQL rejects. Lock only the run
                    # row that participates in the claim/cancel race.
                    with_for_update={"of": AgentRun},
                )
                if agent_run is None:
                    logger.error(f"Agent run {run_id}: queued record not found")
                    return
                if agent_run.status != "queued":
                    logger.info(
                        "Agent run %s: skipping message for status %s",
                        run_id,
                        agent_run.status,
                    )
                    return

                # Best-effort pre-cancel via the dedicated cancel flag. The
                # legacy Redis execution context no longer exists.
                cancelled = False
                try:
                    async with get_redis() as redis:
                        cancelled = bool(
                            await redis.get(f"{REDIS_PREFIX}:{run_id}:cancel")
                        )
                except Exception:
                    logger.debug(
                        "Agent run %s: cancel-flag check unavailable", run_id
                    )
                if cancelled:
                    logger.info(
                        f"Agent run {run_id}: pre-cancelled, skipping execution"
                    )
                    agent_run.status = "cancelled"
                    agent_run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _publish_sync_result(
                        run_id,
                        {"output": None, "status": "cancelled", "error": None},
                    )
                    return

                trigger_type = agent_run.trigger_type or trigger_type
                execution_snapshot = agent_run.execution_snapshot
                context = {
                    "run_id": run_id,
                    "agent_id": (
                        str(agent_run.agent_id) if agent_run.agent_id else None
                    ),
                    "trigger_type": trigger_type,
                    "trigger_source": agent_run.trigger_source,
                    "input": agent_run.input,
                    "output_schema": agent_run.output_schema,
                    "org_id": (
                        str(agent_run.org_id) if agent_run.org_id else None
                    ),
                    "caller": {
                        "user_id": agent_run.caller_user_id,
                        "email": agent_run.caller_email,
                        "name": agent_run.caller_name,
                        "organization_id": (
                            str(agent_run.org_id) if agent_run.org_id else None
                        ),
                        "is_superuser": False,
                        "is_external": False,
                        "is_provider_org": False,
                        "roles": [],
                    },
                    "event_delivery_id": (
                        str(agent_run.event_delivery_id)
                        if agent_run.event_delivery_id
                        else None
                    ),
                    "conversation_id": (
                        str(agent_run.conversation_id)
                        if agent_run.conversation_id
                        else None
                    ),
                    "caller_context": agent_run.caller_context,
                    "correlation": agent_run.correlation,
                    "sync": sync,
                    "cancelled": False,
                }

                is_chat_trigger = trigger_type == "chat"
                if is_chat_trigger:
                    agent_run.status = "running"
                    agent_run.started_at = datetime.now(timezone.utc)
                    await db.commit()
                    if agent_run.conversation_id is None:
                        raise ValueError(f"Chat run {run_id} is missing conversation_id")
                    await publish_chat_run_event(
                        conversation_id=agent_run.conversation_id,
                        run_id=run_id,
                        kind="run_status",
                        status="running",
                        payload=ChatStreamChunk(
                            type="run_status",
                            conversation_id=str(agent_run.conversation_id),
                            run_status="running",
                        ),
                    )
                    await publish_agent_run_update(agent_run, "Unknown")
                    await self._process_chat_run(
                        run_id=run_id,
                        context=context,
                        agent_run=agent_run,
                        agent=None,
                        sync=sync,
                        start_time=start_time,
                    )
                    return

                # Non-chat runs execute from the immutable snapshot. A legacy
                # row without one may still carry an in-flight Redis context
                # from before the upgrade; use it once rather than guessing.
                # Otherwise fail closed with a recovery reason.
                agent_id = context["agent_id"] or body.get("agent_id")
                if agent_id is None:
                    logger.error(f"Agent run {run_id}: agent id missing for non-chat run")
                    agent_run.status = "failed"
                    agent_run.error = "Agent id missing"
                    agent_run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _publish_sync_result(
                        run_id,
                        {
                            "output": None,
                            "status": "failed",
                            "error": "Agent id missing",
                        },
                    )
                    return
                if execution_snapshot is None:
                    legacy_context = await self._load_legacy_context(run_id)
                    if legacy_context is not None:
                        logger.info(
                            "Agent run %s: executing from legacy Redis context",
                            run_id,
                        )
                        context = legacy_context
                        context["run_id"] = run_id
                        trigger_type = (
                            legacy_context.get("trigger_type") or trigger_type
                        )
                    else:
                        from src.services.agent_runtime.execution_snapshot import (
                            SnapshotError,
                        )

                        logger.error(
                            f"Agent run {run_id}: no execution snapshot"
                        )
                        agent_run.status = "failed"
                        agent_run.error = str(
                            SnapshotError(
                                "AgentRun predates durable execution snapshots; "
                                "re-enqueue the work."
                            )
                        )
                        agent_run.completed_at = datetime.now(timezone.utc)
                        await db.commit()
                        await _publish_sync_result(
                            run_id,
                            {
                                "output": None,
                                "status": "failed",
                                "error": agent_run.error,
                            },
                        )
                        return

                assert context is not None
                result = await db.execute(
                    select(Agent)
                    .options(
                        selectinload(Agent.tools),
                        selectinload(Agent.delegated_agents),
                        selectinload(Agent.roles),
                    )
                    .where(Agent.id == UUID(agent_id))
                )
                agent = result.scalar_one_or_none()
                if agent is None:
                    logger.error(f"Agent run {run_id}: agent {agent_id} not found")
                    agent_run.status = "failed"
                    agent_run.error = "Agent no longer exists"
                    agent_run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _publish_sync_result(
                        run_id,
                        {
                            "output": None,
                            "status": "failed",
                            "error": "Agent no longer exists",
                        },
                    )
                    return

                # Claim the same AgentRun under a fenced worker lease. Only
                # one worker owns a live run at a time; an expired lease
                # makes the same run claimable again — never a new run.
                try:
                    claimed = await run_store.claim_run(
                        db,
                        UUID(run_id),
                        _worker_owner(),
                        lease_ttl_seconds=AGENT_RUN_LEASE_TTL_SECONDS,
                    )
                except runtime_types.RunNotClaimableError:
                    logger.info(
                        "Agent run %s: lease held by another worker, skipping",
                        run_id,
                    )
                    return
                lease_token = claimed.lease_token
                assert lease_token is not None
                attempt = claimed.attempt or 0

                snapshot_limits = (execution_snapshot or {}).get("limits") or {}
                agent_run.budget_max_iterations = snapshot_limits.get(
                    "max_iterations", agent.max_iterations
                )
                agent_run.budget_max_tokens = snapshot_limits.get(
                    "max_token_budget", agent.max_token_budget
                )
                await db.commit()
            await publish_agent_run_update(agent_run, agent.name if agent else "Unknown")

            # Chat was already handled above from the durable row; reaching
            # here with a chat trigger means legacy context took over.
            if trigger_type == "chat" or (context or {}).get("trigger_type") == "chat":
                await self._process_chat_run(
                    run_id=run_id,
                    context=context,
                    agent_run=agent_run,
                    agent=agent,
                    sync=sync,
                    start_time=start_time,
                )
                return

            # Reconcile in-flight work and rebuild resume history from the
            # latest checkpoint. An uncertain side effect without proof
            # moves the run to recovery_required instead of replaying blindly.
            resume_plan, _reclaim_report, unrecoverable = await prepare_resume(
                self._session_factory, UUID(run_id), lease_token
            )
            if unrecoverable is not None:
                async with self._session_factory() as db:
                    recovered_run = await run_store.mark_recovery_required(
                        db,
                        UUID(run_id),
                        lease_token,
                        reason=unrecoverable,
                        evidence={"attempt": attempt},
                    )
                await publish_agent_run_update(recovered_run, agent.name)
                await _publish_sync_result(
                    run_id,
                    {
                        "output": None,
                        "status": runtime_types.RECOVERY_REQUIRED,
                        "error": unrecoverable,
                    },
                )
                return

            # Fresh Pydantic invocation ID for this attempt, recorded in the
            # journal. The Bifrost AgentRun ID stays the same across attempts.
            pydantic_invocation_id = uuid4().hex
            async with self._session_factory() as db:
                await run_store.append_journal(
                    db,
                    UUID(run_id),
                    lease_token,
                    runtime_types.JOURNAL_RESUME,
                    {
                        "attempt": attempt,
                        "pydantic_invocation_id": pydantic_invocation_id,
                    },
                    provider_invocation_id=pydantic_invocation_id,
                )

            # Wall-clock accounting excludes waiting/sleeping: only active
            # execution across attempts counts toward max_run_timeout.
            # max_run_timeout=0 means disabled.
            snapshot_timeout = snapshot_limits.get("max_run_timeout")
            configured_timeout = (
                snapshot_timeout
                if snapshot_timeout is not None
                else agent.max_run_timeout
            )
            run_timeout: float | None
            if configured_timeout == 0:
                run_timeout = None
            else:
                async with self._session_factory() as db:
                    spent = await active_seconds_used(
                        db, UUID(run_id), attempt
                    )
                configured_timeout = configured_timeout or DEFAULT_RUN_TIMEOUT
                remaining = configured_timeout - spent
                if remaining <= 0:
                    async with self._session_factory() as db:
                        timed_out = await run_store.finish_run(
                            db,
                            UUID(run_id),
                            lease_token,
                            runtime_types.TIMEOUT,
                            error=(
                                "Agent run exceeded max_run_timeout "
                                f"({configured_timeout}s active)"
                            ),
                            duration_ms=int((time.time() - start_time) * 1000),
                        )
                    await publish_agent_run_update(timed_out, agent.name)
                    await _publish_sync_result(
                        run_id,
                        {
                            "output": timed_out.output,
                            "status": timed_out.status,
                            "error": timed_out.error,
                        },
                    )
                    return
                run_timeout = remaining

            async with get_redis() as redis_for_executor:
                # Agent/MCP provider clients are heavyweight and unused until an
                # agent message is actually processed. Keep them out of the
                # worker's import-time closure and pay the cost at this boundary.
                from src.services.execution.autonomous_agent_executor import (
                    AutonomousAgentExecutor,
                )

                executor = AutonomousAgentExecutor(
                    self._session_factory,
                    redis_client=redis_for_executor,
                )

                # Create executor task so cancel watcher can cancel it
                executor_task = asyncio.ensure_future(executor.run(
                    agent=agent,
                    input_data=context.get("input"),
                    output_schema=context.get("output_schema"),
                    run_id=run_id,
                    execution_snapshot=execution_snapshot,
                    lease_token=lease_token,
                    resume_history=(
                        resume_plan.history if resume_plan is not None else None
                    ) or None,
                    caller_context=context.get("caller_context"),
                    correlation=context.get("correlation"),
                    deferred_results=(
                        resume_plan.deferred_results if resume_plan is not None else None
                    ) or None,
                    deferred_pending=(
                        resume_plan.deferred_pending if resume_plan is not None else None
                    ) or None,
                    _caller=context.get("caller"),
                ))

                # Cancel watcher: polls Redis flag, force-cancels task if stuck
                cancel_watcher = asyncio.ensure_future(
                    AgentRunConsumer._cancel_watcher(run_id, executor_task, redis_for_executor)
                )

                # Lease heartbeat: meaningful progress renews crash detection.
                # Losing the lease means another worker claimed the run, so
                # this attempt must stop rather than duplicate its work.
                heartbeat_stop = asyncio.Event()
                lease_lost = False

                def _on_lease_lost() -> None:
                    nonlocal lease_lost
                    lease_lost = True
                    executor_task.cancel()

                heartbeat_task = asyncio.ensure_future(
                    AgentRunConsumer._lease_heartbeat(
                        self._session_factory,
                        run_id,
                        lease_token,
                        AGENT_RUN_LEASE_HEARTBEAT_SECONDS,
                        heartbeat_stop,
                        _on_lease_lost,
                    )
                )

                try:
                    run_result = await asyncio.wait_for(
                        asyncio.shield(executor_task),
                        timeout=run_timeout,
                    )
                except asyncio.TimeoutError:
                    executor_task.cancel()
                    try:
                        await executor_task
                    except asyncio.CancelledError:
                        # Expected — we just cancelled the task on timeout
                        pass
                    run_result = {
                        "output": None,
                        "iterations_used": 0,
                        "tokens_used": 0,
                        "status": "timeout",
                        "llm_model": None,
                        "error": f"Agent run timed out after {run_timeout}s",
                    }
                except asyncio.CancelledError:
                    if lease_lost:
                        # Another worker owns the run now; do not terminalize.
                        # The finish path below detects the lost lease and
                        # republishes current state without overwriting.
                        run_result = {
                            "output": None,
                            "iterations_used": 0,
                            "tokens_used": 0,
                            "status": "running",
                            "llm_model": None,
                            "error": "Worker lease lost; run remains resumable",
                        }
                    else:
                        run_result = {
                            "output": None,
                            "iterations_used": 0,
                            "tokens_used": 0,
                            "status": "cancelled",
                            "llm_model": None,
                        }
                finally:
                    heartbeat_stop.set()
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        # Expected — we just cancelled the heartbeat
                        pass
                    cancel_watcher.cancel()
                    try:
                        await cancel_watcher
                    except asyncio.CancelledError:
                        # Expected — we just cancelled the watcher
                        pass

            # Terminalize through the fenced store: the completion event is
            # marked pending in the same transaction. A lost lease or an
            # already-terminal row means another worker won the race, so
            # republish current state instead of overwriting it.
            duration_ms = int((time.time() - start_time) * 1000)
            consumer_applied_result = False
            raw_status = run_result.get("status", "completed")
            terminal_status = _TERMINAL_STATUS_MAP.get(raw_status)
			# A suspended parent parked itself in waiting_child and released
            # the worker: the sync waiter keeps waiting for the final
            # terminal result, and event delivery completes with it.
			suspended = raw_status == "suspended"
			async with self._session_factory() as db:
				# Flush metering and steps even when another worker won the
                # terminal-state race; completed provider work still incurred
                # cost and remains useful diagnostic evidence.
                if executor:
                    await executor.flush_to_db(db)
                if terminal_status is not None:
                    try:
                        finished = await run_store.finish_run(
                            db,
                            UUID(run_id),
                            lease_token,
                            terminal_status,
                            output=(
                                run_result.get("output")
                                if isinstance(run_result.get("output"), dict)
                                else {"text": run_result.get("output")}
                            ),
                            error=run_result.get("error"),
                            iterations_used=run_result.get("iterations_used", 0),
                            tokens_used=run_result.get("tokens_used", 0),
                            duration_ms=duration_ms,
                            llm_model=run_result.get("llm_model"),
                            contract_valid=run_result.get("contract_valid"),
                            contract_errors=run_result.get("contract_errors"),
                        )
                    except (
                        runtime_types.LeaseMismatchError,
                        runtime_types.InvalidTransitionError,
                    ):
                        await db.rollback()
                        logger.info(
                            "Agent run %s: finish skipped (lease lost or "
                            "already terminal)",
                            run_id,
                        )
                    else:
                        consumer_applied_result = True
                        agent_run = finished
                else:
                    # Lease lost mid-attempt or a legacy non-terminal outcome:
                    # persist flushed evidence only.
                    await db.commit()

                reloaded = await db.get(AgentRun, UUID(run_id))
                if reloaded is not None:
                    agent_run = reloaded

            # Clean up Redis Stream now that steps are committed to DB
            try:
                async with get_redis() as r:
                    await r.delete(agent_run_steps_stream_key(run_id))
            except Exception as e:
                # Stream cleanup is best-effort — Redis stream has a TTL anyway
                logger.debug(f"failed to delete agent_run steps stream for {run_id}: {e}")

            await publish_agent_run_update(agent_run, agent.name)

            # Enqueue post-run summarization for completed runs only.
            # Failures/timeouts/cancellations don't get summarized; the UI
            # exposes a regenerate button to retry from any state.
            # Errors here MUST NOT crash the run — summary_status stays
            # 'pending' and the UI offers a regenerate path.
            if consumer_applied_result and agent_run.status == "completed":
                try:
                    from src.services.execution.run_summarizer import enqueue_summarize
                    await enqueue_summarize(UUID(run_id))
                except Exception:
                    logger.exception(
                        f"Failed to enqueue summarizer for run {run_id}"
                    )

            # Update event delivery status if triggered by event. Suspended
            # parents deliver with their final terminal result instead.
            if (context or {}).get("event_delivery_id") and not suspended:
                async with self._session_factory() as db:
                    await self._update_event_delivery(
                        db,
                        event_delivery_id=context["event_delivery_id"],
                        agent_run_id=run_id,
                        run_status=agent_run.status,
                        error_message=agent_run.error,
                    )

            # Always publish the terminal result: sync SDK callers BLPOP on
            # this key and async callers simply never listen (300s TTL).
            # Suspended parents publish nothing; the waiter stays blocked
            # until the delegation tree completes.
            if trigger_type != "chat" and not suspended:
                await _publish_sync_result(
                    run_id,
                    {
                        "output": agent_run.output,
                        "status": agent_run.status,
                        "error": agent_run.error,
                        "iterations_used": agent_run.iterations_used,
                        "tokens_used": agent_run.tokens_used,
                        "llm_model": agent_run.llm_model,
                    },
                )

            # A terminal child wakes its waiting parent exactly once, single
            # or fan-out. The notify is idempotent: late duplicates find a
            # non-waiting parent or a completed join.
            if (
                not suspended
                and consumer_applied_result
                and agent_run.parent_run_id is not None
            ):
                try:
                    from src.services.agent_runtime.delegation import (
                        notify_parent_of_completion,
                    )

                    await notify_parent_of_completion(
                        self._session_factory, UUID(run_id)
                    )
                except Exception:
                    logger.exception(
                        "Failed to wake parent for child %s", run_id
                    )

        except Exception as e:
            logger.exception(f"Agent run {run_id} failed: {e}")
            if agent_run is not None:
                try:
                    async with self._session_factory() as db:
                        run_obj = await db.get(
                            AgentRun,
                            UUID(run_id),
                            with_for_update={"of": AgentRun},
                        )
                        if run_obj:
                            if run_obj.status == "running":
                                run_obj.status = "failed"
                                run_obj.error = str(e)
                                run_obj.duration_ms = int(
                                    (time.time() - start_time) * 1000
                                )
                                run_obj.completed_at = datetime.now(timezone.utc)
                            else:
                                logger.info(
                                    "Agent run %s: failure update skipped because current status is %s",
                                    run_id,
                                    run_obj.status,
                                )

                            # Still flush any buffered steps on failure
                            if executor:
                                await executor.flush_to_db(db)

                            await db.commit()
                            agent_run = run_obj
                except Exception:
                    logger.exception(f"Failed to update agent_run {run_id} after error")

                # Clean up Redis Stream on failure too
                try:
                    async with get_redis() as r:
                        await r.delete(agent_run_steps_stream_key(run_id))
                except Exception as cleanup_err:
                    # Stream cleanup is best-effort
                    logger.debug(f"failed to delete agent_run steps stream for {run_id}: {cleanup_err}")

                if trigger_type == "chat" or (context or {}).get(
                    "trigger_type"
                ) == "chat":
                    chat_conversation_id = (
                        (context or {}).get("input", {}).get("conversation_id")
                        or (context or {}).get("conversation_id")
                        or (agent_run.conversation_id if agent_run else None)
                    )
                    if chat_conversation_id is not None:
                        try:
                            await publish_chat_run_event(
                                conversation_id=UUID(str(chat_conversation_id)),
                                run_id=run_id,
                                kind="error",
                                status="failed",
                                payload=ChatStreamChunk(
                                    type="error",
                                    error=str(e),
                                    run_status="failed",
                                ),
                            )
                        except Exception as pub_err:
                            logger.debug(
                                "failed to publish terminal chat error for %s: %s",
                                run_id,
                                pub_err,
                            )

                try:
                    await publish_agent_run_update(
                        agent_run, agent.name if agent else "Unknown"
                    )
                except Exception as pub_err:
                    # Pubsub notify is a UI hint; the DB row already reflects the failure
                    logger.debug(f"failed to publish agent_run failure update for {run_id}: {pub_err}")

            if trigger_type != "chat":
                await _publish_sync_result(
                    run_id,
                    {
                        "output": agent_run.output if agent_run else None,
                        "status": agent_run.status if agent_run else "failed",
                        "error": agent_run.error if agent_run else str(e),
                    },
                )

        finally:
            # Legacy publishers wrote the full execution context to Redis;
            # the key is no longer written at enqueue. Deleting is harmless.
            try:
                async with get_redis() as r:
                    await r.delete(f"{REDIS_PREFIX}:{run_id}:context")
            except Exception as e:
                # Context key has a TTL; leaking one for a few minutes is harmless
                logger.debug(f"failed to delete agent_run context key for {run_id}: {e}")

    @staticmethod
    async def _load_legacy_context(run_id: str) -> dict[str, Any] | None:
        """Load a pre-upgrade Redis execution context, if one is in flight."""
        try:
            async with get_redis() as redis:
                context_raw = await redis.get(f"{REDIS_PREFIX}:{run_id}:context")
        except Exception:
            logger.debug(
                "Agent run %s: legacy context check unavailable", run_id
            )
            return None
        if not context_raw:
            return None
        try:
            return json.loads(context_raw)
        except (TypeError, ValueError):
            logger.warning("Agent run %s: legacy context is not JSON", run_id)
            return None

    async def _process_chat_run(
        self,
        *,
        run_id: str,
        context: dict[str, Any],
        agent_run: AgentRun,
        agent: Agent | None,
        sync: bool,
        start_time: float,
    ) -> None:
        input_data = context.get("input") or {}
        conversation_id_raw = (
            input_data.get("conversation_id")
            or context.get("conversation_id")
            or agent_run.conversation_id
        )
        if conversation_id_raw is None:
            raise ValueError(f"Chat run {run_id} is missing conversation_id")

        conversation_id = UUID(str(conversation_id_raw))
        user_message = str(input_data.get("content") or "")
        user_message_id_raw = input_data.get("user_message_id")
        persisted_user_message_id = (
            UUID(str(user_message_id_raw)) if user_message_id_raw else None
        )
        local_id = str(user_message_id_raw or run_id)
        model_profile_id_raw = input_data.get("model_profile_id")
        model_profile_id = (
            UUID(str(model_profile_id_raw)) if model_profile_id_raw else None
        )
        attachment_ids = [
            UUID(str(attachment_id))
            for attachment_id in (input_data.get("attachment_ids") or [])
        ]
        caller = _caller_to_principal(context.get("caller") or {})

        from src.services.ai_model_service import AIModelService

        async with self._session_factory() as db:
            model_service = AIModelService(db)
            _, resolved_config, _ = await model_service.resolve_chat_profile(
                model_profile_id
            )
            llm_model = resolved_config.model

        async with self._session_factory() as db:
            result = await db.execute(
                select(Conversation)
                .options(
                    selectinload(Conversation.agent).selectinload(Agent.tools),
                    selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
                    selectinload(Conversation.agent).selectinload(Agent.roles),
                    selectinload(Conversation.user),
                )
                .where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        chat_agent = conversation.agent or agent
        if chat_agent is None and agent_run.agent_id is not None:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(Agent)
                    .options(
                        selectinload(Agent.tools),
                        selectinload(Agent.delegated_agents),
                        selectinload(Agent.roles),
                    )
                    .where(Agent.id == agent_run.agent_id)
                )
                chat_agent = result.scalar_one_or_none()
        if chat_agent is None and input_data.get("agent_id"):
            async with self._session_factory() as db:
                result = await db.execute(
                    select(Agent)
                    .options(
                        selectinload(Agent.tools),
                        selectinload(Agent.delegated_agents),
                        selectinload(Agent.roles),
                    )
                    .where(Agent.id == UUID(str(input_data["agent_id"])))
                )
                chat_agent = result.scalar_one_or_none()

        run_timeout = (
            chat_agent.max_run_timeout
            if chat_agent is not None and chat_agent.max_run_timeout
            else DEFAULT_RUN_TIMEOUT
        )
        from src.services.agent_executor import AgentExecutor

        executor = AgentExecutor(self._session_factory)
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("Chat worker task missing")

        async with get_redis() as redis_for_executor:
            cancel_watcher = asyncio.create_task(
                self._cancel_watcher(run_id, current_task, redis_for_executor)
            )

            streamed_content = ""
            assistant_message_id: str | None = None
            terminal_status = "failed"
            terminal_error: str | None = None
            final_content: str | None = None
            final_finish_reason: str | None = None
            final_incomplete: bool | None = None
            iterations_used = 0
            tokens_used = 0
            timed_out = False

            try:
                try:
                    async with asyncio.timeout(run_timeout):
                        async for chunk in executor.chat(
                            chat_agent,
                            conversation,
                            user_message,
                            stream=True,
                            local_id=local_id,
                            user=caller,
                            attachment_ids=attachment_ids or None,
                            model_profile_id=model_profile_id,
                            user_message_id=persisted_user_message_id,
                        ):
                            await publish_chat_run_event(
                                conversation_id=conversation.id,
                                run_id=run_id,
                                kind=chunk.type,
                                status=_chat_chunk_status(chunk.type),
                                payload=chunk,
                            )

                            if (
                                chunk.type == "message_start"
                                and chunk.assistant_message_id
                            ):
                                assistant_message_id = chunk.assistant_message_id
                            elif chunk.type == "delta" and chunk.content:
                                streamed_content += chunk.content
                            elif chunk.type == "assistant_message_end":
                                streamed_content = ""
                                assistant_message_id = None
                            elif chunk.type == "done":
                                terminal_status = "completed"
                                final_content = (
                                    chunk.content
                                    if chunk.content is not None
                                    else streamed_content or None
                                )
                                final_finish_reason = chunk.finish_reason
                                final_incomplete = chunk.incomplete
                                usage = executor._active_usage
                                if usage is not None:
                                    iterations_used = usage.requests
                                    tokens_used = usage.total_tokens
                            elif chunk.type == "error":
                                terminal_status = "failed"
                                terminal_error = chunk.error or "Chat execution failed"
                                final_content = None
                                usage = executor._active_usage
                                if usage is not None:
                                    iterations_used = usage.requests
                                    tokens_used = usage.total_tokens
                except TimeoutError:
                    timed_out = True
                    raise asyncio.CancelledError from None

                duration_ms = int((time.time() - start_time) * 1000)
                if terminal_status == "completed":
                    output: dict[str, Any] | None = {
                        "text": final_content,
                        "finish_reason": final_finish_reason,
                        "incomplete": final_incomplete,
                    }
                elif terminal_status == "failed":
                    output = None
                else:
                    output = {"text": final_content, "partial": True}

                async with self._session_factory() as db:
                    run_obj = await db.get(
                        AgentRun,
                        UUID(run_id),
                        with_for_update={"of": AgentRun},
                    )
                    if run_obj is None:
                        logger.info(
                            "Chat run %s: final update skipped because row disappeared",
                            run_id,
                        )
                        return
                    elif run_obj.status in {"running", "cancelling"}:
                        run_obj.status = terminal_status
                        run_obj.output = output
                        run_obj.iterations_used = iterations_used
                        run_obj.tokens_used = tokens_used
                        run_obj.llm_model = executor._active_llm_model or llm_model
                        run_obj.duration_ms = duration_ms
                        run_obj.completed_at = datetime.now(timezone.utc)
                        if terminal_error:
                            run_obj.error = terminal_error
                        if executor._active_failover_path:
                            run_obj.run_metadata = {
                                **(run_obj.run_metadata or {}),
                                "failover_path": json.dumps(executor._active_failover_path),
                            }
                        await db.commit()
                    else:
                        logger.info(
                            "Chat run %s: final update skipped because current status is %s",
                            run_id,
                            run_obj.status,
                        )
                    agent_run_ref = run_obj

                await publish_agent_run_update(
                    agent_run_ref,
                    chat_agent.name if chat_agent else "Unknown",
                )

                if terminal_status == "completed" and conversation.title is None:
                    title = None
                    async with self._session_factory() as db:
                        conv = await db.get(
                            Conversation,
                            conversation.id,
                            with_for_update={"of": Conversation},
                        )
                        if conv is not None and conv.title is None:
                            title = await _generate_conversation_title(
                                db,
                                conv,
                                user_message,
                            )
                            if title:
                                conv.title = title
                                await db.commit()
                    if title:
                        await publish_chat_run_event(
                            conversation_id=conversation.id,
                            run_id=run_id,
                            kind="title_update",
                            status="completed",
                            payload=ChatStreamChunk(
                                type="title_update",
                                title=title,
                                run_status="completed",
                            ),
                        )

                if sync:
                    await _publish_sync_result(
                        run_id,
                        {
                            "output": agent_run_ref.output,
                            "status": agent_run_ref.status,
                            "error": agent_run_ref.error,
                            "iterations_used": agent_run_ref.iterations_used,
                            "tokens_used": agent_run_ref.tokens_used,
                            "llm_model": agent_run_ref.llm_model,
                        },
                    )
            except asyncio.CancelledError:
                duration_ms = int((time.time() - start_time) * 1000)
                if streamed_content and assistant_message_id is not None:
                    await executor._save_message(
                        conversation_id=conversation.id,
                        role=MessageRole.ASSISTANT,
                        content=streamed_content,
                        message_id=UUID(assistant_message_id),
                    )

                usage = executor._active_usage
                if usage is not None:
                    iterations_used = usage.requests
                    tokens_used = usage.total_tokens

                interrupted_status = "timeout" if timed_out else "cancelled"
                interrupted_kind: Literal["error", "cancelled"] = (
                    "error" if timed_out else "cancelled"
                )
                interrupted_error = (
                    f"Chat run timed out after {run_timeout}s"
                    if timed_out
                    else "Chat run cancelled"
                )
                interrupted_payload = ChatStreamChunk(
                    type=interrupted_kind,
                    content=streamed_content or None,
                    message_id=assistant_message_id,
                    run_status=interrupted_status,
                    duration_ms=duration_ms,
                    error=interrupted_error,
                )
                await publish_chat_run_event(
                    conversation_id=conversation.id,
                    run_id=run_id,
                    kind=interrupted_kind,
                    status=interrupted_status,
                    payload=interrupted_payload,
                )

                async with self._session_factory() as db:
                    run_obj = await db.get(
                        AgentRun,
                        UUID(run_id),
                        with_for_update={"of": AgentRun},
                    )
                    if run_obj is None:
                        logger.info(
                            "Chat run %s: cancel update skipped because row disappeared",
                            run_id,
                        )
                        return
                    if run_obj.status in {"running", "cancelling"}:
                        run_obj.status = interrupted_status
                        run_obj.output = {
                            "text": streamed_content or None,
                            "partial": True,
                        }
                        run_obj.iterations_used = iterations_used
                        run_obj.tokens_used = tokens_used
                        run_obj.llm_model = executor._active_llm_model or llm_model
                        run_obj.duration_ms = duration_ms
                        run_obj.completed_at = datetime.now(timezone.utc)
                        run_obj.error = interrupted_error
                        if executor._active_failover_path:
                            run_obj.run_metadata = {
                                **(run_obj.run_metadata or {}),
                                "failover_path": json.dumps(executor._active_failover_path),
                            }
                        await db.commit()
                    agent_run_ref = run_obj

                await publish_agent_run_update(
                    agent_run_ref,
                    chat_agent.name if chat_agent else "Chat",
                )

                if sync:
                    await _publish_sync_result(
                        run_id,
                        {
                            "output": agent_run_ref.output,
                            "status": agent_run_ref.status,
                            "error": agent_run_ref.error,
                            "iterations_used": agent_run_ref.iterations_used,
                            "tokens_used": agent_run_ref.tokens_used,
                            "llm_model": agent_run_ref.llm_model,
                        },
                    )
            finally:
                cancel_watcher.cancel()
                try:
                    await cancel_watcher
                except asyncio.CancelledError:
                    # Expected after explicitly cancelling the watcher above.
                    pass

    @staticmethod
    async def _lease_heartbeat(
        session_factory,
        run_id: str,
        lease_token: str,
        interval_seconds: int,
        stop_event: asyncio.Event,
        on_lost,
    ) -> None:
        """Renew the worker lease until the attempt ends.

        If renewal fails the lease is gone (another worker claimed the run
        or the row terminalized); ``on_lost`` stops this attempt so work is
        never duplicated.
        """
        try:
            while not stop_event.is_set():
                await asyncio.sleep(interval_seconds)
                if stop_event.is_set():
                    return
                try:
                    async with session_factory() as db:
                        await run_store.renew_lease(
                            db,
                            UUID(run_id),
                            lease_token,
                            lease_ttl_seconds=AGENT_RUN_LEASE_TTL_SECONDS,
                        )
                except Exception as exc:
                    logger.warning(
                        "Agent run %s: lease renewal failed (%s); "
                        "yielding to the new owner",
                        run_id,
                        exc,
                    )
                    on_lost()
                    return
        except asyncio.CancelledError:
            pass  # Normal cleanup when the attempt finishes

    @staticmethod
    async def _cancel_watcher(
        run_id: str,
        task: asyncio.Task,  # pyright: ignore[reportMissingTypeArgument]
        redis_client: object,
    ) -> None:
        """Background task that cancels the executor if Redis cancel flag is set.

        This handles the case where the executor is stuck (e.g., hanging LLM call)
        and can't check the cancel flag itself between iterations.
        """
        try:
            while not task.done():
                try:
                    key = f"bifrost:agent_run:{run_id}:cancel"
                    result = await redis_client.get(key)  # pyright: ignore[reportAttributeAccessIssue]
                    if result is not None:
                        logger.info(f"Cancel watcher: cancelling stuck task for run {run_id}")
                        task.cancel()
                        return
                except Exception:
                    pass  # Don't let Redis errors kill the watcher
                await asyncio.sleep(CANCEL_CHECK_INTERVAL)
        except asyncio.CancelledError:
            pass  # Normal cleanup when executor finishes

    @staticmethod
    async def _update_event_delivery(
        db,
        event_delivery_id: str,
        agent_run_id: str,
        run_status: str,
        error_message: str | None = None,
    ) -> None:
        """Update EventDelivery status after agent run completes."""
        from src.models.orm.events import EventDelivery
        from src.models.enums import EventDeliveryStatus
        from src.repositories.events import EventDeliveryRepository

        try:
            result = await db.execute(
                select(EventDelivery).where(
                    EventDelivery.id == UUID(event_delivery_id)
                )
            )
            delivery = result.scalar_one_or_none()
            if not delivery:
                return

            # Map agent run status to delivery status
            if run_status == "completed":
                delivery.status = EventDeliveryStatus.SUCCESS
            else:
                delivery.status = EventDeliveryStatus.FAILED
                delivery.error_message = error_message

            delivery.agent_run_id = UUID(agent_run_id)
            delivery.completed_at = datetime.now(timezone.utc)
            delivery.attempt_count += 1
            await db.flush()

            # Update parent event status
            delivery_repo = EventDeliveryRepository(db)
            await delivery_repo.update_event_status(delivery.event_id)

            await db.commit()
        except Exception:
            logger.exception(f"Failed to update event delivery {event_delivery_id}")
