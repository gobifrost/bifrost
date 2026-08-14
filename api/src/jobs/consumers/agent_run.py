"""RabbitMQ consumer for autonomous agent runs."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.core.pubsub import publish_agent_run_update
from src.core.cache.keys import agent_run_steps_stream_key
from src.core.cache.redis_client import get_redis
from src.core.database import get_session_factory
from src.jobs.rabbitmq import BaseConsumer
from src.models.orm.agents import Agent
from src.models.orm.agent_runs import AgentRun
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"
DEFAULT_RUN_TIMEOUT = 1800  # 30 minutes
CANCEL_CHECK_INTERVAL = 2  # seconds between cancel flag checks


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
        agent_id = body["agent_id"]
        trigger_type = body["trigger_type"]
        sync = body.get("sync", False)

        logger.info(f"Processing agent run {run_id} (agent={agent_id}, trigger={trigger_type})")

        # Read full context from Redis
        redis_key = f"{REDIS_PREFIX}:{run_id}:context"
        async with get_redis() as redis:
            context_raw = await redis.get(redis_key)

        if not context_raw:
            logger.error(f"Agent run {run_id}: context not found in Redis")
            return

        context = json.loads(context_raw)

        # Pre-cancel check: if cancelled before worker picked it up, skip execution
        if context.get("cancelled"):
            logger.info(f"Agent run {run_id}: pre-cancelled, skipping execution")
            async with self._session_factory() as db:
                agent_run = AgentRun(
                    id=UUID(run_id),
                    agent_id=UUID(agent_id),
                    trigger_type=trigger_type,
                    status="cancelled",
                    org_id=UUID(context["org_id"]) if context.get("org_id") else None,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                )
                db.add(agent_run)
                await db.commit()
            return

        start_time = time.time()
        agent_run: AgentRun | None = None
        agent: Agent | None = None
        executor: AutonomousAgentExecutor | None = None

        try:
            # Load agent with relationships (brief DB session)
            async with self._session_factory() as db:
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

            if not agent:
                logger.error(f"Agent run {run_id}: agent {agent_id} not found")
                return

            # Create AgentRun record (brief DB session)
            async with self._session_factory() as db:
                agent_run = AgentRun(
                    id=UUID(run_id),
                    agent_id=agent.id,
                    trigger_type=trigger_type,
                    trigger_source=context.get("trigger_source"),
                    event_delivery_id=UUID(context["event_delivery_id"]) if context.get("event_delivery_id") else None,
                    input=context.get("input"),
                    output_schema=context.get("output_schema"),
                    status="running",
                    org_id=UUID(context["org_id"]) if context.get("org_id") else None,
                    caller_user_id=context["caller"].get("user_id") if context.get("caller") else None,
                    caller_email=context["caller"].get("email") if context.get("caller") else None,
                    caller_name=context["caller"].get("name") if context.get("caller") else None,
                    budget_max_iterations=agent.max_iterations,
                    budget_max_tokens=agent.max_token_budget,
                    started_at=datetime.now(timezone.utc),
                )
                db.add(agent_run)
                await db.commit()

            await publish_agent_run_update(agent_run, agent.name)

            # Run the agent with timeout (Layer 1: hard safety net)
            run_timeout = agent.max_run_timeout or DEFAULT_RUN_TIMEOUT

            async with get_redis() as redis_for_executor:
                executor = AutonomousAgentExecutor(self._session_factory, redis_client=redis_for_executor)

                # Create executor task so cancel watcher can cancel it
                executor_task = asyncio.ensure_future(executor.run(
                    agent=agent,
                    input_data=context.get("input"),
                    output_schema=context.get("output_schema"),
                    run_id=run_id,
                    _caller=context.get("caller"),
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
                        "tokens_used": 0,
                        "status": "timeout",
                        "llm_model": None,
                        "error": f"Agent run timed out after {run_timeout}s",
                    }
                except asyncio.CancelledError:
                    run_result = {
                        "output": None,
                        "iterations_used": 0,
                        "tokens_used": 0,
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
            async with self._session_factory() as db:
                # Re-fetch the AgentRun in this session to update it
                run_obj = await db.get(AgentRun, UUID(run_id))
                if run_obj:
                    run_obj.status = run_result.get("status", "completed")
                    run_obj.output = run_result.get("output") if isinstance(run_result.get("output"), dict) else {"text": run_result.get("output")}
                    run_obj.iterations_used = run_result.get("iterations_used", 0)
                    run_obj.tokens_used = run_result.get("tokens_used", 0)
                    run_obj.llm_model = run_result.get("llm_model")
                    run_obj.duration_ms = duration_ms
                    run_obj.completed_at = datetime.now(timezone.utc)
                    if run_result.get("error"):
                        run_obj.error = run_result["error"]

                # Flush executor's buffered steps and AI usage
                if executor:
                    await executor.flush_to_db(db)

                await db.commit()

                # Re-read for publish (need agent relationship)
                if run_obj:
                    agent_run = run_obj

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
            if run_result.get("status") == "completed":
                try:
                    from src.services.execution.run_summarizer import enqueue_summarize
                    await enqueue_summarize(UUID(run_id))
                except Exception:
                    logger.exception(
                        f"Failed to enqueue summarizer for run {run_id}"
                    )

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
                result_key = f"{REDIS_PREFIX}:{run_id}:result"
                async with get_redis() as r:
                    # redis-py 7.x stubs type lpush as -> int, but it's async at runtime
                    await r.lpush(result_key, json.dumps({  # pyright: ignore[reportGeneralTypeIssues]
                        "output": run_result.get("output"),
                        "status": run_result.get("status", "completed"),
                        "error": run_result.get("error"),
                        "iterations_used": run_result.get("iterations_used", 0),
                        "tokens_used": run_result.get("tokens_used", 0),
                        "llm_model": run_result.get("llm_model"),
                    }))
                    await r.expire(result_key, 300)

        except Exception as e:
            logger.exception(f"Agent run {run_id} failed: {e}")
            if agent_run is not None:
                try:
                    async with self._session_factory() as db:
                        run_obj = await db.get(AgentRun, UUID(run_id))
                        if run_obj:
                            run_obj.status = "failed"
                            run_obj.error = str(e)
                            run_obj.duration_ms = int((time.time() - start_time) * 1000)
                            run_obj.completed_at = datetime.now(timezone.utc)

                            # Still flush any buffered steps on failure
                            if executor:
                                await executor.flush_to_db(db)

                            await db.commit()
                except Exception:
                    logger.exception(f"Failed to update agent_run {run_id} after error")

                # Clean up Redis Stream on failure too
                try:
                    async with get_redis() as r:
                        await r.delete(agent_run_steps_stream_key(run_id))
                except Exception as cleanup_err:
                    # Stream cleanup is best-effort
                    logger.debug(f"failed to delete agent_run steps stream for {run_id}: {cleanup_err}")

                try:
                    await publish_agent_run_update(
                        agent_run, agent.name if agent else "Unknown"
                    )
                except Exception as pub_err:
                    # Pubsub notify is a UI hint; the DB row already reflects the failure
                    logger.debug(f"failed to publish agent_run failure update for {run_id}: {pub_err}")

            if sync:
                result_key = f"{REDIS_PREFIX}:{run_id}:result"
                async with get_redis() as r:
                    await r.lpush(result_key, json.dumps({  # pyright: ignore[reportGeneralTypeIssues]
                        "output": None,
                        "status": "failed",
                        "error": str(e),
                    }))
                    await r.expire(result_key, 300)

        finally:
            try:
                async with get_redis() as r:
                    await r.delete(f"{REDIS_PREFIX}:{run_id}:context")
            except Exception as e:
                # Context key has a TTL; leaking one for a few minutes is harmless
                logger.debug(f"failed to delete agent_run context key for {run_id}: {e}")

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
