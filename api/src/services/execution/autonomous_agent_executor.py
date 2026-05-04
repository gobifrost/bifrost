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
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.models.orm.agents import Agent
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
from src.services.llm import LLMMessage, ToolCallRequest, get_llm_client
from src.services.mcp_client import dispatch as mcp_dispatch
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50  # Hard ceiling
MAX_DELEGATION_DEPTH = 5  # Prevent infinite delegation chains
DELEGATION_TIMEOUT_SECONDS = 600  # 10 minutes per delegation


class ToolError(Exception):
    """Raised when a tool call fails in an expected way (unknown tool, delegation failure, etc.)."""
    pass


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
    ):
        self._session_factory = session_factory
        self.redis_client = redis_client
        self._delegation_depth = _delegation_depth
        self._tool_workflow_id_map: dict[str, UUID] = {}
        self._current_run_id: str = ""
        self._last_delegation_run_id: str | None = None
        # Caller_user_id for the active run, threaded into MCP dispatch.
        # ``None`` means the run is autonomous (scheduled / webhook /
        # event-trigger), in which case dispatch resolves to the
        # service token only.
        self._caller_user_id: UUID | None = None
        # Buffers for Redis-first pattern (flushed to DB after run completes)
        self._pending_steps: list[dict[str, Any]] = []
        self._pending_ai_usage: list[dict[str, Any]] = []

    async def run(
        self,
        agent: Agent,
        *,
        input_data: dict | None = None,
        output_schema: dict | None = None,
        run_id: str | None = None,
        _caller: dict | None = None,
    ) -> dict:
        """Execute an autonomous agent run.

        Args:
            agent: The Agent ORM instance to execute.
            input_data: Input payload (serialized as JSON in the user message).
            output_schema: Optional JSON Schema the agent should conform its output to.
            run_id: External run ID (generates one if not provided).
            _caller: Optional caller metadata for context.

        Returns:
            Dict with keys: output, iterations_used, tokens_used, status, llm_model
            (and optionally "error" if status is "failed").
        """
        run_id = run_id or str(uuid4())
        self._current_run_id = run_id

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
                "llm_model": agent.llm_model,
            }

        step_number = 0
        iterations_used = 0
        tokens_used = 0
        max_iterations = min(agent.max_iterations or 50, MAX_ITERATIONS)
        max_tokens = agent.max_token_budget or 100000

        # Resolve tools and get LLM client (brief DB reads, then release).
        # ``caller_user_id`` controls MCP-tool inclusion at planning:
        # autonomous runs (None) only see MCP tools whose connection
        # is flagged ``available_to_autonomous`` AND has a usable
        # service token. Chat-claim webhooks that pass a user_id see
        # all enabled MCP tools in the agent's org.
        async with self._session_factory() as db:
            tool_definitions, self._tool_workflow_id_map = await resolve_agent_tools(
                agent, db, caller_user_id=caller_user_id
            )
            llm_client = await get_llm_client(db)

        # Build initial messages
        system_prompt = build_agent_system_prompt(agent, execution_context={"mode": "autonomous"})
        user_content = json.dumps(input_data) if input_data else "Run your task."
        if output_schema:
            user_content += f"\n\nRespond with JSON matching this schema:\n{json.dumps(output_schema)}"

        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_content),
        ]

        model = agent.llm_model

        # Record initial request step
        step_number += 1
        await self._record_step(run_id, step_number, "llm_request", {
            "messages_count": len(messages),
            "tools_count": len(tool_definitions),
            "model": model,
        })

        # Main loop
        final_content = ""
        status = "completed"

        while iterations_used < max_iterations:
            # Check for cancellation before each iteration (Layer 2: graceful)
            if await self._check_cancelled(run_id):
                status = "cancelled"
                step_number += 1
                await self._record_step(run_id, step_number, "cancelled", {
                    "reason": "Cancelled by user",
                    "iterations_used": iterations_used,
                })
                break

            iterations_used += 1
            start_time = time.time()

            # Budget warning at 80%
            if iterations_used == int(max_iterations * 0.8):
                messages.append(LLMMessage(
                    role="system",
                    content="You are approaching your iteration budget. "
                            "Please wrap up your work and provide your final output.",
                ))
                step_number += 1
                await self._record_step(run_id, step_number, "budget_warning", {
                    "iterations_used": iterations_used,
                    "max_iterations": max_iterations,
                })

            # Call LLM — NO DB connection held during this call
            try:
                response = await llm_client.complete(
                    messages=messages,
                    tools=tool_definitions if tool_definitions else None,
                    model=model,
                    max_tokens=agent.llm_max_tokens,
                )
            except Exception as e:
                logger.error(f"LLM call failed in run {run_id}: {e}", exc_info=True)
                step_number += 1
                await self._record_step(run_id, step_number, "error", {
                    "error": str(e),
                    "phase": "llm_call",
                })
                return {
                    "output": None,
                    "iterations_used": iterations_used,
                    "tokens_used": tokens_used,
                    "status": "failed",
                    "llm_model": model,
                    "error": str(e),
                }

            duration_ms = int((time.time() - start_time) * 1000)
            chunk_tokens = (response.input_tokens or 0) + (response.output_tokens or 0)
            tokens_used += chunk_tokens

            # Capture the actual model name from the first LLM response
            if not model and response.model:
                model = response.model

            # Buffer AI usage for later DB flush
            self._buffer_ai_usage(
                agent=agent,
                run_id=run_id,
                provider=llm_client.provider_name,
                model=response.model or model or "",
                input_tokens=response.input_tokens or 0,
                output_tokens=response.output_tokens or 0,
                duration_ms=duration_ms,
            )

            # Record LLM response step
            step_number += 1
            await self._record_step(run_id, step_number, "llm_response", {
                "content": (response.content or "")[:20000],
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in (response.tool_calls or [])
                ],
                "finish_reason": response.finish_reason,
            }, tokens_used=chunk_tokens, duration_ms=duration_ms)

            # No tool calls = done
            if not response.tool_calls:
                final_content = response.content or ""
                break

            # Add assistant message with tool calls to history
            messages.append(LLMMessage(
                role="assistant",
                content=response.content if response.content else None,
                tool_calls=response.tool_calls,
            ))

            # Execute tools
            cancelled_during_tools = False
            for tc in response.tool_calls:
                # Check for cancellation between tool calls
                if await self._check_cancelled(run_id):
                    cancelled_during_tools = True
                    break

                step_number += 1
                await self._record_step(run_id, step_number, "tool_call", {
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                })

                tool_start = time.time()
                try:
                    result = await self._execute_tool(tc, agent)
                    tool_duration = int((time.time() - tool_start) * 1000)

                    step_content: dict = {
                        "tool_name": tc.name,
                        "result": str(result)[:20000],
                        "is_error": False,
                    }
                    # Include child_run_id for delegation steps
                    if tc.name.startswith("delegate_to_") and self._last_delegation_run_id:
                        step_content["child_run_id"] = self._last_delegation_run_id

                    step_number += 1
                    await self._record_step(run_id, step_number, "tool_result", step_content, duration_ms=tool_duration)

                    messages.append(LLMMessage(
                        role="tool",
                        content=str(result),
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                    ))
                except Exception as e:
                    tool_duration = int((time.time() - tool_start) * 1000)
                    step_number += 1
                    await self._record_step(run_id, step_number, "tool_error", {
                        "tool_name": tc.name,
                        "error": str(e),
                        "is_error": True,
                    }, duration_ms=tool_duration)

                    messages.append(LLMMessage(
                        role="tool",
                        content=f"Error: {e}",
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                    ))

            # Check if cancelled during tool execution
            if cancelled_during_tools:
                status = "cancelled"
                step_number += 1
                await self._record_step(run_id, step_number, "cancelled", {
                    "reason": "Cancelled by user during tool execution",
                    "iterations_used": iterations_used,
                })
                break

            # Check token budget
            if tokens_used >= max_tokens:
                status = "budget_exceeded"
                step_number += 1
                await self._record_step(run_id, step_number, "budget_warning", {
                    "tokens_used": tokens_used,
                    "max_tokens": max_tokens,
                    "reason": "token_budget_exceeded",
                })
                break
        else:
            # Loop exhausted without breaking — iteration budget exceeded
            status = "budget_exceeded"

        # Parse output
        output: str | dict = final_content
        if output_schema and final_content:
            try:
                output = json.loads(final_content)
            except json.JSONDecodeError as e:
                # Agent didn't return valid JSON for the schema — return raw content
                logger.debug(f"final agent output is not JSON, returning raw string: {e}")

        return {
            "output": output,
            "iterations_used": iterations_used,
            "tokens_used": tokens_used,
            "status": status,
            "llm_model": model,
        }

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

        from src.services.execution.service import execute_tool

        response = await execute_tool(
            workflow_id=str(workflow_id),
            workflow_name=tool_call.name,
            parameters=tool_call.arguments or {},
            user_id=SYSTEM_USER_ID,
            user_email=SYSTEM_USER_EMAIL,
            user_name=agent.name,
            org_id=str(agent.organization_id) if agent.organization_id else None,
            is_platform_admin=False,
            is_agent=True,
        )

        if response.status.value != "Success":
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
        """Execute knowledge search using the agent's configured namespaces."""
        try:
            from src.repositories.knowledge import KnowledgeRepository
            from src.services.embeddings import get_embedding_client

            query = tool_call.arguments.get("query", "")
            limit = tool_call.arguments.get("limit", 5)

            if not query:
                return "No query provided for knowledge search"

            namespaces = agent.knowledge_sources
            if not namespaces:
                return "No knowledge sources configured for this agent"

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
                    limit=limit,
                    fallback=True,
                )

            if not results:
                return "No relevant knowledge found."

            # Format results
            search_results = [
                {
                    "content": doc.content,
                    "namespace": doc.namespace,
                    "score": round(doc.score, 4) if doc.score else None,
                    "key": doc.key,
                    "metadata": doc.metadata,
                }
                for doc in results
            ]
            return json.dumps({"documents": search_results, "count": len(search_results)})

        except Exception as e:
            logger.error(f"Knowledge search failed: {e}", exc_info=True)
            raise ToolError(f"Knowledge search error: {e}") from e

    async def _execute_delegation(self, tool_call: ToolCallRequest, agent: Agent) -> str:
        """Execute delegation to another agent (recursive autonomous run)."""
        # Check cancellation before starting potentially long delegation
        if await self._check_cancelled(self._current_run_id):
            raise ToolError("Agent run was cancelled")

        if self._delegation_depth >= MAX_DELEGATION_DEPTH:
            logger.warning(f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) exceeded for {tool_call.name}")
            raise ToolError(f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) exceeded — cannot delegate further.")

        task = tool_call.arguments.get("task", "")

        target_agent = find_delegated_agent(agent, tool_call.name)
        if not target_agent:
            raise ToolError(f"Delegation target for '{tool_call.name}' not found.")

        logger.info(
            f"Agent '{agent.name}' delegating to '{target_agent.name}' "
            f"(depth={self._delegation_depth + 1}/{MAX_DELEGATION_DEPTH})"
        )

        # Brief DB session: re-fetch child agent with relationships + create sub-run record
        sub_run_id = str(uuid4())
        async with self._session_factory() as db:
            result = await db.execute(
                select(Agent)
                .options(selectinload(Agent.tools), selectinload(Agent.delegated_agents))
                .where(Agent.id == target_agent.id)
            )
            target_agent = result.scalar_one()

            # Create a child AgentRun so steps and AI usage are properly tracked
            sub_run = AgentRun(
                id=UUID(sub_run_id),
                agent_id=target_agent.id,
                trigger_type="delegation",
                trigger_source=f"agent:{agent.name}",
                input={"task": task, "_delegated_from": agent.name},
                status="running",
                org_id=agent.organization_id,
                parent_run_id=UUID(self._current_run_id),
                budget_max_iterations=target_agent.max_iterations,
                budget_max_tokens=target_agent.max_token_budget,
                started_at=datetime.now(timezone.utc),
            )
            db.add(sub_run)
            await db.commit()

        # Store for the caller to include in the tool_result step
        self._last_delegation_run_id = sub_run_id

        # Recursive run with the delegated agent (child gets its own session factory)
        sub_executor = AutonomousAgentExecutor(
            self._session_factory,
            redis_client=self.redis_client,
            _delegation_depth=self._delegation_depth + 1,
        )
        sub_start = time.time()
        try:
            sub_result = await asyncio.wait_for(
                sub_executor.run(
                    agent=target_agent,
                    input_data={"task": task, "_delegated_from": agent.name},
                    run_id=sub_run_id,
                ),
                timeout=DELEGATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.time() - sub_start) * 1000)
            async with self._session_factory() as db:
                sub_run_obj = await db.get(AgentRun, UUID(sub_run_id))
                if sub_run_obj:
                    sub_run_obj.status = "failed"
                    sub_run_obj.error = f"Timed out after {DELEGATION_TIMEOUT_SECONDS}s"
                    sub_run_obj.duration_ms = duration_ms
                    sub_run_obj.completed_at = datetime.now(timezone.utc)
                    await db.commit()
            logger.error(
                f"Delegation to '{target_agent.name}' timed out after {DELEGATION_TIMEOUT_SECONDS}s"
            )
            raise ToolError(f"Delegation to {target_agent.name} timed out after {DELEGATION_TIMEOUT_SECONDS}s")

        # Update sub-run record with results (brief DB session)
        duration_ms = int((time.time() - sub_start) * 1000)
        async with self._session_factory() as db:
            sub_run_obj = await db.get(AgentRun, UUID(sub_run_id))
            if sub_run_obj:
                sub_run_obj.status = sub_result.get("status", "completed")
                output = sub_result.get("output")
                sub_run_obj.output = output if isinstance(output, dict) else {"text": output}
                sub_run_obj.iterations_used = sub_result.get("iterations_used", 0)
                sub_run_obj.tokens_used = sub_result.get("tokens_used", 0)
                sub_run_obj.llm_model = sub_result.get("llm_model")
                sub_run_obj.duration_ms = duration_ms
                sub_run_obj.completed_at = datetime.now(timezone.utc)
                if sub_result.get("error"):
                    sub_run_obj.error = sub_result["error"]

                # Flush child executor's buffered steps in the same transaction
                await sub_executor.flush_to_db(db)
                await db.commit()

        logger.info(
            f"Delegation to '{target_agent.name}' completed with status={sub_result.get('status')}"
        )

        return str(sub_result.get("output", "Delegation completed with no output."))

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
                    user_id=SYSTEM_USER_ID,
                    org_id=str(agent.organization_id) if agent.organization_id else None,
                    is_platform_admin=False,
                    user_email=SYSTEM_USER_EMAIL,
                    user_name=agent.name,
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
        """Check if this agent run has been flagged for cancellation via Redis."""
        if not self.redis_client:
            return False
        try:
            key = f"bifrost:agent_run:{run_id}:cancel"
            result = await self.redis_client.get(key)
            return result is not None
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
