"""Autonomous agent executor — runs agents without chat/streaming concerns.

Used for event-triggered, schedule-triggered, and SDK-triggered agent runs.
Records every step as an AgentRunStep for full observability.

Connection management: This executor uses a Redis-first pattern — steps and AI
usage are buffered in memory/Redis during execution. DB connections are only
acquired briefly for reads (tool resolution, LLM config, knowledge search) and
released immediately. All buffered data is flushed to Postgres in a single
batch after the run completes via flush_to_db().
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from pydantic_ai import (
    Agent as PydanticAgent,
    CallDeferred,
    DeferredToolRequests,
    DeferredToolResults,
    capture_run_messages,
)
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from src.models.orm.agents import Agent, AgentDelegation
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.core.constants import SYSTEM_USER_ID, SYSTEM_USER_EMAIL
from src.core.cache.keys import agent_run_steps_stream_key
from src.core.pubsub import publish_agent_run_step
from src.services.execution.agent_helpers import (
    build_agent_system_prompt,
    find_delegated_agent,
    parse_mcp_tool_name,
    resolve_agent_tools,
)
from src.services.execution.agent_workflow_tools import (
    AgentWorkflowCaller,
    execute_agent_workflow_tool,
)
from src.services.agent_runtime import (
    AgentRunBudget,
    AgentRunCancelled,
    BifrostToolset,
    ModelCallEvent,
    ToolEvent,
    build_chain_model,
    build_runtime_capabilities,
    provider_reported_cost,
)
from src.services.agent_runtime.empty_output import EmptyOutputCircuitBreaker
from src.services.llm import ToolCallRequest
from src.services.llm.factory import get_llm_configs
from src.services.knowledge.search_budget import (
    KNOWLEDGE_FULL_CONTENT_HINT,
    KnowledgeSearchBudget,
    build_compact_knowledge_document,
    clamp_knowledge_result_limit,
    compact_knowledge_metadata,
    knowledge_search_rejection_payload,
    parse_knowledge_followup,
    select_novel_knowledge_evidence,
)
from src.services.mcp_client import dispatch as mcp_dispatch
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)

logger = logging.getLogger(__name__)

MAX_DELEGATION_DEPTH = 5  # Prevent infinite delegation chains
DELEGATION_TIMEOUT_SECONDS = 600  # 10 minutes per delegation


class ToolError(Exception):
    """Raised when a tool call fails in an expected way (unknown tool, delegation failure, etc.)."""
    pass


@dataclass(frozen=True)
class DelegationOutcome:
    """Canonical internal result for a delegated child run."""

    child_run_id: UUID
    agent_name: str
    status: str
    output: str | dict | None
    error: str | None
    duration_ms: int

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"


class AutonomousAgentExecutor:
    """Execute an agent autonomously (no streaming, no chat session).

    Handles the full tool-calling loop: LLM call -> tool dispatch -> LLM call,
    recording each step as an AgentRunStep for audit and debugging.

    Uses a Redis-first pattern: steps are written to Redis Stream during
    execution and flushed to Postgres after the run completes. No DB
    connection is held during LLM calls or tool execution.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: aioredis.Redis | None = None,
        *,
        _delegation_depth: int = 0,
        _ancestor_run_ids: tuple[str, ...] = (),
    ):
        self._session_factory = session_factory
        self.redis_client = redis_client
        self._delegation_depth = _delegation_depth
        self._ancestor_run_ids = _ancestor_run_ids
        self._tool_workflow_id_map: dict[str, UUID] = {}
        self._current_run_id: str = ""
        self._last_delegation_run_id: str | None = None
        self._last_workflow_execution_id: str | None = None
        self._last_workflow_execution_is_error = False
        # Caller_user_id for the active run, threaded into MCP dispatch.
        # ``None`` means the run is autonomous (scheduled / webhook /
        # event-trigger), in which case dispatch resolves to the
        # service token only.
        self._caller_user_id: UUID | None = None
        self._caller: dict[str, Any] | None = None
        # Buffers for Redis-first pattern (flushed to DB after run completes)
        self._pending_steps: list[dict[str, Any]] = []
        self._pending_ai_usage: list[dict[str, Any]] = []
        # Durable boundary recording. When a lease token is supplied, every
        # completed model response and tool result is checkpointed to
        # PostgreSQL immediately instead of buffered to end of run.
        self._durable_lease_token: str | None = None
        self._durable_history: list | None = None
        self._last_checkpoint_sequence: int | None = None
        # Engine operation ID for the tool call currently dispatching,
        # exposed to workflow/system tools as an idempotency key.
        self._current_operation_id: str | None = None
        # Immutable snapshot retained for deferral-time grant checks.
        self._snapshot_for_deferral: dict[str, Any] | None = None
        self._knowledge_search_budget = KnowledgeSearchBudget()
        # Delegated executors receive these same objects. Pydantic AI mutates
        # RunUsage in place, so every model request in the delegation tree is
        # charged to the root run instead of giving each child a fresh budget.
        self._active_usage: RunUsage | None = None
        self._active_budget: AgentRunBudget | None = None

    async def run(
        self,
        agent: Agent,
        *,
        input_data: dict | None = None,
        output_schema: dict | None = None,
        run_id: str | None = None,
        execution_snapshot: dict[str, Any] | None = None,
        lease_token: str | None = None,
        resume_history: list | None = None,
        caller_context: dict[str, Any] | None = None,
        correlation: dict[str, Any] | None = None,
        deferred_results: dict[str, str] | None = None,
        deferred_pending: set[str] | None = None,
        _caller: dict | None = None,
        _shared_usage: RunUsage | None = None,
        _shared_budget: AgentRunBudget | None = None,
    ) -> dict:
        """Execute an autonomous agent run.

        Args:
            agent: The Agent ORM instance to execute.
            input_data: Input payload (serialized as JSON in the user message).
            output_schema: Optional JSON Schema the agent should conform its output to.
            run_id: External run ID (generates one if not provided).
            execution_snapshot: Immutable configuration pinned at enqueue.
                When present, prompt, model identity, tool set, and limits
                come from the snapshot instead of the live Agent row, so a
                resumed run never silently adopts an edited Agent.
            lease_token: Current worker lease token for this run. When
                present, model/tool boundaries checkpoint durably instead of
                buffering to end of run.
            resume_history: Decoded checkpoint history from a previous
                attempt. Planned calls execute in a pre-pass; committed
                results replay without re-execution; then the model loop
                continues from the completed history.
            caller_context: Minimal durable locators (e.g. ticket ID) handed
                to delegated children with the parent-selected task.
            correlation: Bounded correlation metadata copied to children.
            deferred_results: tool_call_id -> result text for children that
                terminalized while the parent was suspended. Skipped by the
                pre-pass and delivered through DeferredToolResults.
            deferred_pending: journaled delegation calls with no terminal
                child yet. Non-empty here means a bad wake; the attempt fails
                loudly instead of continuing on a dangling tool call.
            _caller: Optional caller metadata for context.
            _shared_usage: Internal cumulative usage ledger inherited from a
                parent run during delegation.
            _shared_budget: Internal hard ceiling inherited from a parent run.

        Returns:
            Dict with keys: output, iterations_used, tokens_used, status, llm_model
            (and optionally "error" if status is "failed").
        """
        run_id = run_id or str(uuid4())
        self._current_run_id = run_id
        self._durable_lease_token = lease_token
        self._snapshot_for_deferral = execution_snapshot
        self._knowledge_search_budget.reset()
        if deferred_pending:
            raise ToolError(
                "Cannot resume: delegated children "
                f"{sorted(deferred_pending)} have no terminal result yet."
            )

        # Resolve caller_user_id from _caller metadata. If a webhook ran
        # without a signed user claim, _caller is either absent or has no
        # ``user_id`` and the run is treated as autonomous (None) — auth
        # resolution will then route to the service token, gated by the
        # connection's ``available_to_autonomous`` flag.
        caller_user_id: UUID | None = None
        if _caller and _caller.get("user_id"):
            try:
                caller_user_id = UUID(str(_caller["user_id"]))
            except (ValueError, TypeError):
                logger.warning(
                    "Autonomous run %s: _caller.user_id %r is not a valid UUID; "
                    "treating run as fully autonomous",
                    run_id,
                    _caller.get("user_id"),
                )
                caller_user_id = None
        self._caller_user_id = caller_user_id
        self._caller = dict(_caller) if _caller else None

        if execution_snapshot is not None:
            from src.services.agent_runtime.execution_snapshot import (
                validate_snapshot,
            )

            execution_snapshot = validate_snapshot(execution_snapshot)
            snapshot_model = execution_snapshot["model"]
            snapshot_limits = execution_snapshot.get("limits", {})
            profile_id = (
                UUID(snapshot_model["profile_id"])
                if snapshot_model.get("profile_id")
                else None
            )
        else:
            snapshot_model = None
            snapshot_limits = {}
            profile_id = agent.llm_profile_id

        async with self._session_factory() as db:
			llm_config = await get_llm_config(db, profile_id=profile_id)
		model_name = (
			snapshot_model["model"] if snapshot_model else llm_config.model
		)

        # Short-circuit if agent is paused. Runs already past this point continue
        # normally — this check only gates new runs at entry. Snapshot-backed
        # runs were admitted against an active Agent; resume must not die
        # because someone paused mid-run.
        if not agent.is_active and execution_snapshot is None:
            return {
                "output": None,
                "iterations_used": 0,
                "tokens_used": 0,
                "status": "paused",
                "accepted": False,
                "message": f"Agent '{agent.name}' is paused. Request not processed.",
                "llm_model": model_name,
            }

        step_number = 0
        configured_iterations = snapshot_limits.get(
            "max_iterations", agent.max_iterations
        )
        configured_tokens = snapshot_limits.get(
            "max_token_budget", agent.max_token_budget
        )
        usage = _shared_usage
        if usage is None and resume_history is not None:
            # Continuing the same AgentRun: seed the ledger from stored
            # counters so snapshot budgets apply cumulatively across attempts.
            # Only totals survive per attempt; attribute them to input so the
            # summed total (what limits enforce) stays exact.
            seed_requests, seed_tokens = 0, 0
            async with self._session_factory() as db:
                prior = await db.get(AgentRun, UUID(run_id))
                if prior is not None:
                    seed_requests = prior.iterations_used or 0
                    seed_tokens = prior.tokens_used or 0
            usage = RunUsage(requests=seed_requests, input_tokens=seed_tokens)
        usage = usage or RunUsage()
        usage_start_requests = usage.requests
        usage_start_tokens = usage.total_tokens

        # A child gets at most its own configured allowance, but never escapes
        # the ceiling inherited from its parent. Grandchildren inherit the
        # child's effective subtree ceiling. The root starts at zero, so these
        # are simply its configured limits there.
        if _shared_budget is None:
            budget = AgentRunBudget(
                max_requests=configured_iterations,
                max_total_tokens=configured_tokens,
            )
        else:
            budget = _shared_budget.child_subtree(
                current_requests=usage_start_requests,
                current_total_tokens=usage_start_tokens,
                child_max_requests=configured_iterations,
                child_max_total_tokens=configured_tokens,
            )
        max_iterations = budget.max_requests
        max_tokens = budget.max_total_tokens
        self._active_usage = usage
        self._active_budget = budget

        # Resolve tools in one short DB lease. No DB
        # connection is held across model requests or tool execution.
        # Snapshot-backed runs rebuild definitions from the pinned snapshot
        # so post-enqueue Agent edits cannot change the executed tool set.
        if execution_snapshot is not None:
            from src.services.agent_runtime.execution_snapshot import (
                snapshot_tool_targets,
            )
            from src.services.llm.base import ToolDefinition

            tool_definitions = [
                ToolDefinition(
                    name=tool["name"],
                    description=tool.get("description") or "",
                    parameters=tool.get("parameters")
                    or {"type": "object", "properties": {}},
                )
                for tool in execution_snapshot.get("tools", [])
            ]
            self._tool_workflow_id_map = snapshot_tool_targets(execution_snapshot)
        else:
            async with self._session_factory() as db:
                tool_definitions, self._tool_workflow_id_map = (
                    await resolve_agent_tools(
                        agent,
                        db,
                        caller_user_id=caller_user_id,
                    )
                )
        last_response_content = ""

        async def record_model_event(event: ModelCallEvent) -> None:
            nonlocal step_number, model_name, last_response_content
            if event.type == "request":
                if await self._check_cancelled(run_id):
                    raise AgentRunCancelled("Cancelled by user")
                step_number += 1
                await self._record_step(
                    run_id,
                    step_number,
                    "llm_request",
                    {
                        "messages_count": event.messages_count,
                        "tools_count": event.tools_count,
                        "model": model_name,
                        "context_breakdown": event.context_breakdown,
                    },
                )
                return

            if event.type == "error":
                step_number += 1
                await self._record_step(
                    run_id,
                    step_number,
                    "error",
                    {"error": event.error or "Model request failed", "phase": "llm_call"},
                    duration_ms=event.duration_ms,
                )
                return

            response = event.response
            assert response is not None
            request_usage = response.usage
            if response.model_name:
                model_name = response.model_name
            if response.text:
                last_response_content = response.text
            response_content = {
                "content": (response.text or "")[:20000],
                "tool_calls": [
                    {"name": call.tool_name, "arguments": call.args_as_dict()}
                    for call in response.tool_calls
                ],
                "finish_reason": response.finish_reason,
                "usage": {
                    "input_tokens": request_usage.input_tokens,
                    "output_tokens": request_usage.output_tokens,
                    "cache_read_tokens": request_usage.cache_read_tokens,
                    "cache_write_tokens": request_usage.cache_write_tokens,
                    "provider_cost": (
                        str(cost)
                        if (cost := provider_reported_cost(response)) is not None
                        else None
                    ),
                },
            }
            usage_kwargs = {
                "provider": response.provider_name or llm_config.provider,
                "model": response.model_name or model_name,
                "input_tokens": request_usage.input_tokens,
                "output_tokens": request_usage.output_tokens,
                "cache_read_tokens": request_usage.cache_read_tokens,
                "cache_write_tokens": request_usage.cache_write_tokens,
                "provider_cost": provider_reported_cost(response),
                "duration_ms": event.duration_ms or 0,
            }
            step_number += 1
            if self._durable_lease_token is not None:
                await self._record_step(
                    run_id,
                    step_number,
                    "llm_response",
                    response_content,
                    tokens_used=request_usage.total_tokens,
                    duration_ms=event.duration_ms,
                    buffer=False,
                )
                await self._durable_checkpoint(
                    run_id=run_id,
                    journal_kind="model_response",
                    journal_data={
                        "model": model_name,
                        "finish_reason": response.finish_reason,
                        "tool_calls": [
                            call.tool_name for call in response.tool_calls
                        ],
                    },
                    step={
                        "step_number": step_number,
                        "type": "llm_response",
                        "content": response_content,
                        "tokens_used": request_usage.total_tokens,
                        "duration_ms": event.duration_ms,
                    },
                    iterations_used=usage.requests - usage_start_requests,
                    tokens_used=usage.total_tokens - usage_start_tokens,
                )
                await self._persist_usage_durable(
                    agent=agent,
                    run_id=run_id,
                    **usage_kwargs,
                )
                return
            self._buffer_ai_usage(
                agent=agent,
                run_id=run_id,
                **usage_kwargs,
            )
            await self._record_step(
                run_id,
                step_number,
                "llm_response",
                response_content,
                tokens_used=request_usage.total_tokens,
                duration_ms=event.duration_ms,
            )

        async def execute_tool(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
            if await self._check_cancelled(run_id):
                raise AgentRunCancelled("Cancelled by user during tool execution")
            self._last_workflow_execution_id = None
            self._last_workflow_execution_is_error = False
            if name.startswith("delegate_to_"):
                self._last_delegation_run_id = None
            if self._durable_lease_token is not None:
                return await self._execute_tool_durable(
                    name,
                    arguments,
                    tool_call_id,
                    run_id=run_id,
                    agent=agent,
                )
            return await self._execute_tool(
                ToolCallRequest(id=tool_call_id, name=name, arguments=arguments),
                agent,
            )

        async def record_tool_event(event: ToolEvent) -> None:
            nonlocal step_number
            step_number += 1
            durable = self._durable_lease_token is not None
            if event.type == "tool_call":
                await self._record_step(
                    run_id,
                    step_number,
                    "tool_call",
                    {"tool_name": event.tool_name, "arguments": event.arguments},
                    buffer=not durable,
                )
                if durable:
                    await self._durable_checkpoint(
                        run_id=run_id,
                        journal_kind="tool_call",
                        journal_data={"tool_name": event.tool_name},
                        step={
                            "step_number": step_number,
                            "type": "tool_call",
                            "content": {
                                "tool_name": event.tool_name,
                                "arguments": event.arguments,
                            },
                            "tokens_used": None,
                            "duration_ms": None,
                        },
                        iterations_used=usage.requests - usage_start_requests,
                        tokens_used=usage.total_tokens - usage_start_tokens,
                    )
                return

            content: dict[str, Any] = {
                "tool_name": event.tool_name,
                "is_error": event.type == "tool_error" or self._last_workflow_execution_is_error,
            }
            if event.type == "tool_error":
                content["error"] = event.error
            else:
                content["result"] = str(event.result)[:20000]
            if event.tool_name.startswith("delegate_to_") and self._last_delegation_run_id:
                content["child_run_id"] = self._last_delegation_run_id
            if self._last_workflow_execution_id:
                content["execution_id"] = self._last_workflow_execution_id
            await self._record_step(
                run_id,
                step_number,
                event.type,
                content,
                duration_ms=event.duration_ms,
                buffer=not durable,
            )
            # The invocation result/error was already persisted before the
            # model saw it; this checkpoint projects the step row so restart
            # preserves observable step ordering.
            if durable:
                await self._durable_checkpoint(
                    run_id=run_id,
                    journal_kind=(
                        "tool_error" if event.type == "tool_error" else "tool_result"
                    ),
                    journal_data={"tool_name": event.tool_name},
                    step={
                        "step_number": step_number,
                        "type": event.type,
                        "content": content,
                        "tokens_used": None,
                        "duration_ms": event.duration_ms,
                    },
                    iterations_used=usage.requests - usage_start_requests,
                    tokens_used=usage.total_tokens - usage_start_tokens,
                )

        chain = build_chain_model(
            llm_configs,
            record_model_event,
            retry_surface="autonomous_agent",
            model_override=model_name,
            max_tokens=agent.llm_max_tokens,
            session_id=run_id,
        )
        # llm_config already carries the resolved profile's
        # default_max_tokens; agent.llm_max_tokens wins when set,
        # otherwise the profile default (or provider default) applies.
        # Fallbacks resolve from their own profile defaults.
        toolset = BifrostToolset(
            tool_definitions,
            execute_tool,
            event_handler=record_tool_event,
            toolset_id=f"bifrost-{agent.id}",
        )
        # Blank / repetitive no-tool completions are billed like real output
        # (observed: 131072 output tokens with zero visible content and zero
        # tool calls). Pydantic AI treats them as valid final output, so the
        # breaker below rejects the first one via ModelRetry with a tightened
        # output cap, then forces a durable handoff instead of stalling.
        # Usage for every attempt — including rejected ones — is already
        # charged to the shared UsageLimits ledger by ObservedModel.
        empty_output_guard = EmptyOutputCircuitBreaker()
		runtime = PydanticAgent(
			observed_model,
            # DeferredToolRequests stays a legal output so system tools can
            # suspend (delegation, fan-out, timers) instead of erroring.
			output_type=[DeferredToolRequests, str],
			system_prompt=(
				execution_snapshot["system_prompt"]
                if execution_snapshot is not None
                else build_agent_system_prompt(
					agent,
					execution_context={"mode": "autonomous"},
				)
			),
            toolsets=[toolset] if tool_definitions else [],
            capabilities=[
                *build_runtime_capabilities(budget),
                empty_output_guard,
            ],
			model_settings=agent_model_settings(
                llm_config,
                max_tokens=(
                    snapshot_model["llm_max_tokens"]
                    if snapshot_model
                    else agent.llm_max_tokens
                ),
                session_id=run_id,
                # llm_config already carries the resolved profile's
                # default_max_tokens; agent.llm_max_tokens wins when set,
                # otherwise the profile default (or provider default) applies.
				agent_kind="worker",
			),
            # One bounded correction for malformed tool names/arguments. The
            # shared UsageLimits ledger charges the retry to the parent run.
            retries=1,
            end_strategy="exhaustive",
        )

        user_content = json.dumps(input_data) if input_data else "Run your task."
        if output_schema:
            user_content += f"\n\nRespond with JSON matching this schema:\n{json.dumps(output_schema)}"

        status = "completed"
        final_content = ""
        error: str | None = None
        contract_valid: bool | None = None
        contract_errors: list[str] = []
        try:
            # capture_run_messages feeds the durable checkpoint encoder: at
            # every committed boundary the full history-so-far is available
            # for a restart to resume from.
            with capture_run_messages() as durable_history:
                self._durable_history = durable_history
                if resume_history is not None:
                    resumed_messages = await self._resume_pre_pass(
                        resume_history,
                        run_id=run_id,
                        agent=agent,
                        deferred_tool_call_ids=set(deferred_results or {}),
                    )
                    result = await runtime.run(
                        None,
                        message_history=resumed_messages,
                        deferred_tool_results=(
                            DeferredToolResults(calls=dict(deferred_results))
                            if deferred_results
                            else None
                        ),
                        usage_limits=budget.usage_limits(),
                        usage=usage,
                        conversation_id=run_id,
                    )
                else:
                    result = await runtime.run(
                        user_content,
                        usage_limits=budget.usage_limits(),
                        usage=usage,
                        conversation_id=run_id,
                    )
                if isinstance(result.output, DeferredToolRequests):
                    suspension_or_text = await self._handle_deferred_requests(
                        result.output,
                        runtime=runtime,
                        agent=agent,
                        run_id=run_id,
                        caller_context=caller_context,
                        correlation=correlation,
                        usage=usage,
                        usage_start_requests=usage_start_requests,
                        usage_start_tokens=usage_start_tokens,
                    )
                    # Durable park: the parent is waiting; release the worker.
                    if isinstance(suspension_or_text, dict):
                        return suspension_or_text
                    # Legacy inline fallback: the loop already continued.
                    final_content = suspension_or_text
                else:
                    final_content = result.output
                if status == "completed" and output_schema and final_content:
                    (
                        final_content,
                        contract_valid,
                        contract_errors,
                        status,
                    ) = await self._enforce_output_contract(
                        final_content,
                        output_schema,
                        runtime=runtime,
                        usage=usage,
                        budget=budget,
                        usage_start_requests=usage_start_requests,
                        usage_start_tokens=usage_start_tokens,
                        run_id=run_id,
                    )
            if empty_output_guard.handoff_triggered:
                step_number += 1
                await self._record_step(
                    run_id,
                    step_number,
                    "budget_warning",
                    {
                        "tokens_used": usage.total_tokens - usage_start_tokens,
                        "max_tokens": max_tokens,
                        "iterations_used": usage.requests - usage_start_requests,
                        "max_iterations": max_iterations,
                        "reason": empty_output_guard.handoff_reason,
                        "fallbacks_used": empty_output_guard.fallbacks_used,
                    },
                )
        except AgentRunCancelled as exc:
            status = "cancelled"
            error = str(exc)
            step_number += 1
            await self._record_step(
                run_id,
                step_number,
                "cancelled",
                {"reason": error, "iterations_used": usage.requests},
            )
        except UsageLimitExceeded as exc:
            status = "budget_exceeded"
            error = str(exc)
            # A single blank completion can already exceed the token budget
            # (observed: 131072 output tokens, zero visible content), in which
            # case the guard's ModelRetry never gets a second request — the
            # pre-request ledger guard rejects it first. That is still a
            # durable handoff, but the message names the blank response so
            # ownership does not stall on a generic budget note.
            if not last_response_content and empty_output_guard.saw_empty_response:
                final_content = (
                    "I stopped here because the model returned an empty "
                    "response without calling a tool, and the billed output "
                    "exhausted this run's budget before a retry was possible. "
                    "Completed tool results and run steps are preserved in "
                    "this run. A human should review the goal and either "
                    "retry with narrower instructions or continue manually."
                )
            else:
                final_content = last_response_content or (
                    "I reached this run's limit before I could finish. Completed "
                    "tool results and run steps were preserved so the work can "
                    "resume without starting over."
                )
            step_number += 1
            await self._record_step(
                run_id,
                step_number,
                "budget_warning",
                {
                    "tokens_used": usage.total_tokens,
                    "max_tokens": max_tokens,
                    "iterations_used": usage.requests - usage_start_requests,
                    "max_iterations": max_iterations,
                    "reason": "runtime_budget_exceeded",
                    "empty_response_seen": empty_output_guard.saw_empty_response,
                },
            )
        except Exception as exc:
            logger.error("Agent runtime failed in run %s: %s", run_id, exc, exc_info=True)
            status = "failed"
            error = str(exc)

        output: str | dict = final_content
        if contract_valid is None and output_schema and final_content:
            # No contract enforcement ran (non-completed path or empty
            # output): preserve the legacy best-effort JSON coercion.
            try:
                output = json.loads(final_content)
            except json.JSONDecodeError as exc:
                logger.debug("final agent output is not JSON, returning raw string: %s", exc)

        response = {
            "output": output or None,
            "iterations_used": usage.requests - usage_start_requests,
            "tokens_used": usage.total_tokens - usage_start_tokens,
            "status": status,
            "llm_model": model_name,
            "contract_valid": contract_valid,
            "contract_errors": contract_errors,
        }
        if error and status == "failed":
            response["error"] = error
		if chain.failover is not None and (path := chain.failover.fallback_path()):
			response["failover_path"] = path
		if status == "contract_failed":
			response["error"] = (
				"Output did not satisfy the caller's output contract: "
				+ "; ".join(contract_errors[:5])
			)
        return response

    async def _enforce_output_contract(
        self,
        final_text: str,
        output_schema: dict[str, Any],
        *,
        runtime: PydanticAgent,
        usage: RunUsage,
        budget: AgentRunBudget,
        usage_start_requests: int,
        usage_start_tokens: int,
        run_id: str,
    ) -> tuple[Any, bool, list[str], str]:
        """Validate final output; one bounded correction turn when affordable.

        Returns ``(output, valid, errors, status)``. Invalid output is
        preserved and the run becomes ``contract_failed`` unless a single
        correction turn fits the remaining iteration/token budget.
        """
        from src.services.agent_runtime.output_contract import (
            contract_correction_prompt,
            correction_allowed,
            parse_final_output,
            validate_output,
            validate_output_schema,
        )

        validate_output_schema(output_schema)
        parsed, was_json = parse_final_output(final_text)
        errors = (
            validate_output(output_schema, parsed)
            if was_json
            else ["output is not valid JSON"]
        )
        if not errors:
            return parsed, True, [], "completed"
        if not correction_allowed(
            iterations_used=usage.requests - usage_start_requests,
            max_iterations=budget.max_requests,
            tokens_used=usage.total_tokens - usage_start_tokens,
            max_tokens=budget.max_total_tokens,
        ):
            return {"text": final_text}, False, errors, "contract_failed"

        with capture_run_messages() as correction_history:
            correction_result = await runtime.run(
                contract_correction_prompt(errors),
                message_history=list(self._durable_history or []),
                usage_limits=budget.usage_limits(),
                usage=usage,
                conversation_id=run_id,
            )
        if self._durable_history is not None:
            self._durable_history.extend(correction_history)
        corrected_text = correction_result.output or ""
        reparsed, was_json = parse_final_output(corrected_text)
        new_errors = (
            validate_output(output_schema, reparsed)
            if was_json
            else ["output is not valid JSON"]
        )
        await self._durable_checkpoint(
            run_id=run_id,
            journal_kind="validation",
            journal_data={
                "valid": not new_errors,
                "errors": new_errors[:10],
                "corrected": True,
            },
            step=None,
            iterations_used=usage.requests - usage_start_requests,
            tokens_used=usage.total_tokens - usage_start_tokens,
        )
        if not new_errors:
            return reparsed, True, [], "completed"
        return {"text": corrected_text}, False, new_errors, "contract_failed"

    # ------------------------------------------------------------------
    # DB flush (called by consumer after run completes)
    # ------------------------------------------------------------------

    async def flush_to_db(self, session: AsyncSession) -> None:
        """Flush all buffered steps and AI usage to Postgres in a single transaction.

        Called by the consumer after the run completes (success, failure, timeout, etc.).
        This is the only point where the executor writes to the database.
        """
        # Flush steps from this executor
        if self._pending_steps:
            for step_data in self._pending_steps:
                step = AgentRunStep(
                    id=UUID(step_data["id"]),
                    run_id=UUID(step_data["run_id"]),
                    step_number=step_data["step_number"],
                    type=step_data["type"],
                    content=step_data.get("content"),
                    tokens_used=step_data.get("tokens_used"),
                    duration_ms=step_data.get("duration_ms"),
                )
                session.add(step)

        # Flush AI usage from this executor
        if self._pending_ai_usage and self.redis_client:
            from src.services.ai_usage_service import record_ai_usage

            for usage in self._pending_ai_usage:
                try:
                    await record_ai_usage(
                        session=session,
                        redis_client=self.redis_client,
                        **usage,
                    )
                except Exception as e:
                    logger.warning(f"Failed to flush AI usage record: {e}")

    async def _durable_checkpoint(
        self,
        *,
        run_id: str,
        journal_kind: str,
        journal_data: dict[str, Any] | None,
        step: dict[str, Any] | None,
        iterations_used: int,
        tokens_used: int,
    ) -> None:
        """Commit one model/tool boundary: checkpoint + journal + step row.

        No-op unless the run holds a lease token. Failures propagate — a
        boundary that cannot be committed must fail the attempt loudly
        rather than continue on uncommitted state.
        """
        token = self._durable_lease_token
        history = self._durable_history
        if token is None or history is None:
            return
        from src.services.agent_runtime import run_store
        from src.services.agent_runtime.checkpoint_codec import (
            CHECKPOINT_MESSAGE_FORMAT_VERSION,
            encode_messages,
        )

        async with self._session_factory() as db:
            checkpoint = await run_store.commit_checkpoint(
                db,
                UUID(run_id),
                token,
                encode_messages(history),
                format_version=CHECKPOINT_MESSAGE_FORMAT_VERSION,
                journal_kind=journal_kind,
                journal_data=journal_data,
                steps=[step] if step is not None else None,
                iterations_used=iterations_used,
                tokens_used=tokens_used,
            )
        self._last_checkpoint_sequence = checkpoint.sequence

    async def _persist_usage_durable(
        self,
        *,
        agent: Agent,
        run_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        provider_cost: Decimal | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Write one model-boundary usage row, idempotent on retry.

        The usage sequence keys off the latest committed checkpoint, so a
        restarted worker re-committing the same boundary cannot double-count
        committed usage.
        """
        sequence = getattr(self, "_last_checkpoint_sequence", None)
        if sequence is None:
            return
        from src.models.orm.ai_usage import AIUsage

        async with self._session_factory() as db:
            existing = (
                await db.execute(
                    select(AIUsage.id).where(
                        AIUsage.agent_run_id == UUID(run_id),
                        AIUsage.sequence == sequence,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            if not self.redis_client:
                return
            from src.services.ai_usage_service import record_ai_usage

            await record_ai_usage(
                session=db,
                redis_client=self.redis_client,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                provider_cost=provider_cost,
                sequence=sequence,
                duration_ms=duration_ms,
                agent_run_id=UUID(run_id),
                organization_id=agent.organization_id,
            )
            await db.commit()

    async def _execute_tool_durable(
        self,
        name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
        *,
        run_id: str,
        agent: Agent,
    ) -> Any:
        """Execute one tool call with planned/running/completed durability.

        A committed result is replayed verbatim and never executed twice.
        """
        from src.services.agent_runtime.tool_invocations import (
            complete_invocation,
            delete_invocation,
            durable_operation_id,
            fail_invocation,
            get_invocation,
            mark_running,
            plan_invocation,
        )

        token = self._durable_lease_token
        assert token is not None
        operation_id = durable_operation_id(run_id, tool_call_id)
        self._current_operation_id = operation_id
        try:
            async with self._session_factory() as db:
                existing = await get_invocation(db, UUID(run_id), tool_call_id)
                if existing is not None and existing.state == "completed":
                    stored = existing.result or {}
                    return stored.get("text", "")
                await plan_invocation(
                    db,
                    run_id=UUID(run_id),
                    lease_token=token,
                    provider_tool_call_id=tool_call_id,
                    tool_name=name,
                    arguments=arguments,
                )
                await mark_running(
                    db,
                    run_id=UUID(run_id),
                    lease_token=token,
                    provider_tool_call_id=tool_call_id,
                )
            try:
                result = await self._execute_tool(
                    ToolCallRequest(id=tool_call_id, name=name, arguments=arguments),
                    agent,
                )
            except CallDeferred:
                # Suspension, not failure: drop the provisional invocation
                # record (the deferral is journaled at suspend time) and let
                # the model loop return DeferredToolRequests.
                async with self._session_factory() as db:
                    try:
                        await delete_invocation(db, UUID(run_id), tool_call_id)
                        await db.commit()
                    except Exception:
                        logger.debug(
                            "Failed to drop provisional invocation for %s",
                            tool_call_id,
                            exc_info=True,
                        )
                raise
            except AgentRunCancelled as exc:
                async with self._session_factory() as db:
                    await fail_invocation(
                        db,
                        run_id=UUID(run_id),
                        lease_token=token,
                        provider_tool_call_id=tool_call_id,
                        error=f"cancelled: {exc}",
                    )
                raise
            except Exception as exc:
                async with self._session_factory() as db:
                    await fail_invocation(
                        db,
                        run_id=UUID(run_id),
                        lease_token=token,
                        provider_tool_call_id=tool_call_id,
                        error=str(exc),
                    )
                raise
            async with self._session_factory() as db:
                await complete_invocation(
                    db,
                    run_id=UUID(run_id),
                    lease_token=token,
                    provider_tool_call_id=tool_call_id,
                    result=result,
                )
            return result
        finally:
            self._current_operation_id = None

    async def _handle_deferred_requests(
        self,
        deferred: DeferredToolRequests,
        *,
        runtime: PydanticAgent,
        agent: Agent,
        run_id: str,
        caller_context: dict[str, Any] | None,
        correlation: dict[str, Any] | None,
        usage: RunUsage,
        usage_start_requests: int,
        usage_start_tokens: int,
    ) -> dict | str:
        """Resolve a suspension: durable park or legacy inline fallback.

        Returns a response dict for a durable park (the caller returns it
        to release the worker), or the continued final text for the legacy
        inline fallback so the same frame proceeds to contract enforcement.
        """
        if self._durable_lease_token is None:
            return await self._resolve_deferred_inline(
                deferred,
                runtime=runtime,
                agent=agent,
                run_id=run_id,
                usage=usage,
            )
        from src.services.agent_runtime.delegation import (
            suspend_for_deferred_calls,
            suspend_for_fanout,
        )
        from src.services.agent_runtime.timers import suspend_for_timer

        all_calls = (*deferred.calls, *deferred.approvals)
        singles = [
            call for call in all_calls if call.tool_name.startswith("delegate_to_")
        ]
        fanouts = [call for call in all_calls if call.tool_name == "delegate_agents"]
        timers = [call for call in all_calls if call.tool_name == "sleep_until"]
        if len(singles) + len(fanouts) + len(timers) != len(all_calls):
            unknown = next(
                call.tool_name
                for call in all_calls
                if not call.tool_name.startswith("delegate_to_")
                and call.tool_name not in ("delegate_agents", "sleep_until")
            )
            raise ToolError(
                f"Deferred tool '{unknown}' has no durable "
                "suspension handler in this runtime version."
            )
        if deferred.approvals:
            raise ToolError("Deferred approvals are not supported.")
        # One suspension parks a run per model turn. Sequential suspensions
        # across turns compose naturally (suspend, wake, resume, suspend).
        if len(all_calls) > 1:
            raise ToolError(
                "Only one delegation, fan-out, or timer may suspend a run "
                "per model turn; issue them on separate turns."
            )
        if timers:
            call = timers[0]
            timer_outcome = await suspend_for_timer(
                session_factory=self._session_factory,
                run_id=UUID(run_id),
                lease_token=self._durable_lease_token,
                tool_call_id=call.tool_call_id,
                arguments=(
                    call.args_as_dict()
                    if hasattr(call, "args_as_dict")
                    else dict(call.args or {})
                ),
            )
            return {
                "output": None,
                "iterations_used": usage.requests - usage_start_requests,
                "tokens_used": usage.total_tokens - usage_start_tokens,
                "status": "suspended",
                "llm_model": None,
                "contract_valid": None,
                "contract_errors": [],
                "suspended": timer_outcome,
            }
        if fanouts:
            call = fanouts[0]
            outcome = await suspend_for_fanout(
                session_factory=self._session_factory,
                parent_run_id=UUID(run_id),
                lease_token=self._durable_lease_token,
                agent=agent,
                execution_snapshot=getattr(self, "_snapshot_for_deferral", None),
                tool_call_id=call.tool_call_id,
                arguments=(
                    call.args_as_dict()
                    if hasattr(call, "args_as_dict")
                    else dict(call.args or {})
                ),
                caller=self._caller,
                caller_context=caller_context,
                correlation=correlation,
            )
            return {
                "output": None,
                "iterations_used": usage.requests - usage_start_requests,
                "tokens_used": usage.total_tokens - usage_start_tokens,
                "status": "suspended",
                "llm_model": None,
                "contract_valid": None,
                "contract_errors": [],
                "suspended": outcome,
            }
        outcome = await suspend_for_deferred_calls(
            session_factory=self._session_factory,
            parent_run_id=UUID(run_id),
            lease_token=self._durable_lease_token,
            agent=agent,
            execution_snapshot=getattr(self, "_snapshot_for_deferral", None),
            tool_calls=list(deferred.calls),
            caller=self._caller,
            caller_context=caller_context,
            correlation=correlation,
        )
        return {
            "output": None,
            "iterations_used": usage.requests - usage_start_requests,
            "tokens_used": usage.total_tokens - usage_start_tokens,
            "status": "suspended",
            "llm_model": None,
            "contract_valid": None,
            "contract_errors": [],
            "suspended": outcome,
        }

    async def _resolve_deferred_inline(
        self,
        deferred: DeferredToolRequests,
        *,
        runtime: PydanticAgent,
        agent: Agent,
        run_id: str,
        usage: RunUsage,
    ) -> str:
        """Legacy fallback: run deferred delegations inline and continue."""
        from src.services.llm.base import ToolCallRequest as LlmToolCallRequest

        results: dict[str, Any] = {}
        for call in deferred.calls:
            args = (
                call.args_as_dict()
                if hasattr(call, "args_as_dict")
                else dict(call.args or {})
            )
            if call.tool_name == "sleep_until":
                results[call.tool_call_id] = await self._execute_sleep(
                    LlmToolCallRequest(
                        id=call.tool_call_id,
                        name=call.tool_name,
                        arguments=args,
                    ),
                    agent,
                )
                continue
            if call.tool_name == "delegate_agents":
                results[call.tool_call_id] = await self._execute_fanout(
                    LlmToolCallRequest(
                        id=call.tool_call_id,
                        name=call.tool_name,
                        arguments=args,
                    ),
                    agent,
                )
                continue
            if not call.tool_name.startswith("delegate_to_"):
                raise ToolError(
                    f"Deferred tool '{call.tool_name}' cannot run inline."
                )
            outcome = await self.run_delegation(
                parent_agent=agent,
                tool_call=LlmToolCallRequest(
                    id=call.tool_call_id,
                    name=call.tool_name,
                    arguments=args,
                ),
                parent_run_id=self._current_run_id or run_id,
                caller=self._caller,
                _shared_usage=usage,
                _shared_budget=self._active_budget,
            )
            if not outcome.succeeded:
                raise ToolError(
                    outcome.error or f"Delegation ended with {outcome.status}"
                )
            results[call.tool_call_id] = (
                json.dumps(outcome.output, default=str)
                if isinstance(outcome.output, dict)
                else str(outcome.output or "Delegation completed.")
            )
        resumed = await runtime.run(
            None,
            message_history=list(self._durable_history or []),
            deferred_tool_results=DeferredToolResults(calls=results),
            usage_limits=(
                self._active_budget.usage_limits()
                if self._active_budget
                else None
            ),
            usage=usage,
            conversation_id=run_id,
        )
        if isinstance(resumed.output, DeferredToolRequests):
            raise ToolError("Inline delegation did not resolve; still deferred.")
        return resumed.output

    async def _resume_pre_pass(
        self,
        history: list,
        *,
        run_id: str,
        agent: Agent,
        deferred_tool_call_ids: set[str] | None = None,
    ) -> list:
        """Execute planned-but-unanswered calls before the model continues.

        Committed results were already replayed into history by the resume
        planner; this pass only dispatches calls with no stored outcome and
        appends their results (or error envelopes) as one request. Failed
        calls surface their recorded error; uncertain calls fail the attempt
        loudly instead of guessing. Suspended delegation calls are skipped:
        their results arrive through DeferredToolResults.
        """
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            ToolCallPart,
            ToolReturnPart,
        )
        from src.services.agent_runtime.tool_invocations import get_invocation

        answered: set[str] = set(deferred_tool_call_ids or set())
        ordered_calls: list = []
        for message in history:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, ToolReturnPart):
                        answered.add(part.tool_call_id)
            elif isinstance(message, ModelResponse):
                for part in message.parts:
                    if isinstance(part, ToolCallPart):
                        ordered_calls.append(part)
        pending = [call for call in ordered_calls if call.tool_call_id not in answered]
        if not pending:
            return history
        if self._durable_lease_token is None:
            raise ToolError("Resume requires a worker lease token")

        returns: list[ToolReturnPart] = []
        async with self._session_factory() as db:
            states = {
                call.tool_call_id: await get_invocation(
                    db, UUID(run_id), call.tool_call_id
                )
                for call in pending
            }
        for call in pending:
            invocation = states[call.tool_call_id]
            if invocation is not None and invocation.state == "failed":
                content: Any = f"Error: {invocation.error or 'tool failed'}"
            elif invocation is not None and invocation.state == "uncertain":
                raise ToolError(
                    f"Tool call {call.tool_call_id} is uncertain and requires "
                    "reconciliation before resume"
                )
            else:
                try:
                    content = await self._execute_tool_durable(
                        call.tool_name,
                        call.args_as_dict(),
                        call.tool_call_id,
                        run_id=run_id,
                        agent=agent,
                    )
                except AgentRunCancelled:
                    raise
                except Exception as exc:
                    content = f"Error: {exc}"
            returns.append(
                ToolReturnPart(
                    tool_name=call.tool_name,
                    content=content,
                    tool_call_id=call.tool_call_id,
                )
            )
        return [*history, ModelRequest(parts=returns)]

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _execution_org_id(self, agent: Agent) -> UUID | None:
        """Use authenticated caller scope, or agent scope for autonomous runs."""
        if self._caller_user_id is not None:
            raw_org_id = self._caller.get("organization_id") if self._caller else None
            if raw_org_id is None:
                return None
            return UUID(str(raw_org_id))
        return agent.organization_id

    async def _execute_tool(self, tool_call: ToolCallRequest, agent: Agent) -> str:
        """Execute a tool call, mirroring AgentExecutor's dispatch logic."""
        # Knowledge search
        if tool_call.name == "search_knowledge" and agent.knowledge_sources:
            return await self._execute_knowledge_search(tool_call, agent)

        # Delegation
        if tool_call.name.startswith("delegate_to_"):
            return await self._execute_delegation(tool_call, agent)

        # Fan-out across delegated agents (durable all-join)
        if tool_call.name == "delegate_agents":
            return await self._execute_fanout(tool_call, agent)

        # Durable timer (model-requested wake-up in the same run)
        if tool_call.name == "sleep_until":
            return await self._execute_sleep(tool_call, agent)

        # System tools
        if tool_call.name in (agent.system_tools or []):
            return await self._execute_system_tool(tool_call, agent)

        # External MCP tools — namespaced ``mcp__<connection_id>__<tool>``.
        # Routed BEFORE workflow tools because the workflow id_map maps
        # MCP qualified names to ``MCPConnection.id`` and dispatch needs
        # to go through ``mcp_dispatch.invoke`` rather than the workflow
        # execution service. The threaded ``self._caller_user_id`` is
        # what differentiates a chat-claim webhook (per-user OAuth) from
        # a fully autonomous run (service token only).
        mcp_route = parse_mcp_tool_name(tool_call.name)
        if mcp_route is not None:
            connection_id, remote_tool_name = mcp_route
            return await self._execute_mcp_tool(
                tool_call,
                connection_id=connection_id,
                remote_tool_name=remote_tool_name,
            )

        # Workflow tools
        workflow_id = self._tool_workflow_id_map.get(tool_call.name)
        if not workflow_id:
            raise ToolError(f"Unknown tool: {tool_call.name}")

        response = await execute_agent_workflow_tool(
            workflow_id=workflow_id,
            workflow_name=tool_call.name,
            parameters=tool_call.arguments or {},
            caller=AgentWorkflowCaller(
                user_id=(
                    str(self._caller_user_id)
                    if self._caller_user_id
                    else SYSTEM_USER_ID
                ),                email=(
                    str(self._caller.get("email"))
                    if self._caller_user_id
                    and self._caller
                    and self._caller.get("email")
                    else SYSTEM_USER_EMAIL
                ),
                name=(
                    str(self._caller.get("name"))
                    if self._caller_user_id
                    and self._caller
                    and self._caller.get("name")
                    else agent.name
                ),
                organization_id=self._execution_org_id(agent),
                is_platform_admin=(
                    bool(self._caller.get("is_platform_admin", False))
                    if self._caller_user_id and self._caller
                    else False
                ),
                operation_id=self._current_operation_id,
            ),
            artifact_workspace_id=(
                self._ancestor_run_ids[0]
                if self._ancestor_run_ids
                else self._current_run_id or None
            ),
        )
        self._last_workflow_execution_id = response.execution_id
        self._last_workflow_execution_is_error = response.status.value != "Success"

        if self._last_workflow_execution_is_error:
            error_msg = response.error or f"Tool execution failed with status: {response.status.value}"
            return f"Error: {error_msg}"

        if not response.result:
            return "Tool executed successfully"
        if isinstance(response.result, (dict, list)):
            return json.dumps(response.result, default=str)
        return str(response.result)

    async def _execute_mcp_tool(
        self,
        tool_call: ToolCallRequest,
        *,
        connection_id: UUID,
        remote_tool_name: str,
    ) -> str:
        """Dispatch an external MCP tool call from an autonomous run.

        Mirrors ``AgentExecutor._execute_mcp_tool`` but returns the
        plain-string envelope the autonomous loop expects rather than
        the chat-surface ``ToolResult``.

        For autonomous runs ``self._caller_user_id`` is typically
        ``None`` — auth resolution then routes to the connection's
        service token, gated by ``available_to_autonomous``. Webhook
        deliveries that pass a user_id (signed claim) get user-token
        resolution.

        ``NeedsReauthError`` and ``MisconfigError`` cannot be remediated
        by an autonomous run, so they're raised as ``ToolError`` and
        recorded in the run's step log. The user can connect the
        missing credential via the chat surface, then retry.
        """
        from src.models.orm.external_mcp import MCPConnection, MCPServer

        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(MCPConnection)
                    .where(MCPConnection.id == connection_id)
                    .options(
                        selectinload(MCPConnection.server).selectinload(
                            MCPServer.oauth_provider
                        ),
                        selectinload(MCPConnection.service_oauth_token),
                    )
                )
                connection = result.scalar_one_or_none()
                if connection is None:
                    raise ToolError(
                        f"MCP connection {connection_id} not found"
                    )

                envelope = await mcp_dispatch.invoke(
                    connection=connection,
                    tool_name=remote_tool_name,
                    arguments=tool_call.arguments or {},
                    caller_user_id=self._caller_user_id,
                    db=db,
                )
        except NeedsReauthError as exc:
            raise ToolError(
                f"MCP tool {remote_tool_name!r} on connection "
                f"{connection_id} needs reauth: {exc}"
            ) from exc
        except MisconfigError as exc:
            raise ToolError(
                f"MCP tool {remote_tool_name!r} on connection "
                f"{connection_id} misconfigured: {exc}"
            ) from exc
        except ToolDispatchError as exc:
            raise ToolError(
                f"MCP dispatch error on connection {connection_id} "
                f"tool {remote_tool_name!r}: {exc}"
            ) from exc

        return json.dumps(envelope, default=str)

    async def _execute_knowledge_search(self, tool_call: ToolCallRequest, agent: Agent) -> str:
        """Execute knowledge search using the agent's configured namespaces.

        Compact default: ranked id + title + confidence + bounded excerpt.
        Full document content requires an explicit follow-up
        (``doc_id`` + ``include_full_content=true``) served from the run
        cache, so the 40K envelope stays a ceiling rather than the norm.
        """
        try:
            from src.repositories.knowledge import KnowledgeRepository
            from src.services.embeddings import get_embedding_client

            arguments = tool_call.arguments or {}
            query = arguments.get("query", "")
            limit = clamp_knowledge_result_limit(
                arguments.get("limit", 5)
            )
            doc_id, include_full = parse_knowledge_followup(arguments)

            namespaces = agent.knowledge_sources
            if not namespaces:
                return "No knowledge sources configured for this agent"

            if doc_id:
                cached = self._knowledge_search_budget.cached_document(doc_id)
                if cached is None:
                    return json.dumps({
                        "documents": [],
                        "count": 0,
                        "from_cache": False,
                        "message": (
                            f"Unknown doc_id '{doc_id}'. Search first, then "
                            "follow up with a returned document id."
                        ),
                    })
                if not include_full:
                    return json.dumps({
                        "documents": [build_compact_knowledge_document(
                            doc_id,
                            content=cached["content"],
                            namespace=cached["namespace"],
                            score=cached["score"],
                            key=cached["key"],
                            metadata=cached["metadata"],
                        )],
                        "count": 1,
                        "from_cache": True,
                        "hint": KNOWLEDGE_FULL_CONTENT_HINT,
                    })
                full_document = build_compact_knowledge_document(
                    doc_id,
                    content=cached["content"],
                    namespace=cached["namespace"],
                    score=cached["score"],
                    key=cached["key"],
                    metadata=cached["metadata"],
                    include_full=True,
                )
                claim = self._knowledge_search_budget.claim_evidence(
                    f"{doc_id}#full",
                    len(json.dumps(full_document, default=str, ensure_ascii=False)),
                )
                if claim != "accepted":
                    return json.dumps({
                        "documents": [],
                        "count": 0,
                        "from_cache": True,
                        "omitted_for_evidence_budget": 1 if claim == "evidence_budget_exhausted" else 0,
                        "message": (
                            "Full document does not fit the remaining "
                            "evidence envelope. Synthesize from the excerpt "
                            "already returned."
                        ),
                        "evidence_chars_remaining": (
                            self._knowledge_search_budget.evidence_chars_remaining
                        ),
                    })
                return json.dumps({
                    "documents": [full_document],
                    "count": 1,
                    "from_cache": True,
                    "evidence_chars_used": self._knowledge_search_budget.evidence_chars_used,
                    "evidence_chars_remaining": (
                        self._knowledge_search_budget.evidence_chars_remaining
                    ),
                })

            if not query:
                return "No query provided for knowledge search"

            decision = self._knowledge_search_budget.reserve(query)
            if not decision.allowed:
                return json.dumps(knowledge_search_rejection_payload(decision))

            # Brief DB session for embedding client config + knowledge search
            async with self._session_factory() as db:
                embedding_client = await get_embedding_client(db)
                query_embedding = await embedding_client.embed_single(query)

                repo = KnowledgeRepository(
                    db, org_id=agent.organization_id, is_superuser=True
                )
                results = await repo.search(
                    query_embedding=query_embedding,
                    namespace=namespaces,
                    query_text=query,
                    limit=limit,
                    fallback=True,
                )

            if not results:
                return "No relevant knowledge found."

            # Compact default: cache the full payload, send only the excerpt.
            compacted: list[tuple[str, dict[str, Any]]] = []
            for doc in results:
                score = round(doc.score, 4) if doc.score is not None else None
                metadata = compact_knowledge_metadata(doc.metadata)
                self._knowledge_search_budget.cache_document(doc.id, {
                    "content": doc.content,
                    "namespace": doc.namespace,
                    "score": score,
                    "key": doc.key,
                    "metadata": metadata,
                })
                compacted.append((
                    doc.id,
                    build_compact_knowledge_document(
                        doc.id,
                        content=doc.content,
                        namespace=doc.namespace,
                        score=score,
                        key=doc.key,
                        metadata=metadata,
                        include_full=include_full,
                    ),
                ))
            # Format results
            evidence = select_novel_knowledge_evidence(
                self._knowledge_search_budget,
                compacted,
            )
            return json.dumps({
                "documents": evidence.documents,
                "count": len(evidence.documents),
                "compact": True,
                "hint": KNOWLEDGE_FULL_CONTENT_HINT,
                "from_cache": False,
                "omitted_duplicate_evidence": evidence.omitted_duplicates,
                "omitted_for_evidence_budget": evidence.omitted_for_budget,
                "searches_used": decision.searches_used,
                "searches_remaining": decision.searches_remaining,
                "evidence_chars_used": evidence.evidence_chars_used,
                "evidence_chars_remaining": evidence.evidence_chars_remaining,
            })

        except Exception as e:
            logger.error(f"Knowledge search failed: {e}", exc_info=True)
            raise ToolError(f"Knowledge search error: {e}") from e

    async def run_delegation(
        self,
        *,
        parent_agent: Agent,
        tool_call: ToolCallRequest,
        parent_run_id: str | None = None,
        conversation_id: UUID | None = None,
        caller: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        _shared_usage: RunUsage | None = None,
        _shared_budget: AgentRunBudget | None = None,
    ) -> DelegationOutcome:
        """Run one delegated child with a durable, caller-neutral lifecycle.

        ``output_schema`` is the parent-selected contract for this child
        invocation (Task 8 wires parent choice through the deferred tool;
        until then callers may pass it explicitly). The child enforces the
        same contract path as a direct run.
        """
        if parent_run_id and await self._check_cancelled(parent_run_id):
            raise ToolError("Agent run was cancelled")
        if self._delegation_depth >= MAX_DELEGATION_DEPTH:
            logger.warning(
                f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) exceeded for {tool_call.name}"
            )
            raise ToolError(
                f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) exceeded — "
                "cannot delegate further."
            )

        task = tool_call.arguments.get("task", "")
        if not task:
            raise ToolError("No task provided for delegation")

        target_agent = find_delegated_agent(parent_agent, tool_call.name)
        if not target_agent:
            raise ToolError(f"Delegation target for '{tool_call.name}' not found.")
        if (
            target_agent.organization_id is not None
            and target_agent.organization_id != parent_agent.organization_id
        ):
            raise ToolError(
                f"Delegation target '{target_agent.name}' is outside the "
                "parent agent's organization."
            )

        logger.info(
            f"Agent '{parent_agent.name}' delegating to '{target_agent.name}' "
            f"(depth={self._delegation_depth + 1}/{MAX_DELEGATION_DEPTH})"
        )

        sub_run_id = uuid4()
        delegation_org_id = parent_agent.organization_id
        if (
            delegation_org_id is None
            and caller
            and caller.get("organization_id")
        ):
            try:
                delegation_org_id = UUID(str(caller["organization_id"]))
            except (TypeError, ValueError):
                logger.warning(
                    "Delegation from agent %s received invalid caller "
                    "organization_id %r; retaining global run scope",
                    parent_agent.id,
                    caller.get("organization_id"),
                )

        async with self._session_factory() as db:
            result = await db.execute(
                select(Agent)
                .join(
                    AgentDelegation,
                    AgentDelegation.child_agent_id == Agent.id,
                )
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                )
                .where(
                    Agent.id == target_agent.id,
                    Agent.is_active.is_(True),
                    AgentDelegation.parent_agent_id == parent_agent.id,
                    or_(
                        Agent.organization_id.is_(None),
                        Agent.organization_id == parent_agent.organization_id,
                    ),
                )
                .with_for_update()
            )
            target_agent = result.scalar_one_or_none()
            if target_agent is None:
                raise ToolError(
                    f"Delegation target for '{tool_call.name}' is no longer "
                    "active, authorized, or in scope."
                )

            sub_run = AgentRun(
                id=sub_run_id,
                agent_id=target_agent.id,
                trigger_type="delegation",
                trigger_source=(
                    f"conversation:{conversation_id}"
                    if conversation_id
                    else f"agent:{parent_agent.name}"
                ),
                conversation_id=conversation_id,
                input={"task": task, "_delegated_from": parent_agent.name},
                output_schema=output_schema,
                status="running",
                org_id=delegation_org_id,
                caller_user_id=caller.get("user_id") if caller else None,
                caller_email=caller.get("email") if caller else None,
                caller_name=caller.get("name") if caller else None,
                parent_run_id=UUID(parent_run_id) if parent_run_id else None,
                budget_max_iterations=target_agent.max_iterations,
                budget_max_tokens=target_agent.max_token_budget,
                started_at=datetime.now(timezone.utc),
            )
            db.add(sub_run)
            await db.commit()

        self._last_delegation_run_id = str(sub_run_id)

        ancestor_run_ids = self._ancestor_run_ids
        if parent_run_id and parent_run_id not in ancestor_run_ids:
            ancestor_run_ids = (*ancestor_run_ids, parent_run_id)
        sub_executor = AutonomousAgentExecutor(
            self._session_factory,
            redis_client=self.redis_client,
            _delegation_depth=self._delegation_depth + 1,
            _ancestor_run_ids=ancestor_run_ids,
        )

        sub_start = time.time()
        cancellation: asyncio.CancelledError | None = None
        shared_usage = _shared_usage or self._active_usage
        shared_budget = _shared_budget or self._active_budget
        target_profile_id = (
            str(target_agent.llm_profile_id) if target_agent.llm_profile_id else None
        )
        try:
            sub_result = await asyncio.wait_for(
                sub_executor.run(
                    agent=target_agent,
                    input_data={
                        "task": task,
                        "_delegated_from": parent_agent.name,
                    },
                    output_schema=output_schema,
                    run_id=str(sub_run_id),
                    _caller=caller,
                    _shared_usage=shared_usage,
                    _shared_budget=shared_budget,
                ),
                timeout=DELEGATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Delegation to '{target_agent.name}' timed out after "
                f"{DELEGATION_TIMEOUT_SECONDS}s"
            )
            sub_result = {
                "output": None,
                "iterations_used": 0,
                "tokens_used": 0,
                "status": "timeout",
                "llm_profile_id": target_profile_id,
                "error": (
                    f"Delegation to {target_agent.name} timed out after "
                    f"{DELEGATION_TIMEOUT_SECONDS}s"
                ),
            }
        except asyncio.CancelledError as exc:
            cancellation = exc
            sub_result = {
                "output": None,
                "iterations_used": 0,
                "tokens_used": 0,
                "status": "cancelled",
                "llm_profile_id": target_profile_id,
                "error": f"Delegation to {target_agent.name} was cancelled",
            }
        except Exception as exc:
            logger.error(
                f"Delegation to '{target_agent.name}' failed: {exc}",
                exc_info=True,
            )
            sub_result = {
                "output": None,
                "iterations_used": 0,
                "tokens_used": 0,
                "status": "failed",
                "llm_profile_id": target_profile_id,
                "error": str(exc),
            }

        duration_ms = int((time.time() - sub_start) * 1000)
        status = str(sub_result.get("status") or "completed")
        if status not in {
            "completed",
            "failed",
            "cancelled",
            "paused",
            "budget_exceeded",
            "timeout",
            "contract_failed",
        }:
            sub_result = {
                **sub_result,
                "status": "failed",
                "error": f"Delegation returned unsupported status '{status}'",
            }
            status = "failed"

        error = self._delegation_error(target_agent.name, status, sub_result)
        outcome = DelegationOutcome(
            child_run_id=sub_run_id,
            agent_name=target_agent.name,
            status=status,
            output=sub_result.get("output"),
            error=error,
            duration_ms=duration_ms,
        )

        async with self._session_factory() as db:
            sub_run_obj = await db.get(AgentRun, sub_run_id)
            if sub_run_obj is None:
                raise RuntimeError(
                    f"Delegated AgentRun {sub_run_id} disappeared before finalization"
                )
            sub_run_obj.status = status
            output = sub_result.get("output")
            sub_run_obj.output = (
                output if isinstance(output, dict) else {"text": output}
            )
            sub_run_obj.iterations_used = sub_result.get("iterations_used", 0)
            sub_run_obj.tokens_used = sub_result.get("tokens_used", 0)
            sub_run_obj.llm_model = sub_result.get("llm_model")
            sub_run_obj.duration_ms = duration_ms
            sub_run_obj.completed_at = datetime.now(timezone.utc)
            sub_run_obj.error = error
			if sub_result.get("failover_path"):
				sub_run_obj.run_metadata = {
                    **(sub_run_obj.run_metadata or {}),
                    # String-valued map on the wire; encode the path.
					"failover_path": json.dumps(sub_result["failover_path"]),
				}
			sub_run_obj.contract_valid = sub_result.get("contract_valid")
			sub_run_obj.contract_errors = sub_result.get("contract_errors")

            await sub_executor.flush_to_db(db)
            await db.commit()

        if status == "completed":
            try:
                from src.services.execution.run_summarizer import enqueue_summarize

                await enqueue_summarize(sub_run_id)
            except Exception:
                logger.exception(
                    "Failed to enqueue summarizer for delegated run %s",
                    sub_run_id,
                )

        if self.redis_client:
            try:
                await self.redis_client.delete(
                    agent_run_steps_stream_key(str(sub_run_id))
                )
            except Exception:
                logger.debug(
                    "Failed to clean delegated run stream %s",
                    sub_run_id,
                    exc_info=True,
                )

        logger.info(
            f"Delegation to '{target_agent.name}' completed with status={status}"
        )

        if cancellation is not None:
            raise cancellation
        return outcome

    @staticmethod
    def _delegation_error(
        agent_name: str,
        status: str,
        sub_result: dict[str, Any],
    ) -> str | None:
        if status == "completed":
            return None
        if sub_result.get("error"):
            return str(sub_result["error"])
        if status == "paused" and sub_result.get("message"):
            return str(sub_result["message"])
        if status == "cancelled":
            return f"Delegation to {agent_name} was cancelled"
        if status == "paused":
            return f"Delegated agent {agent_name} is paused"
        if status == "budget_exceeded":
            return f"Delegated agent {agent_name} exceeded its budget"
        if status == "contract_failed":
            return (
                f"Delegated agent {agent_name} violated its output contract: "
                f"{sub_result.get('error') or 'validation failed'}"
            )
        if status == "timeout":
            return f"Delegation to {agent_name} timed out"
        return f"Delegation to {agent_name} failed"

    async def _execute_delegation(
        self,
        tool_call: ToolCallRequest,
        agent: Agent,
    ) -> str:
        """Execute delegation: suspend durably when leased, inline otherwise.

        Leased runs raise ``CallDeferred`` so the engine admits an
        independent child and parks the parent. Unleased runs (tests, legacy
        direct calls) keep the historical inline nested execution.
        """
        if self._durable_lease_token is not None:
            task = (tool_call.arguments or {}).get("task", "")
            if not task:
                raise ToolError("No task provided for delegation")
            target = find_delegated_agent(agent, tool_call.name)
            target_id: UUID | None = target.id if target is not None else None
            if target is None and self._snapshot_for_deferral is not None:
                # Fail fast against the immutable grant set; the suspending
                # transaction re-validates authoritatively.
                from src.services.execution.agent_helpers import (
                    agent_delegation_slug,
                )

                for delegate in self._snapshot_for_deferral.get(
                    "delegated_agents", []
                ):
                    if agent_delegation_slug(
                        delegate.get("name", "")
                    ) == tool_call.name and delegate.get("id"):
                        target_id = UUID(str(delegate["id"]))
                        break
            if target_id is None:
                raise ToolError(
                    f"Delegation target for '{tool_call.name}' not found."
                )
            if (
                target is not None
                and target.organization_id is not None
                and target.organization_id != agent.organization_id
            ):
                raise ToolError(
                    f"Delegation target '{target.name}' is outside the "
                    "parent agent's organization."
                )
            from src.services.agent_runtime.delegation import (
                DelegationSpec,
                delegation_call_metadata,
            )

            raise CallDeferred(
                delegation_call_metadata(
                    DelegationSpec(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        target_agent_id=target_id,
                        task=task,
                        output_schema=(tool_call.arguments or {}).get(
                            "output_schema"
                        ),
                    )
                )
            )
        outcome = await self.run_delegation(
            parent_agent=agent,
            tool_call=tool_call,
            parent_run_id=self._current_run_id,
            caller=self._caller,
        )
        if not outcome.succeeded:
            raise ToolError(
                outcome.error or f"Delegation ended with status {outcome.status}"
            )
        if isinstance(outcome.output, dict):
            return json.dumps(outcome.output)
        if outcome.output is None:
            return "Delegation completed with no output."
        return str(outcome.output)

    async def _execute_fanout(
        self,
        tool_call: ToolCallRequest,
        agent: Agent,
    ) -> str:
        """Execute a fan-out: suspend durably when leased, inline otherwise."""
        from src.services.agent_runtime.delegation import parse_fanout_args

        arguments = tool_call.arguments or {}
        if self._durable_lease_token is not None:
            # Fail fast on malformed requests inside the loop; the suspending
            # transaction re-validates grants, depth, cycles, and limits.
            parse_fanout_args(arguments)
            raise CallDeferred(
                {
                    "kind": "fanout",
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_call.name,
                    "arguments": arguments,
                }
            )
        specs, _ = parse_fanout_args(arguments)
        from src.services.llm.base import ToolCallRequest as LlmToolCallRequest

        items = []
        for spec in specs:
            target = find_delegated_agent(
                agent, f"delegate_to_{spec.agent_name.lower().replace(' ', '_')}"
            )
            if target is None:
                raise ToolError(
                    f"Fan-out target '{spec.agent_name}' not found."
                )
            outcome = await self.run_delegation(
                parent_agent=agent,
                tool_call=LlmToolCallRequest(
                    id=f"{tool_call.id}:{spec.position}",
                    name=f"delegate_to_{spec.agent_name.lower().replace(' ', '_')}",
                    arguments={
                        "task": spec.task,
                        **(
                            {"output_schema": spec.output_schema}
                            if spec.output_schema
                            else {}
                        ),
                    },
                ),
                parent_run_id=self._current_run_id,
                caller=self._caller,
                output_schema=spec.output_schema,
                _shared_usage=self._active_usage,
                _shared_budget=self._active_budget,
            )
            output = outcome.output
            items.append(
                {
                    "position": spec.position,
                    "run_id": str(outcome.child_run_id),
                    "agent": outcome.agent_name,
                    "status": outcome.status,
                    "output": output,
                    "error": outcome.error,
                }
            )
        return json.dumps(
            {"mode": "all", "children": items}, default=str, sort_keys=False
        )

    async def _execute_sleep(
        self,
        tool_call: ToolCallRequest,
        agent: Agent,
    ) -> str:
        """Execute a timer: suspend durably when leased, sleep inline otherwise."""
        from datetime import datetime as _datetime
        from datetime import timezone as _timezone

        from src.services.agent_runtime.timers import (
            TIMER_FIRED_TEXT,
            parse_timer_args,
            timer_call_metadata,
        )

        _ = agent
        arguments = tool_call.arguments or {}
        if self._durable_lease_token is not None:
            wake_at = parse_timer_args(
                arguments, now=_datetime.now(_timezone.utc)
            )
            raise CallDeferred(
                timer_call_metadata(
                    tool_call.id,
                    wake_at,
                    str(arguments.get("reason", "")),
                )
            )
        wake_at = parse_timer_args(arguments, now=_datetime.now(_timezone.utc))
        delay = (wake_at - _datetime.now(_timezone.utc)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        return TIMER_FIRED_TEXT

    async def _execute_system_tool(self, tool_call: ToolCallRequest, agent: Agent) -> str:
        """Execute a system tool."""
        from src.services.mcp_server.server import MCPContext, get_system_tool_function

        func = get_system_tool_function(tool_call.name)
        if not func:
            raise ToolError(f"System tool '{tool_call.name}' not found")

        try:
            # Brief DB session scoped to the tool call
            async with self._session_factory() as db:
                context = MCPContext(
                    user_id=(
                        str(self._caller_user_id)
                        if self._caller_user_id
                        else SYSTEM_USER_ID
                    ),
                    org_id=(
                        str(execution_org_id)
                        if (execution_org_id := self._execution_org_id(agent))
                        else None
                    ),
                    is_platform_admin=(
                        bool(self._caller.get("is_platform_admin", False))
                        if self._caller_user_id and self._caller
                        else False
                    ),
                    user_email=(
                        str(self._caller.get("email"))
                        if self._caller_user_id
                        and self._caller
                        and self._caller.get("email")
                        else SYSTEM_USER_EMAIL
                    ),
                    user_name=(
                        str(self._caller.get("name"))
                        if self._caller_user_id
                        and self._caller
                        and self._caller.get("name")
                        else agent.name
                    ),
                    session=db,
                    operation_id=self._current_operation_id,
                )

                result = await func(context, **tool_call.arguments)
                await db.commit()

            # Extract result from FastMCP ToolResult format
            import pydantic_core

            if hasattr(result, "content") and hasattr(result, "structured_content"):
                result_data = {
                    "content": pydantic_core.to_jsonable_python(result.content),
                    "structured_content": result.structured_content,
                }
            elif hasattr(result, "content"):
                result_data = pydantic_core.to_jsonable_python(result.content)
            else:
                result_data = str(result)

            return json.dumps(result_data) if isinstance(result_data, (dict, list)) else str(result_data)

        except Exception as e:
            logger.error(f"System tool {tool_call.name} failed: {e}", exc_info=True)
            raise ToolError(f"System tool error: {e}") from e

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def _check_cancelled(self, run_id: str) -> bool:
        """Check this run and every ancestor for a Redis cancellation flag."""
        if not self.redis_client:
            return False
        try:
            for candidate_run_id in dict.fromkeys((run_id, *self._ancestor_run_ids)):
                key = f"bifrost:agent_run:{candidate_run_id}:cancel"
                if await self.redis_client.get(key) is not None:
                    return True
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # AI usage buffering
    # ------------------------------------------------------------------

    def _buffer_ai_usage(
        self,
        agent: Agent,
        run_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        provider_cost: Decimal | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Buffer an AI usage entry for later DB flush."""
        if not self.redis_client:
            return
        self._pending_ai_usage.append({
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "provider_cost": provider_cost,
            "duration_ms": duration_ms,
            "agent_run_id": UUID(run_id),
            "organization_id": agent.organization_id,
        })

    # ------------------------------------------------------------------
    # Step recording (Redis-first)
    # ------------------------------------------------------------------

    async def _record_step(
        self,
        run_id: str,
        step_number: int,
        step_type: str,
        content: dict | None = None,
        *,
        tokens_used: int | None = None,
        duration_ms: int | None = None,
        buffer: bool = True,
    ) -> None:
        """Record a step to Redis Stream and buffer for later DB flush.

        Steps are NOT written to Postgres here — they are buffered in
        self._pending_steps and flushed via flush_to_db() after the run.
        Durable runs pass ``buffer=False``: their step rows are projected
        by the boundary checkpoint instead, while live stream/pubsub
        updates still flow.
        """
        step_id = str(uuid4())

        # Buffer for later DB flush
        if buffer:
            self._pending_steps.append({
                "id": step_id,
                "run_id": run_id,
                "step_number": step_number,
                "type": step_type,
                "content": content,
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
            })

        # Broadcast step for real-time updates
        step_data = {
            "id": step_id,
            "run_id": str(run_id),
            "step_number": step_number,
            "type": step_type,
            "content": content,
            "tokens_used": tokens_used,
            "duration_ms": duration_ms,
        }
        try:
            await publish_agent_run_step(run_id=str(run_id), step=step_data)
        except Exception:
            pass  # Don't fail the run if pub/sub fails

        # Write to Redis Stream for dual-read (API reads from Redis when run is in-progress)
        if self.redis_client:
            try:
                stream_key = agent_run_steps_stream_key(str(run_id))
                await self.redis_client.xadd(
                    stream_key,
                    {
                        "id": step_id,
                        "run_id": str(run_id),
                        "step_number": str(step_number),
                        "type": step_type,
                        "content": json.dumps(content) if content else "{}",
                        "tokens_used": str(tokens_used) if tokens_used is not None else "",
                        "duration_ms": str(duration_ms) if duration_ms is not None else "",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    maxlen=1000,
                )
            except Exception:
                pass  # Don't fail the run if Redis write fails
