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
import re
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
from pydantic_ai import Agent as PydanticAgent
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
from shared.run_lineage import run_user_lineage
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
    bound_tool_result_for_model,
    build_chain_model,
    build_runtime_capabilities,
    provider_reported_cost,
    reasoning_tokens,
)
from src.services.agent_runtime.empty_output import EmptyOutputCircuitBreaker
from src.services.llm import ToolCallRequest
from src.services.llm.factory import get_llm_configs
from src.services.model_pricing import chain_context_window
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
MAX_DELEGATION_TIMEOUT_RECEIPT_CHARS = 20_000
MAX_DELEGATION_TIMEOUT_EVIDENCE_ITEM_CHARS = 4_000

_JSON_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n\s*```$", re.DOTALL)


def _parse_structured_output(content: str) -> Any:
    """Parse a structured-output reply, tolerating one outer markdown code fence.

    Models asked for JSON sometimes wrap it in a ```json fence. Returns the
    parsed value, or the original string when it is not JSON.
    """
    text = content.strip()
    fenced = _JSON_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.debug("final agent output is not JSON, returning raw string: %s", exc)
        return content


class ToolError(Exception):
    """Raised when a tool call fails in an expected way (unknown tool, delegation failure, etc.)."""
    pass


@dataclass(frozen=True)
class ActingUser:
    """Whose identity a run's tool calls and delegated children use.

    For a Run As run it is the target user, loaded from the database by the
    consumer. Otherwise it is the run's caller, or the system account when
    nobody started the run. It never changes the run user (lineage).
    """

    user_id: str
    email: str
    name: str
    organization_id: UUID | None
    is_platform_admin: bool
    is_external: bool
    is_provider_org: bool


def _acting_user_from_caller(
    caller_user_id: UUID | None,
    caller: dict[str, Any] | None,
    agent: Agent,
) -> ActingUser:
    """The caller as the acting user, or the system account with the agent's name.

    The organization is the caller's, or the agent's for a run nobody started.
    """
    # ``caller_user_id`` is parsed from ``caller``, so it is never set alone.
    if caller_user_id is None or caller is None:
        return ActingUser(
            user_id=SYSTEM_USER_ID,
            email=SYSTEM_USER_EMAIL,
            name=agent.name,
            organization_id=agent.organization_id,
            is_platform_admin=False,
            is_external=False,
            is_provider_org=False,
        )
    raw_org_id = caller.get("organization_id")
    return ActingUser(
        user_id=str(caller_user_id),
        email=str(caller.get("email")) if caller.get("email") else SYSTEM_USER_EMAIL,
        name=str(caller.get("name")) if caller.get("name") else agent.name,
        organization_id=None if raw_org_id is None else UUID(str(raw_org_id)),
        is_platform_admin=bool(caller.get("is_platform_admin", False)),
        is_external=False,
        is_provider_org=False,
    )


@dataclass(frozen=True)
class DelegationOutcome:
    """Canonical internal result for a delegated child run."""

    child_run_id: UUID
    agent_name: str
    status: str
    output: str | dict | None
    error: str | None
    duration_ms: int
    evidence: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"


def _truncate_timeout_evidence_item(value: Any) -> tuple[str, bool]:
    """Keep one saved tool fact small without pretending it is structured JSON."""
    text = str(value)
    if len(text) <= MAX_DELEGATION_TIMEOUT_EVIDENCE_ITEM_CHARS:
        return text, False
    omitted = len(text) - MAX_DELEGATION_TIMEOUT_EVIDENCE_ITEM_CHARS
    return (
        text[:MAX_DELEGATION_TIMEOUT_EVIDENCE_ITEM_CHARS]
        + f" [stored result truncated; {omitted} characters omitted]",
        True,
    )


def _capture_tool_result_for_timeout_evidence(value: Any) -> tuple[str, bool]:
    """Keep the durable step copy bounded and make loss explicit at capture."""
    text = str(value)
    if len(text) <= MAX_DELEGATION_TIMEOUT_RECEIPT_CHARS:
        return text, False
    omitted = len(text) - MAX_DELEGATION_TIMEOUT_RECEIPT_CHARS
    while True:
        marker = f" [stored result truncated; {omitted} characters omitted]"
        actual_omitted = len(text) - (
            MAX_DELEGATION_TIMEOUT_RECEIPT_CHARS - len(marker)
        )
        if actual_omitted == omitted:
            break
        omitted = actual_omitted
    return (
        text[:MAX_DELEGATION_TIMEOUT_RECEIPT_CHARS - len(marker)] + marker,
        True,
    )


def _delegation_evidence_lines(executor: Any) -> list[str]:
    """Extract only the executor's captured, non-LLM tool lifecycle facts."""
    lines: list[str] = []
    completed_child_runs: set[str] = set()
    settled_by_tool: dict[str, int] = {}
    calls: list[tuple[str, str]] = []

    for step in executor._pending_steps:
        content = step.get("content") or {}
        step_type = step.get("type")
        if step_type == "tool_call":
            tool_name = content.get("tool_name")
            if tool_name:
                calls.append((str(step.get("id")), str(tool_name)))
            continue
        if step_type not in {"tool_result", "tool_error"}:
            continue

        tool_name = str(content.get("tool_name") or "unknown tool")
        settled_by_tool[tool_name] = settled_by_tool.get(tool_name, 0) + 1
        step_id = str(step.get("id"))
        ids = ""
        if content.get("execution_id"):
            ids += f" execution_id={content['execution_id']}"
        if content.get("child_run_id"):
            child_run_id = str(content["child_run_id"])
            completed_child_runs.add(child_run_id)
            ids += f" child_run_id={child_run_id}"

        is_error = step_type == "tool_error" or bool(content.get("is_error"))
        if is_error:
            detail, _ = _truncate_timeout_evidence_item(
                content.get("error") or content.get("result") or "tool failed"
            )
            lines.append(f"failed step {step_id} ({tool_name}): {detail}{ids}")
            continue

        detail, was_truncated = _truncate_timeout_evidence_item(
            content.get("result", "")
        )
        truncation = " [stored result truncated]" if (
            was_truncated or content.get("result_truncated")
        ) else ""
        lines.append(
            f"completed step {step_id} ({tool_name}): {detail}{truncation}{ids}"
        )

    for step_id, tool_name in calls:
        remaining_settled = settled_by_tool.get(tool_name, 0)
        if remaining_settled:
            settled_by_tool[tool_name] = remaining_settled - 1
            continue
        lines.append(
            f"unfinished step {step_id} ({tool_name}); side effects are unverified"
        )

    for outcome in executor._finalized_delegation_outcomes:
        if str(outcome.child_run_id) in completed_child_runs:
            continue
        status = outcome.status
        prefix = (
            f"nested delegation child_run_id={outcome.child_run_id} "
            f"({outcome.agent_name}) status={status}"
        )
        if outcome.evidence:
            detail, _ = _truncate_timeout_evidence_item(outcome.evidence)
            lines.append(f"{prefix}; captured evidence: {detail}")
        if status == "completed" and outcome.output is not None:
            detail, _ = _truncate_timeout_evidence_item(outcome.output)
            lines.append(f"{prefix}; completed receipt: {detail}")
        elif status != "completed":
            lines.append(f"{prefix}; side effects are unverified")

    return lines


def _bounded_timeout_receipt(
    lines: list[str],
    *,
    max_chars: int = MAX_DELEGATION_TIMEOUT_RECEIPT_CHARS,
) -> str:
    """Apply one global evidence cap and say explicitly when facts were omitted."""
    if not lines:
        return "No completed tool evidence was captured before the deadline."

    selected: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        addition = len(line) + (1 if selected else 0)
        if used + addition <= max_chars:
            selected.append(line)
            used += addition
            continue
        omitted = len(lines) - index
        while True:
            marker = f"[partial evidence truncated; {omitted} item(s) omitted]"
            if used + len(marker) + (1 if selected else 0) <= max_chars:
                selected.append(marker)
                break
            if not selected:
                # The normal timeout prefix leaves far more than this marker,
                # but retain a valid explicit omission if a caller supplies a
                # deliberately tiny evidence budget.
                return marker[:max_chars]
            removed = selected.pop()
            used -= len(removed) + (1 if selected else 0)
            omitted += 1
        return "\n".join(selected)
    return "\n".join(selected)


def format_delegation_timeout_receipt(
    agent_name: str,
    executor: Any,
) -> str:
    """Build caller-safe timeout evidence from one executing child's own subtree."""
    prefix = (
        f"Delegation to {agent_name} timed out after {DELEGATION_TIMEOUT_SECONDS}s.\n"
        "Partial execution evidence follows. Captured tool receipts do not establish "
        "overall completion. "
        "Unfinished side effects are unverified.\n"
    )
    evidence = _bounded_timeout_receipt(
        _delegation_evidence_lines(executor),
        max_chars=MAX_DELEGATION_TIMEOUT_RECEIPT_CHARS - len(prefix),
    )
    receipt = prefix + evidence
    # Keep the model-facing copy on the shared 32K result policy even though
    # the durable receipt has the stricter 20K cap above.
    bounded = bound_tool_result_for_model(receipt)
    return bounded if isinstance(bounded, str) else str(bounded)


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
        # Whose identity tool calls use; run() sets it (see ActingUser).
        self._acting: ActingUser | None = None
        # The Run As user, handed unchanged to delegated children.
        self._run_as: ActingUser | None = None
        # The acting person MCP connections authenticate as. ``None`` means
        # the run is autonomous (scheduled / webhook / event-trigger), in
        # which case dispatch resolves to the service token only.
        self._acting_user_id: UUID | None = None
        self._caller: dict[str, Any] | None = None
        self._run_user_id: UUID | None = None
        # Buffers for Redis-first pattern (flushed to DB after run completes)
        self._pending_steps: list[dict[str, Any]] = []
        self._pending_ai_usage: list[dict[str, Any]] = []
        # Terminal child outcomes belonging to this executor. This closes the
        # cancellation gap where a nested child is finalized but its parent
        # never receives a BifrostToolset result event before cancellation.
        self._finalized_delegation_outcomes: list[DelegationOutcome] = []
        self._knowledge_search_budget = KnowledgeSearchBudget()
        # Delegated executors receive these same objects. Pydantic AI mutates
        # RunUsage in place, so every model request in the delegation tree is
        # charged to the root run instead of giving each child a fresh budget.
        self._active_usage: RunUsage | None = None
        self._active_budget: AgentRunBudget | None = None
        # Reported totals for this run's subtree: its own model calls plus
        # every delegate's subtree. Unlike a delta on the shared ledger, this
        # never absorbs a concurrent sibling's calls, and it survives a
        # timeout or cancellation because it is updated per response.
        self._own_tokens = 0
        self._delegated_tokens = 0

    @property
    def acting_user(self) -> ActingUser:
        """The active run's acting user; run() sets it before any tool call."""
        if self._acting is None:
            raise RuntimeError("run() sets the acting user before any tool call")
        return self._acting

    @property
    def subtree_tokens(self) -> int:
        """Tokens used by this run's own calls plus all of its delegates."""
        return self._own_tokens + self._delegated_tokens

    async def run(
        self,
        agent: Agent,
        *,
        input_data: dict | None = None,
        output_schema: dict | None = None,
        run_id: str | None = None,
        _caller: dict | None = None,
        _shared_usage: RunUsage | None = None,
        _shared_budget: AgentRunBudget | None = None,
        run_user_id: UUID | None,
        run_as: ActingUser | None,
    ) -> dict:
        """Execute an autonomous agent run.

        Args:
            agent: The Agent ORM instance to execute.
            input_data: Input payload (serialized as JSON in the user message).
            output_schema: Optional JSON Schema the agent should conform its output to.
            run_id: External run ID (generates one if not provided).
            _caller: Optional caller metadata for context.
            _shared_usage: Internal cumulative usage ledger inherited from a
                parent run during delegation.
            _shared_budget: Internal hard ceiling inherited from a parent run.
            run_user_id: The run's run user (AgentRun.run_user_id); its tool
                calls and delegations run for the same user.
            run_as: The Run As user, loaded from the database; None means the
                caller acts. Delegated children get the same value.

        Returns:
            Dict with keys: output, iterations_used, tokens_used, status, llm_model
            (and optionally "error" if status is "failed").
        """
        run_id = run_id or str(uuid4())
        self._current_run_id = run_id
        self._knowledge_search_budget.reset()
        self._own_tokens = 0
        self._delegated_tokens = 0
        self._finalized_delegation_outcomes = []

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
        self._caller = dict(_caller) if _caller else None
        self._run_user_id = run_user_id
        self._run_as = run_as
        if run_as is None:
            self._acting = _acting_user_from_caller(caller_user_id, self._caller, agent)
            self._acting_user_id = caller_user_id
        else:
            self._acting = run_as
            self._acting_user_id = UUID(run_as.user_id)

        async with self._session_factory() as db:
            llm_configs = await get_llm_configs(db, profile_id=agent.llm_profile_id)
            context_window = await chain_context_window(db, llm_configs)
        llm_config = llm_configs[0]
        model_name = llm_config.model

        # Short-circuit if agent is paused. Runs already past this point continue
        # normally — this check only gates new runs at entry.
        if not agent.is_active:
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
        configured_iterations = agent.max_iterations
        configured_tokens = agent.max_token_budget
        usage = _shared_usage or RunUsage()
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
        async with self._session_factory() as db:
            tool_definitions, self._tool_workflow_id_map = await resolve_agent_tools(
                agent,
                db,
                caller_user_id=self._acting_user_id,
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
            self._own_tokens += request_usage.total_tokens
            request_reasoning_tokens = reasoning_tokens(request_usage)
            self._buffer_ai_usage(
                agent=agent,
                run_id=run_id,
                provider=event.billing_provider or llm_config.provider,
                model=response.model_name or model_name,
                input_tokens=request_usage.input_tokens,
                output_tokens=request_usage.output_tokens,
                cache_read_tokens=request_usage.cache_read_tokens,
                cache_write_tokens=request_usage.cache_write_tokens,
                reasoning_tokens=request_reasoning_tokens,
                provider_cost=provider_reported_cost(response),
                duration_ms=event.duration_ms or 0,
            )
            step_number += 1
            await self._record_step(
                run_id,
                step_number,
                "llm_response",
                {
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
                        "reasoning_tokens": request_reasoning_tokens,
                        "provider_cost": (
                            str(cost)
                            if (cost := provider_reported_cost(response)) is not None
                            else None
                        ),
                    },
                },
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
            return await self._execute_tool(
                ToolCallRequest(id=tool_call_id, name=name, arguments=arguments),
                agent,
            )

        async def record_tool_event(event: ToolEvent) -> None:
            nonlocal step_number
            step_number += 1
            if event.type == "tool_call":
                await self._record_step(
                    run_id,
                    step_number,
                    "tool_call",
                    {"tool_name": event.tool_name, "arguments": event.arguments},
                )
                return

            content: dict[str, Any] = {
                "tool_name": event.tool_name,
                "is_error": event.type == "tool_error" or self._last_workflow_execution_is_error,
            }
            if event.type == "tool_error":
                content["error"] = event.error
            else:
                result, result_truncated = _capture_tool_result_for_timeout_evidence(
                    event.result
                )
                content["result"] = result
                if result_truncated:
                    content["result_truncated"] = True
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
            chain.model,
            system_prompt=build_agent_system_prompt(
                agent,
                execution_context={"mode": "autonomous"},
            ),
            toolsets=[toolset] if tool_definitions else [],
            capabilities=[
                *build_runtime_capabilities(budget, context_window=context_window),
                empty_output_guard,
            ],
            model_settings=chain.primary_settings,
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
        try:
            result = await runtime.run(
                user_content,
                usage_limits=budget.usage_limits(),
                usage=usage,
                conversation_id=run_id,
            )
            final_content = result.output
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
        if output_schema and final_content:
            output = _parse_structured_output(final_content)

        response = {
            "output": output or None,
            "iterations_used": usage.requests - usage_start_requests,
            "tokens_used": self.subtree_tokens,
            "status": status,
            "llm_model": model_name,
        }
        if error and status == "failed":
            response["error"] = error
        if chain.failover is not None and (path := chain.failover.fallback_path()):
            response["failover_path"] = path
        return response

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

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_call: ToolCallRequest, agent: Agent) -> str:
        """Execute a tool call, mirroring AgentExecutor's dispatch logic."""
        # Knowledge search
        if tool_call.name == "search_knowledge" and agent.knowledge_sources:
            return await self._execute_knowledge_search(tool_call, agent)

        # Delegation
        if tool_call.name.startswith("delegate_to_"):
            return await self._execute_delegation(tool_call, agent)

        # System tools
        if tool_call.name in (agent.system_tools or []):
            return await self._execute_system_tool(tool_call)

        # External MCP tools — namespaced ``mcp__<connection_id>__<tool>``.
        # Routed BEFORE workflow tools because the workflow id_map maps
        # MCP qualified names to ``MCPConnection.id`` and dispatch needs
        # to go through ``mcp_dispatch.invoke`` rather than the workflow
        # execution service. The threaded ``self._acting_user_id`` is
        # what differentiates a chat-claim webhook or Run As (per-user
        # OAuth) from a fully autonomous run (service token only).
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

        acting = self.acting_user
        response = await execute_agent_workflow_tool(
            workflow_id=workflow_id,
            workflow_name=tool_call.name,
            parameters=tool_call.arguments or {},
            caller=AgentWorkflowCaller(
                user_id=acting.user_id,
                email=acting.email,
                name=acting.name,
                organization_id=acting.organization_id,
                is_platform_admin=acting.is_platform_admin,
            ),
            artifact_workspace_id=(
                self._ancestor_run_ids[0]
                if self._ancestor_run_ids
                else self._current_run_id or None
            ),
            lineage=run_user_lineage(self._run_user_id),
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

        For autonomous runs ``self._acting_user_id`` is typically
        ``None`` — auth resolution then routes to the connection's
        service token, gated by ``available_to_autonomous``. Webhook
        deliveries that pass a user_id (signed claim) and Run As runs
        get user-token resolution for the acting user.

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
                    caller_user_id=self._acting_user_id,
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
        _shared_usage: RunUsage | None = None,
        _shared_budget: AgentRunBudget | None = None,
        run_user_id: UUID | None,
        run_as: ActingUser | None,
    ) -> DelegationOutcome:
        """Run one delegated child with a durable, caller-neutral lifecycle.

        The child keeps the delegating run's user (``run_user_id``) and its
        Run As user (``run_as``).
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
        if delegation_org_id is None and run_as is not None:
            # A Run As run's children stay in the organization its tools use.
            delegation_org_id = run_as.organization_id
        elif (
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
                status="running",
                org_id=delegation_org_id,
                caller_user_id=caller.get("user_id") if caller else None,
                caller_email=caller.get("email") if caller else None,
                caller_name=caller.get("name") if caller else None,
                run_user_id=run_user_id,
                run_as_user_id=UUID(run_as.user_id) if run_as else None,
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
                    run_id=str(sub_run_id),
                    _caller=caller,
                    _shared_usage=shared_usage,
                    _shared_budget=shared_budget,
                    run_user_id=run_user_id,
                    run_as=run_as,
                ),
                timeout=DELEGATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Delegation to '{target_agent.name}' timed out after "
                f"{DELEGATION_TIMEOUT_SECONDS}s"
            )
            receipt = format_delegation_timeout_receipt(target_agent.name, sub_executor)
            sub_result = {
                # Durable, sanitized facts from the interrupted subtree are
                # useful for the run record, but do not change timeout status.
                "output": receipt,
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
        # The child's own counter is authoritative even when it timed out or
        # was cancelled before returning a result.
        sub_tokens = sub_executor.subtree_tokens
        self._delegated_tokens += sub_tokens
        status = str(sub_result.get("status") or "completed")
        if status not in {
            "completed",
            "failed",
            "cancelled",
            "paused",
            "budget_exceeded",
            "timeout",
        }:
            sub_result = {
                **sub_result,
                "status": "failed",
                "error": f"Delegation returned unsupported status '{status}'",
            }
            status = "failed"

        error = self._delegation_error(target_agent.name, status, sub_result)
        evidence = (
            None
            if status == "completed"
            else _bounded_timeout_receipt(_delegation_evidence_lines(sub_executor))
        )
        caller_error = error
        if status == "timeout":
            caller_error = format_delegation_timeout_receipt(
                target_agent.name, sub_executor
            )
        outcome = DelegationOutcome(
            child_run_id=sub_run_id,
            agent_name=target_agent.name,
            status=status,
            output=sub_result.get("output"),
            error=caller_error,
            duration_ms=duration_ms,
            evidence=evidence,
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
            sub_run_obj.tokens_used = sub_tokens
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

        self._finalized_delegation_outcomes.append(outcome)
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
        if status == "timeout":
            return f"Delegation to {agent_name} timed out"
        return f"Delegation to {agent_name} failed"

    async def _execute_delegation(
        self,
        tool_call: ToolCallRequest,
        agent: Agent,
    ) -> str:
        """Execute delegation for an autonomous parent run."""
        outcome = await self.run_delegation(
            parent_agent=agent,
            tool_call=tool_call,
            parent_run_id=self._current_run_id,
            caller=self._caller,
            run_user_id=self._run_user_id,
            run_as=self._run_as,
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

    async def _execute_system_tool(self, tool_call: ToolCallRequest) -> str:
        """Execute a system tool."""
        from src.services.mcp_server.server import MCPContext, get_system_tool_function

        func = get_system_tool_function(tool_call.name)
        if not func:
            raise ToolError(f"System tool '{tool_call.name}' not found")

        try:
            # Brief DB session scoped to the tool call
            acting = self.acting_user
            async with self._session_factory() as db:
                context = MCPContext(
                    user_id=acting.user_id,
                    org_id=(
                        str(acting.organization_id)
                        if acting.organization_id
                        else None
                    ),
                    is_platform_admin=acting.is_platform_admin,
                    is_provider_org=acting.is_provider_org,
                    is_external=acting.is_external,
                    user_email=acting.email,
                    user_name=acting.name,
                    run_user_id=self._run_user_id,
                    session=db,
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
        reasoning_tokens: int = 0,
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
            "reasoning_tokens": reasoning_tokens,
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
    ) -> None:
        """Record a step to Redis Stream and buffer for later DB flush.

        Steps are NOT written to Postgres here — they are buffered in
        self._pending_steps and flushed via flush_to_db() after the run.
        """
        step_id = str(uuid4())

        # Buffer for later DB flush
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
