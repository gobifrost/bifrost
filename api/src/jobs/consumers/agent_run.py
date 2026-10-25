"""Runs the agent-run dispatches an executor claimed from PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.cache.keys import agent_run_steps_stream_key
from src.core.cache.redis_client import get_redis
from src.core.database import get_session_factory
from src.core.pubsub import publish_agent_run_update, publish_chat_run_event
from src.models.contracts.agents import ChatStreamChunk
from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.ai_behavior_service import AgentExecutionMode, AIBehaviorService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.services.authorization.enforce import Caller
    from src.services.execution.autonomous_agent_executor import ActingUser

logger = logging.getLogger(__name__)

REDIS_PREFIX = "bifrost:agent_run"
DEFAULT_RUN_TIMEOUT = 1800  # 30 minutes
CANCEL_CHECK_INTERVAL = 2  # seconds between cancel flag checks
RUN_AS_INACTIVE_ERROR = "The user this run acts as is no longer active"
ACTING_USER_UNAVAILABLE_ERROR = "Agent run actor is unavailable"


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



async def _load_acting_user(
    db: AsyncSession,
    acting_user_id: UUID | None,
) -> ActingUser | None:
    """Load the persisted actor's current facts, or fail closed if absent."""
    if acting_user_id is None:
        return None

    row = (
        await db.execute(
            select(User, Organization.is_provider)
            .outerjoin(Organization, Organization.id == User.organization_id)
            .where(User.id == acting_user_id)
        )
    ).one_or_none()
    if row is None:
        return None

    user, is_provider_org = row
    from src.services.execution.autonomous_agent_executor import ActingUser

    acting = ActingUser(
        user_id=str(user.id),
        email=user.email,
        name=user.name or user.email,
        organization_id=user.organization_id,
        is_platform_admin=user.is_superuser,
        is_external=user.is_external,
        is_provider_org=bool(is_provider_org),
    )
    return acting


async def _load_run_as(
    db: AsyncSession,
    run_as_user_id: UUID,
    run_org_id: UUID | None,
) -> ActingUser | None:
    """The run's Run As user from the database; None when it can no longer act.

    The identity comes from the ``agent_runs`` and ``users`` rows only, never
    from the run's dispatch context. Its organization is the run's.
    """
    from src.services.authorization.explain import load_run_as_target
    from src.services.execution.autonomous_agent_executor import ActingUser

    target = await load_run_as_target(db, run_as_user_id)
    if target is None or not target.is_active or target.is_system:
        return None
    return ActingUser(
        user_id=str(target.user_id),
        email=target.email,
        name=target.name or target.email,
        organization_id=run_org_id,
        is_platform_admin=target.is_superuser,
        is_external=target.is_external,
        is_provider_org=target.is_provider_org,
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


class AgentRunConsumer:
    def __init__(self):
        self._session_factory = get_session_factory()

    async def process_message(self, body: dict) -> None:
        from src.services.authorization.enforce import load_caller
        from src.services.authorization.runtime_auth import load_runtime_principal

        run_id = body["run_id"]
        agent_id = body.get("agent_id")
        trigger_type = body["trigger_type"]
        sync = body.get("sync", False)

        logger.info(f"Processing agent run {run_id} (agent={agent_id}, trigger={trigger_type})")

        # The dispatch carries the context in PostgreSQL; it is taken with
        # the claim below.
        dispatch_id = UUID(body["dispatch_id"])
        context: dict | None = None

        start_time = time.time()
        agent_run: AgentRun | None = None
        agent: Agent | None = None
        executor = None
        acting: ActingUser | None = None
        runtime_caller: Caller | None = None
        agent_execution_mode: AgentExecutionMode | None = None

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
                from src.services.work_dispatch import take_dispatch_payload

                context = await take_dispatch_payload(db, dispatch_id)
                if agent_run.status != "queued":
                    # A duplicate delivery or a cancelled run; any dispatch
                    # left behind is dropped by the reconciler.
                    logger.info(
                        "Agent run %s: skipping message for status %s",
                        run_id,
                        agent_run.status,
                    )
                    return
                if context is None:
                    raise RuntimeError(f"Queued agent run {run_id} has no dispatch")
                loaded_actor = await _load_acting_user(db, agent_run.acting_user_id)
                if loaded_actor is None:
                    logger.info("Agent run %s: its persisted actor is unavailable", run_id)
                    agent_run.status = "failed"
                    agent_run.error = ACTING_USER_UNAVAILABLE_ERROR
                    agent_run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    if sync:
                        await _publish_sync_result(
                            run_id,
                            {
                                "output": None,
                                "status": "failed",
                                "error": ACTING_USER_UNAVAILABLE_ERROR,
                            },
                        )
                    return
                acting = loaded_actor

                is_chat_trigger = trigger_type == "chat" or context.get("trigger_type") == "chat"
                if is_chat_trigger:
                    agent_execution_mode = await AIBehaviorService(
                        db
                    ).resolve_agent_run_execution_mode(agent_run)
                    assert agent_execution_mode is not None
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
                    runtime_principal = await load_runtime_principal(
                        db,
                        {
                            "credential_kind": "agent_run",
                            "sub": str(agent_run.id),
                        },
                    )
                    if runtime_principal is None:
                        raise ValueError("Chat run runtime authority is unavailable")
                    chat_caller = await load_caller(db, runtime_principal)
                    if chat_caller.ctx is None:
                        raise ValueError("Chat run caller is unavailable")
                    await publish_agent_run_update(agent_run, "Unknown")
                    await self._process_chat_run(
                        run_id=run_id,
                        context=context,
                        agent_run=agent_run,
                        agent=None,
                        caller=chat_caller,
                        sync=sync,
                        start_time=start_time,
                        agent_execution_mode=agent_execution_mode,
                    )
                    return

                if agent_id is None:
                    logger.error(f"Agent run {run_id}: agent id missing for non-chat run")
                    agent_run.status = "failed"
                    agent_run.error = "Agent id missing"
                    agent_run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    if sync:
                        await _publish_sync_result(
                            run_id,
                            {
                                "output": None,
                                "status": "failed",
                                "error": "Agent id missing",
                            },
                        )
                    return

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
                    if sync:
                        await _publish_sync_result(
                            run_id,
                            {
                                "output": None,
                                "status": "failed",
                                "error": "Agent no longer exists",
                            },
                        )
                    return

                run_as: ActingUser | None = None
                if agent_run.run_as_user_id is not None:
                    run_as = await _load_run_as(
                        db, agent_run.run_as_user_id, agent_run.org_id
                    )
                    if run_as is None:
                        logger.info(
                            "Agent run %s: its Run As user can no longer act",
                            run_id,
                        )
                        agent_run.status = "failed"
                        agent_run.error = RUN_AS_INACTIVE_ERROR
                        agent_run.completed_at = datetime.now(timezone.utc)
                        await db.commit()
                        if sync:
                            await _publish_sync_result(
                                run_id,
                                {
                                    "output": None,
                                    "status": "failed",
                                    "error": RUN_AS_INACTIVE_ERROR,
                                },
                            )
                        return

                agent_run.status = "running"
                agent_execution_mode = await AIBehaviorService(
                    db
                ).resolve_agent_run_execution_mode(agent_run)
                agent_run.budget_max_iterations = agent.max_iterations
                agent_run.budget_max_tokens = agent.max_token_budget
                agent_run.started_at = datetime.now(timezone.utc)
                await db.commit()
                runtime_principal = await load_runtime_principal(
                    db,
                    {
                        "credential_kind": "agent_run",
                        "sub": str(agent_run.id),
                    },
                )
                if runtime_principal is None:
                    raise ValueError("Agent run runtime authority is unavailable")
                runtime_caller = await load_caller(db, runtime_principal)
                if runtime_caller.ctx is None:
                    raise ValueError("Agent run caller is unavailable")

            await publish_agent_run_update(agent_run, agent.name if agent else "Unknown")

            # Run the agent with timeout (Layer 1: hard safety net)
            run_timeout = agent.max_run_timeout or DEFAULT_RUN_TIMEOUT

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
                    caller=runtime_caller,
                    run_user_id=agent_run.run_user_id,
                    run_as=run_as,
                    acting=acting,
                    agent_execution_mode=agent_execution_mode,
                ))

                # Cancel watcher: polls Redis flag, force-cancels task if stuck
                cancel_watcher = asyncio.ensure_future(
                    AgentRunConsumer._cancel_watcher(run_id, executor_task, redis_for_executor)
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
                        "tokens_used": executor.subtree_tokens,
                        "status": "timeout",
                        "llm_model": None,
                        "error": f"Agent run timed out after {run_timeout}s",
                    }
                except asyncio.CancelledError:
                    run_result = {
                        "output": None,
                        "iterations_used": 0,
                        "tokens_used": executor.subtree_tokens,
                        "status": "cancelled",
                        "llm_model": None,
                    }
                finally:
                    cancel_watcher.cancel()
                    try:
                        await cancel_watcher
                    except asyncio.CancelledError:
                        # Expected — we just cancelled the watcher
                        pass

            # Update run record and flush buffered steps (brief DB session)
            duration_ms = int((time.time() - start_time) * 1000)
            consumer_applied_result = False
            summary = None
            async with self._session_factory() as db:
                # Re-fetch the AgentRun in this session to update it, but only
                # if it is still live. A scheduler may have already terminalized
                # the row; in that case we must not overwrite the final status.
                run_obj = await db.get(
                    AgentRun,
                    UUID(run_id),
                    with_for_update={"of": AgentRun},
                )
                if run_obj is None:
                    logger.info(f"Agent run {run_id}: final update skipped because row disappeared")
                    return
                if run_obj.status == "running":
                    run_obj.status = run_result.get("status", "completed")
                    run_obj.output = (
                        run_result.get("output")
                        if isinstance(run_result.get("output"), dict)
                        else {"text": run_result.get("output")}
                    )
                    run_obj.iterations_used = run_result.get("iterations_used", 0)
                    run_obj.tokens_used = run_result.get("tokens_used", 0)
                    run_obj.llm_model = run_result.get("llm_model")
                    run_obj.duration_ms = duration_ms
                    run_obj.completed_at = datetime.now(timezone.utc)
                    if run_result.get("error"):
                        run_obj.error = run_result["error"]
                    if run_result.get("failover_path"):
                        run_obj.run_metadata = {
                            **(run_obj.run_metadata or {}),
                            # run_metadata is a string-valued map on the wire
                            # (AgentRunResponse.metadata); encode the path.
                            "failover_path": json.dumps(run_result["failover_path"]),
                        }
                    consumer_applied_result = True
                else:
                    logger.info(
                        "Agent run %s: final update skipped because current status is %s",
                        run_id,
                        run_obj.status,
                    )

                # Flush metering and steps even when the scheduler won the
                # terminal-state race; completed provider work still incurred
                # cost and remains useful diagnostic evidence.
                if executor:
                    await executor.flush_to_db(db)

                # A completed run's summary is requested with its result, so
                # it is never stranded; failures and cancellations are not
                # summarized (the UI offers regenerate from any state).
                if consumer_applied_result and run_obj.status == "completed":
                    from src.services.execution.summary_dispatch import stage_summary

                    summary = await stage_summary(db, run_obj.id)

                await db.commit()

                # Re-read for publish (need agent relationship)
                if run_obj:
                    agent_run = run_obj

            # Clean up Redis Stream now that steps are committed to DB. Its TTL
            # bounds the leak if this best-effort delete cannot reach Redis.
            try:
                async with get_redis() as r:
                    await r.delete(agent_run_steps_stream_key(run_id))
            except Exception as e:
                # Stream cleanup is best-effort; write-time TTL bounds a leak.
                logger.debug(f"failed to delete agent_run steps stream for {run_id}: {e}")

            await publish_agent_run_update(agent_run, agent.name)

            from src.services.execution.summary_dispatch import hand_off_summary

            await hand_off_summary(summary)

            # Update event delivery status if triggered by event
            if context.get("event_delivery_id"):
                async with self._session_factory() as db:
                    await self._update_event_delivery(
                        db,
                        event_delivery_id=context["event_delivery_id"],
                        agent_run_id=run_id,
                        run_status=agent_run.status,
                        error_message=agent_run.error,
                    )

            # If sync, push result for BLPOP waiter
            if sync:
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

                # Clean up Redis Stream on failure too; write-time TTL bounds a leak.
                try:
                    async with get_redis() as r:
                        await r.delete(agent_run_steps_stream_key(run_id))
                except Exception as cleanup_err:
                    # Stream cleanup is best-effort; write-time TTL bounds a leak.
                    logger.debug(f"failed to delete agent_run steps stream for {run_id}: {cleanup_err}")

                # The failure may precede the claim that loads the context.
                known = context or {}
                if trigger_type == "chat" or known.get("trigger_type") == "chat":
                    chat_conversation_id = (
                        (known.get("input") or {}).get("conversation_id")
                        or known.get("conversation_id")
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

            if sync:
                await _publish_sync_result(
                    run_id,
                    {
                        "output": agent_run.output if agent_run else None,
                        "status": agent_run.status if agent_run else "failed",
                        "error": agent_run.error if agent_run else str(e),
                    },
                )


    async def _process_chat_run(
        self,
        *,
        run_id: str,
        context: dict[str, Any],
        agent_run: AgentRun,
        agent: Agent | None,
        caller: Caller,
        sync: bool,
        start_time: float,
        agent_execution_mode: AgentExecutionMode = "delegated",
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
                            caller=caller,
                            attachment_ids=attachment_ids or None,
                            model_profile_id=model_profile_id,
                            user_message_id=persisted_user_message_id,
                            run_user_id=agent_run.run_user_id,
                            agent_execution_mode=agent_execution_mode,
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
        from src.models.enums import EventDeliveryStatus
        from src.repositories.events import EventDeliveryRepository

        try:
            # Map agent run status to delivery status
            if run_status == "completed":
                delivery_status = EventDeliveryStatus.SUCCESS
            else:
                delivery_status = EventDeliveryStatus.FAILED

            delivery_repo = EventDeliveryRepository(db)
            delivery = await delivery_repo.complete_from_agent_run(
                UUID(event_delivery_id),
                UUID(agent_run_id),
                delivery_status,
                error_message,
            )
            if delivery is None:
                return

            # Update parent event status
            await delivery_repo.update_event_status(delivery.event_id)

            await db.commit()
        except Exception:
            logger.exception(f"Failed to update event delivery {event_delivery_id}")
