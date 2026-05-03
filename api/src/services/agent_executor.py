"""
Agent Executor Service

Handles the chat completion loop for AI agents, including:
- Message history management
- Tool execution via workflow runner
- Streaming responses
- Token usage tracking
- @mention agent switching
- AI-based message routing
- Agent delegation
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.models.contracts.agents import (
    AgentSwitch,
    ChatStreamChunk,
    ContextWarning,
    ToolCall,
    ToolProgress,
    ToolResult,
)
from src.models.enums import MessageRole
from src.models.orm import Agent, Conversation, Message, Workflow
from src.services.llm import (
    LLMMessage,
    ToolCallRequest,
    ToolDefinition,
    get_llm_client,
)
from src.services.execution.agent_helpers import (
    find_delegated_agent,
    parse_mcp_tool_name,
    resolve_agent_tools,
)
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.mcp_client import dispatch as mcp_dispatch
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)

logger = logging.getLogger(__name__)


def _serialize_for_json(value: Any) -> str:
    """Serialize a value to JSON string, handling Pydantic models.

    Uses pydantic_core for robust serialization that handles nested models,
    falling back to str() for unknown types.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import pydantic_core

    return pydantic_core.to_json(value, fallback=str).decode()


# Maximum tool call iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 10

# Context window management thresholds
# Claude models have ~200K context, we use conservative limits
CONTEXT_MAX_TOKENS = 120_000  # Prune when exceeding this
CONTEXT_WARNING_TOKENS = 100_000  # Warn when approaching this
CONTEXT_KEEP_RECENT = 20  # Keep this many recent messages when pruning
TOOL_OUTPUT_PROTECT_TOKENS = 10_000  # Protect this many tokens of recent tool outputs

# Fallback system prompt (used if no config set)
FALLBACK_SYSTEM_PROMPT = """You are a helpful AI assistant. You can help users with a variety of tasks including answering questions, providing information, and having general conversations.

Be concise, accurate, and helpful in your responses."""


class AgentExecutor:
    """
    Executes agent conversations with tool calling support.

    Manages the loop between:
    1. User message
    2. LLM completion (may request tool calls)
    3. Tool execution
    4. LLM completion with tool results
    5. Final response
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._tool_workflow_id_map: dict[str, UUID] = {}  # normalized tool name → workflow UUID

    @asynccontextmanager
    async def _db(self):
        """Short-lived DB session for discrete operations."""
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def _switch_agent(
        self,
        conversation: Conversation,
        new_agent: Agent,
        reason: str,
    ) -> AsyncIterator[ChatStreamChunk]:
        """
        Centralized agent switching with all rule checks.

        All agent switching paths (@mention, AI routing, etc.) should funnel
        through this method to ensure consistent behavior and rule enforcement.

        Args:
            conversation: The conversation to update
            new_agent: The agent to switch to
            reason: Why the switch happened ("@mention", "routed", etc.)

        Yields:
            - agent_switch event (always)
        """
        # 1. Emit agent switch event
        yield ChatStreamChunk(
            type="agent_switch",
            agent_switch=AgentSwitch(
                agent_id=str(new_agent.id),
                agent_name=new_agent.name,
                reason=reason,
            ),
        )

        # 2. Persist to conversation
        async with self._db() as session:
            conv = await session.get(Conversation, conversation.id)
            if conv:
                conv.agent_id = new_agent.id
        # Update in-memory object too so caller sees the change
        conversation.agent_id = new_agent.id

    async def chat(
        self,
        agent: Agent | None,
        conversation: Conversation,
        user_message: str,
        *,
        stream: bool = True,
        enable_routing: bool = True,
        local_id: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """
        Process a user message and generate a response.

        This is a streaming generator that yields ChatStreamChunk objects
        as the response is generated.

        Args:
            agent: The agent handling this conversation (None for agentless chat)
            conversation: The conversation context
            user_message: The user's message text
            stream: Whether to stream the response (default True)
            enable_routing: Whether to enable @mention and AI routing (default True)

        Yields:
            ChatStreamChunk objects with response content, tool calls, etc.
        """
        from src.services.agent_router import AgentRouter

        start_time = time.time()
        router = AgentRouter(self._session_factory)

        try:
            # 1. Check for @mention agent switching
            if enable_routing:
                mentioned_agent = await router.parse_mention(user_message)
                if mentioned_agent:
                    # Strip @mention from message for cleaner processing
                    user_message = router.strip_mention(user_message)
                    # Switch to mentioned agent (handles events and persistence)
                    async for chunk in self._switch_agent(conversation, mentioned_agent, "@mention"):
                        yield chunk
                    agent = mentioned_agent

            # 2. AI-based routing for agentless chat (first message only)
            if enable_routing and agent is None:
                # Check if this is the first user message in the conversation
                is_first_message = await self._is_first_user_message(conversation.id)
                if is_first_message:
                    routed_agent = await router.route_message(
                        user_message
                    )
                    if routed_agent:
                        # Switch to routed agent (handles events and persistence)
                        async for chunk in self._switch_agent(conversation, routed_agent, "routed"):
                            yield chunk
                        agent = routed_agent

            # 3. Save user message
            user_msg = await self._save_message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=user_message,
                local_id=local_id,
            )

            # 3b. Generate assistant message ID upfront and send message_start
            assistant_message_id = uuid4()
            yield ChatStreamChunk(
                type="message_start",
                user_message_id=str(user_msg.id),
                assistant_message_id=str(assistant_message_id),
                local_id=local_id,
            )

            # 4. Get tool definitions for this agent (empty if agentless)
            #
            # ``caller_user_id`` propagates here so MCP tools that bind to
            # per-user OAuth can be filtered correctly. For chat we always
            # have a user — the conversation owner. The caller threads
            # all the way through to ``mcp_dispatch.invoke`` so the
            # auth-resolution layer makes the user-vs-service decision
            # exactly once per call.
            caller_user_id: UUID | None = conversation.user_id
            tool_definitions = (
                await self._get_agent_tools(agent, caller_user_id=caller_user_id)
                if agent
                else []
            )
            logger.info(f"Agent '{agent.name if agent else 'None'}' has {len(tool_definitions)} tool definitions")
            if tool_definitions:
                logger.debug(f"Tools: {[t.name for t in tool_definitions]}")

            # 4b. Delegation tools are now included by resolve_agent_tools

            # 5. Build message history and fix any corrupted ordering
            messages = await self._build_message_history(agent, conversation)
            messages = self._fix_interleaved_messages(messages)
            messages = self._fix_dangling_tool_calls(messages)

            # 5b. Enhance system prompt with tool-use instructions if tools available
            if tool_definitions and messages and messages[0].role == "system":
                tool_names = [t.name for t in tool_definitions]
                tool_instruction = f"""

You have access to the following tools: {', '.join(tool_names)}

IMPORTANT: When the user's request can be fulfilled using one of your tools, you MUST call the tool immediately. Do not describe what you would do or say "Let me..." - instead, actually invoke the tool to perform the action. Only respond with text if you need clarification or if no tool is applicable."""
                messages[0] = LLMMessage(
                    role="system",
                    content=(messages[0].content or "") + tool_instruction,
                )

            # 4. Get LLM client
            async with self._db() as session:
                llm_client = await get_llm_client(session)

            # 5a. Check context size and prune if needed
            estimated_tokens = self._estimate_tokens(messages)

            if estimated_tokens > CONTEXT_WARNING_TOKENS:
                if estimated_tokens > CONTEXT_MAX_TOKENS:
                    # Prune and notify
                    messages, original_tokens = await self._prune_context(
                        messages, llm_client
                    )
                    new_tokens = self._estimate_tokens(messages)
                    yield ChatStreamChunk(
                        type="context_warning",
                        context_warning=ContextWarning(
                            current_tokens=original_tokens,
                            max_tokens=CONTEXT_MAX_TOKENS,
                            action="compacted",
                            message=(
                                f"Conversation history was summarized to stay within "
                                f"context limits. Original: ~{original_tokens:,} tokens, "
                                f"now: ~{new_tokens:,} tokens."
                            ),
                        ),
                    )
                else:
                    # Just warn - approaching limit
                    yield ChatStreamChunk(
                        type="context_warning",
                        context_warning=ContextWarning(
                            current_tokens=estimated_tokens,
                            max_tokens=CONTEXT_MAX_TOKENS,
                            action="warning",
                            message=(
                                f"Approaching context limit (~{estimated_tokens:,} of "
                                f"{CONTEXT_MAX_TOKENS:,} tokens). Consider starting a "
                                f"new conversation soon."
                            ),
                        ),
                    )

            # 5. Run completion loop with tool calling
            iteration = 0
            final_content = ""
            final_tool_calls: list[ToolCall] = []
            total_input_tokens = 0
            total_output_tokens = 0

            # Extract agent LLM overrides
            model_override = agent.llm_model if agent else None
            max_tokens_override = agent.llm_max_tokens if agent else None

            # Track tool_call IDs across iterations to handle providers
            # (e.g. Minimax) that reuse the same IDs across turns.
            seen_tc_ids: set[str] = set()
            # Also collect IDs from message history (loaded from DB)
            for m in messages:
                if m.role == "assistant" and m.tool_calls:
                    for tc in m.tool_calls:
                        seen_tc_ids.add(tc.id)

            while iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                # Stream LLM response
                collected_content = ""
                collected_tool_calls: list[ToolCallRequest] = []
                chunk_input_tokens = 0
                chunk_output_tokens = 0

                async for chunk in llm_client.stream(
                    messages=messages,
                    tools=tool_definitions if tool_definitions else None,
                    model=model_override,
                    max_tokens=max_tokens_override,
                ):
                    if chunk.type == "delta" and chunk.content:
                        collected_content += chunk.content
                        if stream:
                            yield ChatStreamChunk(
                                type="delta",
                                content=chunk.content,
                            )

                    elif chunk.type == "tool_call" and chunk.tool_call:
                        collected_tool_calls.append(chunk.tool_call)
                        if stream:
                            yield ChatStreamChunk(
                                type="tool_call",
                                tool_call=ToolCall(
                                    id=chunk.tool_call.id,
                                    name=chunk.tool_call.name,
                                    arguments=chunk.tool_call.arguments,
                                ),
                            )

                    elif chunk.type == "done":
                        chunk_input_tokens = chunk.input_tokens or 0
                        chunk_output_tokens = chunk.output_tokens or 0
                        total_input_tokens += chunk_input_tokens
                        total_output_tokens += chunk_output_tokens

                    elif chunk.type == "error":
                        yield ChatStreamChunk(
                            type="error",
                            error=chunk.error,
                        )
                        return

                # If no tool calls, we're done
                if not collected_tool_calls:
                    final_content = collected_content
                    break

                # Deduplicate tool_call IDs — some providers (e.g. Minimax)
                # reuse the same IDs across turns within a single request.
                for tc in collected_tool_calls:
                    if tc.id in seen_tc_ids:
                        old_id = tc.id
                        tc.id = f"{tc.id}_iter{iteration}"
                        logger.debug("Remapped duplicate tool_call ID %s -> %s", old_id, tc.id)
                    seen_tc_ids.add(tc.id)

                # Save text content as its own message BEFORE tools (no tool_calls embedded)
                # This ensures text appears before tools in timeline after refresh
                if collected_content:
                    text_msg = await self._save_message(
                        conversation_id=conversation.id,
                        role=MessageRole.ASSISTANT,
                        content=collected_content,
                        token_count_input=chunk_input_tokens,
                        token_count_output=chunk_output_tokens,
                        model=llm_client.model_name,
                    )
                    if stream:
                        yield ChatStreamChunk(
                            type="assistant_message_end",
                            message_id=str(text_msg.id),
                        )

                # Add assistant message to history (text + tool_calls for LLM context)
                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=collected_content if collected_content else None,
                        tool_calls=collected_tool_calls,
                    )
                )

                # Execute tools and add results to history
                for tc in collected_tool_calls:
                    tool_call = ToolCall(
                        id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                    )
                    final_tool_calls.append(tool_call)

                    # Generate execution_id for this tool call (for log streaming)
                    execution_id = str(uuid4())

                    # Save TOOL_CALL message with state "running"
                    tool_call_msg = await self._save_message(
                        conversation_id=conversation.id,
                        role=MessageRole.TOOL_CALL,
                        tool_name=tc.name,
                        tool_input=tc.arguments,
                        tool_state="running",
                        tool_call_id=tc.id,
                        execution_id=execution_id,
                    )

                    # Emit tool_call event with message ID
                    if stream:
                        yield ChatStreamChunk(
                            type="tool_call",
                            tool_call=tool_call,
                            execution_id=execution_id,
                            message_id=str(tool_call_msg.id),
                        )

                    # Emit running status
                    if stream:
                        yield ChatStreamChunk(
                            type="tool_progress",
                            tool_progress=ToolProgress(
                                tool_call_id=tc.id,
                                execution_id=execution_id,
                                status="running",
                            ),
                        )

                    # Execute the tool with pre-generated execution_id
                    tool_result = await self._execute_tool(
                        tc,
                        agent,
                        conversation,
                        execution_id=execution_id,
                        caller_user_id=caller_user_id,
                    )

                    # Update TOOL_CALL message with result and state
                    await self._update_tool_call_message(
                        message_id=tool_call_msg.id,
                        tool_state="completed" if not tool_result.error else "error",
                        tool_result=tool_result.result if not tool_result.error else {"error": tool_result.error},
                        duration_ms=tool_result.duration_ms,
                    )

                    if stream:
                        yield ChatStreamChunk(
                            type="tool_result",
                            tool_result=tool_result,
                            message_id=str(tool_call_msg.id),
                        )

                    # Still save TOOL message for Anthropic API compatibility (history reconstruction)
                    await self._save_message(
                        conversation_id=conversation.id,
                        role=MessageRole.TOOL,
                        content=_serialize_for_json(tool_result.result) if tool_result.result else tool_result.error,
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        execution_id=execution_id,
                        duration_ms=tool_result.duration_ms,
                    )

                    # Add tool result to message history for LLM
                    messages.append(
                        LLMMessage(
                            role="tool",
                            content=_serialize_for_json(tool_result.result) if tool_result.result else tool_result.error,
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                        )
                    )

                # Continue loop to get LLM response with tool results

            # 6. Save final assistant message (using pre-generated ID)
            duration_ms = int((time.time() - start_time) * 1000)
            assistant_msg = await self._save_message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=final_content,
                token_count_input=total_input_tokens,
                token_count_output=total_output_tokens,
                model=llm_client.model_name,
                duration_ms=duration_ms,
                message_id=assistant_message_id,
            )

            # 6b. Record AI usage
            try:
                await self._record_ai_usage(
                    provider=llm_client.provider_name,
                    model=llm_client.model_name,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    duration_ms=duration_ms,
                    conversation_id=conversation.id,
                    message_id=assistant_msg.id,
                    organization_id=agent.organization_id if agent else None,
                    user_id=conversation.user_id,
                )
            except Exception as e:
                logger.warning(f"Failed to record AI usage: {e}")

            # 7. Yield done chunk with final content (for non-streaming mode)
            yield ChatStreamChunk(
                type="done",
                content=final_content if final_content else None,
                message_id=str(assistant_msg.id),
                token_count_input=total_input_tokens,
                token_count_output=total_output_tokens,
                duration_ms=duration_ms,
            )

        except Exception as e:
            logger.error(f"Agent execution error: {e}", exc_info=True)
            yield ChatStreamChunk(
                type="error",
                error=str(e),
            )

    async def _get_agent_tools(
        self,
        agent: Agent,
        *,
        caller_user_id: UUID | None = None,
    ) -> list[ToolDefinition]:
        """
        Get tool definitions for an agent from its assigned tools.

        Delegates to the shared resolve_agent_tools helper which handles:
        1. System tools (unprefixed, e.g., "execute_workflow", "search_knowledge")
        2. Workflow tools (prefixed, e.g., "halopsa_list_tickets", "wf_add_comment")
        3. Delegation tools (e.g., "delegate_to_agent_name")
        4. External MCP tools (prefixed ``mcp__<connection_id>__<tool>``)

        When a workflow tool's normalized name collides with a system tool,
        the system tool wins and a warning is logged.
        """
        async with self._db() as session:
            tools, self._tool_workflow_id_map = await resolve_agent_tools(
                agent, session, caller_user_id=caller_user_id
            )
        return tools

    async def _notify_tool_conflicts(
        self,
        agent: Agent,
        conflicts: list[tuple[str, str, str]],
    ) -> None:
        """
        Create system notification for tool name conflicts.

        Args:
            agent: The agent with conflicting tools
            conflicts: List of (name, loser_source, winner_source) tuples
        """
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationCreate,
        )
        from src.services.notification_service import get_notification_service

        try:
            conflict_msgs = [
                f"'{name}' ({loser}) hidden by {winner}"
                for name, loser, winner in conflicts
            ]
            description = "; ".join(conflict_msgs)

            # Truncate if too long (max 500 chars per NotificationCreate)
            if len(description) > 480:
                description = description[:477] + "..."

            notification_service = get_notification_service()
            await notification_service.create_notification(
                user_id="system",
                request=NotificationCreate(
                    category=NotificationCategory.SYSTEM,
                    title=f"Tool conflicts in agent '{agent.name}'",
                    description=description,
                    metadata={
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "conflicts": [
                            {"name": name, "loser": loser, "winner": winner}
                            for name, loser, winner in conflicts
                        ],
                    },
                ),
                for_admins=True,  # Notify platform admins
            )
            logger.info(f"Created notification for {len(conflicts)} tool conflicts in agent '{agent.name}'")
        except Exception as e:
            # Don't fail the agent execution just because notification failed
            logger.warning(f"Failed to create tool conflict notification: {e}")

    async def _build_message_history(
        self, agent: Agent | None, conversation: Conversation
    ) -> list[LLMMessage]:
        """Build the message history for LLM completion."""
        messages: list[LLMMessage] = []

        # Add system prompt (use agent's prompt or configurable default for agentless chat)
        if agent:
            from src.services.execution.agent_helpers import build_agent_system_prompt
            system_prompt = build_agent_system_prompt(agent, execution_context={"mode": "chat"})
        else:
            system_prompt = await self._get_default_system_prompt()
        messages.append(
            LLMMessage(
                role="system",
                content=system_prompt,
            )
        )

        # Get conversation messages in order
        async with self._db() as session:
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence)
            )
            db_messages = result.scalars().all()

        # Track seen tool_call IDs to handle providers (e.g. Minimax) that
        # reuse the same IDs across turns. When a collision is detected, remap
        # to a unique ID and apply the same remap to the corresponding tool result.
        seen_tc_ids: dict[str, int] = {}  # tc_id -> count of times seen
        tc_id_remap: dict[tuple[int, str], str] = {}  # (sequence, original_id) -> new_id

        for msg in db_messages:
            if msg.role == MessageRole.USER:
                messages.append(
                    LLMMessage(
                        role="user",
                        content=msg.content,
                    )
                )
            elif msg.role == MessageRole.ASSISTANT:
                tool_calls = None
                if msg.tool_calls:
                    tool_calls = []
                    for tc in msg.tool_calls:
                        tc_id = tc["id"]
                        if tc_id in seen_tc_ids:
                            seen_tc_ids[tc_id] += 1
                            new_id = f"{tc_id}_t{seen_tc_ids[tc_id]}"
                            tc_id_remap[(msg.sequence, tc["id"])] = new_id
                            tc_id = new_id
                        else:
                            seen_tc_ids[tc_id] = 1
                        tool_calls.append(
                            ToolCallRequest(
                                id=tc_id,
                                name=tc["name"],
                                arguments=tc.get("arguments", {}),
                            )
                        )
                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=msg.content,
                        tool_calls=tool_calls,
                    )
                )
            elif msg.role == MessageRole.TOOL_CALL:
                # TOOL_CALL rows are stored separately from the assistant message.
                # Attach them as tool_calls on the preceding assistant LLMMessage
                # so the LLM sees the correct assistant→tool_use→tool_result sequence.
                tc_id = msg.tool_call_id or ""
                if tc_id in seen_tc_ids:
                    seen_tc_ids[tc_id] += 1
                    new_id = f"{tc_id}_t{seen_tc_ids[tc_id]}"
                    tc_id_remap[(msg.sequence, msg.tool_call_id or "")] = new_id
                    tc_id = new_id
                else:
                    seen_tc_ids[tc_id] = 1
                tc_request = ToolCallRequest(
                    id=tc_id,
                    name=msg.tool_name or "",
                    arguments=msg.tool_input if isinstance(msg.tool_input, dict) else {},
                )
                # Find the last assistant message (may have tool results between)
                last_assistant_idx = None
                for idx in range(len(messages) - 1, -1, -1):
                    if messages[idx].role == "assistant":
                        last_assistant_idx = idx
                        break
                if last_assistant_idx is not None:
                    assistant_msg = messages[last_assistant_idx]
                    if assistant_msg.tool_calls is None:
                        assistant_msg.tool_calls = []
                    assistant_msg.tool_calls.append(tc_request)
                else:
                    # No preceding assistant message — create a minimal one
                    messages.append(
                        LLMMessage(
                            role="assistant",
                            content=None,
                            tool_calls=[tc_request],
                        )
                    )
            elif msg.role == MessageRole.TOOL:
                # Apply remapping if the preceding tool_call had its ID changed
                tc_id = msg.tool_call_id
                if tc_id:
                    best_seq = -1
                    for (seq, orig_id), new_id in tc_id_remap.items():
                        if orig_id == tc_id and seq < msg.sequence and seq > best_seq:
                            best_seq = seq
                            tc_id = new_id
                messages.append(
                    LLMMessage(
                        role="tool",
                        content=msg.content,
                        tool_call_id=tc_id,
                        tool_name=msg.tool_name,
                    )
                )
            elif msg.role == MessageRole.SYSTEM:
                # Skip additional system messages (we already have the prompt)
                pass

        return messages

    def _estimate_tokens(self, messages: list[LLMMessage]) -> int:
        """
        Estimate token count for a list of messages.

        Uses a simple heuristic of ~4 characters per token, which is
        reasonably accurate for English text and provides a conservative
        estimate for context management purposes.
        """
        total = 0
        for msg in messages:
            if msg.content:
                total += len(msg.content) // 4
            if msg.tool_calls:
                # Estimate tokens for tool call JSON
                tool_json = json.dumps([
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in msg.tool_calls
                ])
                total += len(tool_json) // 4
        return total

    def _find_turn_boundaries(self, messages: list[LLMMessage]) -> list[int]:
        """Find indices where new conversation turns start.

        A turn is either a single message, or an assistant message with tool_calls
        grouped together with the following tool result messages.
        """
        boundaries = []
        i = 0
        while i < len(messages):
            boundaries.append(i)
            if messages[i].role == "assistant" and messages[i].tool_calls:
                i += 1
                while i < len(messages) and messages[i].role == "tool":
                    i += 1
            else:
                i += 1
        return boundaries

    def _compact_tool_outputs(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        """Replace old tool result content with placeholder, keeping recent ones.

        Walks backward through messages, protecting the most recent tool outputs
        up to TOOL_OUTPUT_PROTECT_TOKENS. Older large tool outputs get replaced
        with a short placeholder. This preserves all message structure (tool_use/
        tool_result pairs stay intact) while reducing token count.
        """
        protected_tokens = 0
        protect_indices: set[int] = set()

        for i in range(len(messages) - 1, -1, -1):
            content = messages[i].content
            if messages[i].role == "tool" and content:
                tokens = len(content) // 4
                if protected_tokens + tokens <= TOOL_OUTPUT_PROTECT_TOKENS:
                    protected_tokens += tokens
                    protect_indices.add(i)

        result = []
        for i, msg in enumerate(messages):
            if (
                msg.role == "tool"
                and i not in protect_indices
                and msg.content
                and len(msg.content) > 200
            ):
                result.append(
                    LLMMessage(
                        role=msg.role,
                        content="[Tool output cleared for context management]",
                        tool_call_id=msg.tool_call_id,
                        tool_name=msg.tool_name,
                    )
                )
            else:
                result.append(msg)
        return result

    def _fix_interleaved_messages(
        self, messages: list[LLMMessage]
    ) -> list[LLMMessage]:
        """Fix user messages wedged between tool_use and tool_result.

        When a user sends a message while a tool call is executing, the DB can
        end up with: assistant(tool_use) → user → tool(result).  The Anthropic
        API requires tool_result to immediately follow its tool_use.  This
        method moves such user messages after the tool_result block.
        """
        result = list(messages)
        i = 0
        while i < len(result):
            msg = result[i]
            if msg.role == "assistant" and msg.tool_calls:
                expected_ids = {tc.id for tc in msg.tool_calls}
                # Scan ahead: collect interleaved non-tool messages and tool results
                j = i + 1
                tool_results: list[LLMMessage] = []
                displaced: list[LLMMessage] = []
                while j < len(result):
                    tcid = result[j].tool_call_id
                    if result[j].role == "tool" and tcid and tcid in expected_ids:
                        tool_results.append(result[j])
                        expected_ids.discard(tcid)
                    elif not expected_ids:
                        # All tool results found, stop scanning
                        break
                    else:
                        displaced.append(result[j])
                    j += 1
                if displaced and tool_results:
                    # Rewrite: assistant, tool_results, then displaced messages
                    result[i + 1 : j] = tool_results + displaced
            i += 1
        return result

    def _fix_dangling_tool_calls(
        self, messages: list[LLMMessage]
    ) -> list[LLMMessage]:
        """Ensure every tool_use has a corresponding tool_result.

        If an assistant message has tool_calls but there are no (or incomplete)
        tool result messages following it, inject placeholder results so the
        API doesn't reject the request.
        """
        result = list(messages)
        i = 0
        while i < len(result):
            msg = result[i]
            if msg.role == "assistant" and msg.tool_calls:
                expected_ids = {tc.id for tc in msg.tool_calls}
                j = i + 1
                found_ids: set[str] = set()
                while j < len(result) and result[j].role == "tool":
                    tool_call_id_val = result[j].tool_call_id
                    if tool_call_id_val:
                        found_ids.add(tool_call_id_val)
                    j += 1
                missing = expected_ids - found_ids
                for tool_call_id in missing:
                    tc = next(
                        tc for tc in msg.tool_calls if tc.id == tool_call_id
                    )
                    result.insert(
                        j,
                        LLMMessage(
                            role="tool",
                            content="[Tool execution was interrupted]",
                            tool_call_id=tool_call_id,
                            tool_name=tc.name,
                        ),
                    )
                    j += 1
            i += 1
        return result

    async def _summarize_messages(
        self,
        messages: list[LLMMessage],
        llm_client: Any,
    ) -> str:
        """
        Summarize a batch of messages into a concise context string.

        Used when pruning context to preserve important information
        from older messages that are being removed.
        """
        # Build a text representation of the messages to summarize
        message_texts = []
        for msg in messages:
            if msg.content:
                role_label = msg.role.upper()
                message_texts.append(f"{role_label}: {msg.content}")
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    message_texts.append(f"TOOL_CALL: {tc.name}({json.dumps(tc.arguments)})")
            if msg.role == "tool" and msg.tool_name:
                message_texts.append(f"TOOL_RESULT ({msg.tool_name}): {msg.content}")

        conversation_text = "\n\n".join(message_texts)

        summary_prompt = [
            LLMMessage(
                role="system",
                content=(
                    "Summarize this conversation history concisely. "
                    "Include key facts, decisions made, and important outcomes. "
                    "Focus on information that would be useful context for continuing "
                    "the conversation. Keep your summary under 1000 words."
                ),
            ),
            LLMMessage(
                role="user",
                content=conversation_text,
            ),
        ]

        response = await llm_client.complete(messages=summary_prompt)
        return response.content or ""

    async def _prune_context(
        self,
        messages: list[LLMMessage],
        llm_client: Any,
        keep_recent: int = CONTEXT_KEEP_RECENT,
    ) -> tuple[list[LLMMessage], int]:
        """
        Prune messages if context is too large, preserving tool_use/tool_result pairs.

        Two-phase strategy:
        1. **Tool output compaction**: Replace old, large tool outputs with short
           placeholders while keeping message structure intact (pairs stay paired).
        2. **Turn-boundary summarization**: If still over limit, summarize older
           messages but only cut at turn boundaries so tool_use+tool_result groups
           are never split.

        Also fixes dangling tool_calls (tool_use without matching tool_result)
        before returning.

        Args:
            messages: Full message history
            llm_client: LLM client for summarization
            keep_recent: Number of recent messages to preserve

        Returns:
            Tuple of (pruned_messages, original_token_estimate)
        """
        original_tokens = self._estimate_tokens(messages)

        if original_tokens <= CONTEXT_MAX_TOKENS:
            return messages, original_tokens

        logger.info(
            f"Context pruning triggered: {original_tokens:,} tokens exceeds "
            f"{CONTEXT_MAX_TOKENS:,} limit"
        )

        # Phase 1: Compact old tool outputs (preserves all message structure)
        messages = self._compact_tool_outputs(messages)
        compacted_tokens = self._estimate_tokens(messages)
        if compacted_tokens <= CONTEXT_MAX_TOKENS:
            logger.info(
                f"Tool output compaction sufficient: {original_tokens:,} -> "
                f"{compacted_tokens:,} tokens"
            )
            messages = self._fix_dangling_tool_calls(messages)
            return messages, original_tokens

        # Phase 2: Summarize middle messages at turn boundaries
        boundaries = self._find_turn_boundaries(messages)

        # Keep system prompt (always first)
        system_msg = messages[0]

        # Find first user message
        first_user_idx = next(
            (i for i, m in enumerate(messages) if m.role == "user"),
            None,
        )
        first_user_msg = messages[first_user_idx] if first_user_idx else None

        # Determine where to start summarizing
        if first_user_idx is not None:
            middle_start = first_user_idx + 1
        else:
            middle_start = 1  # After system message

        # Snap the cut point to the nearest turn boundary
        target_cut = len(messages) - keep_recent
        cut_idx = boundaries[0]
        for b in boundaries:
            if b <= target_cut:
                cut_idx = b
            else:
                break

        # Ensure cut_idx is at least past middle_start
        if cut_idx < middle_start:
            cut_idx = middle_start

        middle_end = cut_idx

        if middle_end <= middle_start:
            logger.info("Not enough middle messages to summarize, keeping original")
            messages = self._fix_dangling_tool_calls(messages)
            return messages, original_tokens

        to_summarize = messages[middle_start:middle_end]
        recent_messages = messages[cut_idx:]
        logger.info(
            f"Summarizing {len(to_summarize)} messages from the middle of "
            f"conversation (turn-boundary cut at index {cut_idx})"
        )

        # Generate summary
        summary = await self._summarize_messages(to_summarize, llm_client)

        # Build pruned message list
        pruned: list[LLMMessage] = [system_msg]

        if first_user_msg:
            pruned.append(first_user_msg)

        # Add summary as a user context message
        pruned.append(
            LLMMessage(
                role="user",
                content=f"[Previous conversation summary]\n{summary}",
            )
        )

        # Add recent messages (guaranteed to start at a turn boundary)
        pruned.extend(recent_messages)

        # Fix any dangling tool calls in the final result
        pruned = self._fix_dangling_tool_calls(pruned)

        new_tokens = self._estimate_tokens(pruned)
        logger.info(
            f"Context pruned: {original_tokens:,} -> {new_tokens:,} tokens "
            f"({len(messages)} -> {len(pruned)} messages)"
        )

        return pruned, original_tokens

    async def _save_message(
        self,
        conversation_id: UUID,
        role: MessageRole,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        execution_id: str | None = None,
        token_count_input: int | None = None,
        token_count_output: int | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        message_id: UUID | None = None,
        # New fields for TOOL_CALL messages
        tool_state: str | None = None,
        tool_result: Any | None = None,
        tool_input: dict[str, Any] | None = None,
        # Client-generated ID for optimistic update reconciliation
        local_id: str | None = None,
    ) -> Message:
        """Save a message to the conversation."""
        msg_id = message_id if message_id else uuid4()

        async with self._db() as session:
            # Get next sequence number
            result = await session.execute(
                select(func.coalesce(func.max(Message.sequence), 0))
                .where(Message.conversation_id == conversation_id)
            )
            max_sequence = result.scalar() or 0
            next_sequence = max_sequence + 1

            message = Message(
                id=msg_id,
                conversation_id=conversation_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                execution_id=execution_id,
                token_count_input=token_count_input,
                token_count_output=token_count_output,
                model=model,
                duration_ms=duration_ms,
                sequence=next_sequence,
                # New fields for TOOL_CALL messages
                tool_state=tool_state,
                tool_result=tool_result,
                tool_input=tool_input,
                # Client-generated ID for optimistic update reconciliation
                local_id=local_id,
            )
            session.add(message)

            # Update conversation updated_at
            conversation_result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = conversation_result.scalar_one()
            conversation.updated_at = datetime.now(timezone.utc)
            # commit happens on context manager exit

        return message

    async def _update_tool_call_message(
        self,
        message_id: UUID,
        tool_state: str,
        tool_result: Any | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Update a TOOL_CALL message with execution result."""
        async with self._db() as session:
            result = await session.execute(
                select(Message).where(Message.id == message_id)
            )
            message = result.scalar_one()
            message.tool_state = tool_state
            message.tool_result = tool_result
            message.duration_ms = duration_ms
            # commit happens on context manager exit

    async def _execute_tool(
        self,
        tool_call: ToolCallRequest,
        agent: Agent | None = None,
        conversation: Conversation | None = None,
        execution_id: str | None = None,
        *,
        caller_user_id: UUID | None = None,
    ) -> ToolResult:
        """
        Execute a tool (workflow, delegation, system tool, knowledge search,
        or external MCP tool) and return the result.

        This integrates with the existing workflow execution system
        and handles agent delegation, system tools, built-in knowledge
        search, and remote MCP servers.
        """
        start_time = time.time()

        # Check if this is a knowledge search tool call
        if tool_call.name == "search_knowledge" and agent:
            return await self._execute_knowledge_search(tool_call, agent)

        # Check if this is a delegation tool call
        if tool_call.name.startswith("delegate_to_") and agent:
            return await self._execute_delegation(tool_call, agent)

        # Check if this is a system tool call
        if agent and tool_call.name in (agent.system_tools or []):
            return await self._execute_system_tool(tool_call, agent, conversation)

        # External MCP tools — namespaced ``mcp__<connection_id>__<tool>``.
        # MCP tools are routed BEFORE workflow tools because workflow lookup
        # would otherwise fall through to "tool not found" for an MCP name.
        mcp_route = parse_mcp_tool_name(tool_call.name)
        if mcp_route is not None:
            connection_id, remote_tool_name = mcp_route
            return await self._execute_mcp_tool(
                tool_call,
                connection_id=connection_id,
                remote_tool_name=remote_tool_name,
                caller_user_id=caller_user_id,
                start_time=start_time,
            )

        try:
            # Get the workflow for this tool — prefer ID lookup (handles normalized names)
            workflow_id = self._tool_workflow_id_map.get(tool_call.name)
            async with self._db() as session:
                if workflow_id:
                    result = await session.execute(
                        select(Workflow).where(Workflow.id == workflow_id)
                    )
                else:
                    # Fallback: try by name (for non-prefixed tools or edge cases)
                    result = await session.execute(
                        select(Workflow)
                        .where(Workflow.name == tool_call.name)
                        .where(Workflow.type == "tool")
                        .where(Workflow.is_active.is_(True))
                    )
                workflow = result.scalar_one_or_none()

            if not workflow:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error=f"Tool '{tool_call.name}' not found",
                    duration_ms=int((time.time() - start_time) * 1000),
                )

            # Get user info from conversation
            user = conversation.user if conversation else None

            # Execute the workflow via execution service
            from src.services.execution.service import execute_tool

            # Get org_id from agent (workflows are not org-scoped)
            org_id = str(agent.organization_id) if agent and agent.organization_id else None

            execution_response = await execute_tool(
                workflow_id=str(workflow.id),
                workflow_name=workflow.name,
                parameters=tool_call.arguments or {},
                user_id=str(user.id) if user else "system",
                user_email=user.email if user else "system@internal.gobifrost.com",
                user_name=user.name if user else "System",
                org_id=org_id,
                is_platform_admin=user.is_superuser if user else False,
                execution_id=execution_id,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            if execution_response.status.value == "Success":
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=execution_response.result,
                    error=None,
                    duration_ms=duration_ms,
                )
            else:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error=execution_response.error or "Unknown error",
                    duration_ms=duration_ms,
                )

        except Exception as e:
            logger.error(f"Tool execution error for {tool_call.name}: {e}", exc_info=True)
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _execute_mcp_tool(
        self,
        tool_call: ToolCallRequest,
        *,
        connection_id: UUID,
        remote_tool_name: str,
        caller_user_id: UUID | None,
        start_time: float,
    ) -> ToolResult:
        """Dispatch an external MCP tool call.

        This is the executor side of the Phase 2 ``mcp_client.dispatch.invoke``
        path. We load the connection (with its ``server`` and
        ``service_oauth_token`` relationships), call dispatch, and translate
        the structured errors into ``ToolResult`` envelopes the chat
        surface knows how to render.

        ``NeedsReauthError`` becomes a ``ToolResult`` with
        ``error_type='needs_reauth'`` and a ``metadata`` payload carrying
        ``reauth_url`` / ``connection_id`` / ``tool_name`` so the UI can
        render an inline reconnect button without re-querying the API.
        """
        from src.models.orm.external_mcp import MCPConnection, MCPServer

        try:
            async with self._db() as session:
                result = await session.execute(
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
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        result=None,
                        error=f"MCP connection {connection_id} not found",
                        duration_ms=int((time.time() - start_time) * 1000),
                    )

                envelope = await mcp_dispatch.invoke(
                    connection=connection,
                    tool_name=remote_tool_name,
                    arguments=tool_call.arguments or {},
                    caller_user_id=caller_user_id,
                    db=session,
                )

            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=envelope,
                error=None,
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except NeedsReauthError as exc:
            logger.info(
                "MCP tool %s on connection %s needs reauth",
                remote_tool_name,
                connection_id,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(exc),
                duration_ms=int((time.time() - start_time) * 1000),
                error_type="needs_reauth",
                metadata={
                    "reauth_url": exc.reauth_url,
                    "connection_id": str(exc.connection_id),
                    "tool_name": remote_tool_name,
                },
            )
        except MisconfigError as exc:
            logger.error(
                "MCP misconfig on connection %s tool %s: %s",
                connection_id,
                remote_tool_name,
                exc,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(exc),
                duration_ms=int((time.time() - start_time) * 1000),
                error_type="misconfig",
                metadata={"connection_id": str(connection_id)},
            )
        except ToolDispatchError as exc:
            logger.warning(
                "MCP dispatch error on connection %s tool %s: %s",
                connection_id,
                remote_tool_name,
                exc,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(exc),
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as exc:
            logger.error(
                "Unexpected MCP error on connection %s tool %s: %s",
                connection_id,
                remote_tool_name,
                exc,
                exc_info=True,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(exc),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _get_default_system_prompt(self) -> str:
        """
        Get the default system prompt from LLM config or use fallback.
        """
        from src.services.llm_config_service import LLMConfigService

        try:
            async with self._db() as session:
                config_service = LLMConfigService(session)
                config = await config_service.get_config()

            if config and config.default_system_prompt:
                return config.default_system_prompt
        except Exception as e:
            logger.warning(f"Failed to get default system prompt from config: {e}")

        return FALLBACK_SYSTEM_PROMPT

    async def _is_first_user_message(self, conversation_id: UUID) -> bool:
        """
        Check if this is the first user message in a conversation.
        Used to determine whether to apply AI routing.
        """
        async with self._db() as session:
            result = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
                .where(Message.role == MessageRole.USER)
            )
            count = result.scalar() or 0
        return count == 0

    async def _execute_knowledge_search(
        self,
        tool_call: ToolCallRequest,
        agent: Agent,
    ) -> ToolResult:
        """
        Execute a knowledge search using the agent's configured namespaces.

        This is a built-in tool that doesn't require a workflow.
        """
        start_time = time.time()

        try:
            from src.repositories.knowledge import KnowledgeRepository
            from src.services.embeddings import get_embedding_client

            # Get search parameters
            query = tool_call.arguments.get("query", "")
            limit = tool_call.arguments.get("limit", 5)

            if not query:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error="No query provided for knowledge search",
                    duration_ms=int((time.time() - start_time) * 1000),
                )

            # Get the agent's configured namespaces
            namespaces = agent.knowledge_sources
            if not namespaces:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error="No knowledge sources configured for this agent",
                    duration_ms=int((time.time() - start_time) * 1000),
                )

            # Generate query embedding
            async with self._db() as session:
                embedding_client = await get_embedding_client(session)
            query_embedding = await embedding_client.embed_single(query)

            # Search knowledge store
            async with self._db() as session:
                repo = KnowledgeRepository(
                    session, org_id=agent.organization_id, is_superuser=True
                )
                results = await repo.search(
                    query_embedding=query_embedding,
                    namespace=namespaces,
                    limit=limit,
                    fallback=True,  # Search org + global
                )

            duration_ms = int((time.time() - start_time) * 1000)

            # Format results for the agent
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

            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result={"documents": search_results, "count": len(search_results)},
                error=None,
                duration_ms=duration_ms,
            )

        except Exception as e:
            logger.error(f"Knowledge search error: {e}", exc_info=True)
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _execute_delegation(
        self,
        tool_call: ToolCallRequest,
        agent: Agent,
    ) -> ToolResult:
        """Execute a delegation to another agent via AutonomousAgentExecutor."""
        start_time = time.time()

        delegated_agent = find_delegated_agent(agent, tool_call.name)
        if not delegated_agent:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=f"Delegated agent not found: {tool_call.name}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        task = tool_call.arguments.get("task", "")
        if not task:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error="No task provided for delegation",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        try:
            from src.core.cache import get_shared_redis
            from src.services.execution.autonomous_agent_executor import DELEGATION_TIMEOUT_SECONDS

            logger.info(f"Agent '{agent.name}' delegating to '{delegated_agent.name}' via chat")

            # Re-fetch with relationships loaded — the parent's selectinload
            # doesn't transitively load the child agent's own relationships
            async with self._db() as session:
                result = await session.execute(
                    select(Agent)
                    .options(selectinload(Agent.tools), selectinload(Agent.delegated_agents))
                    .where(Agent.id == delegated_agent.id)
                )
                delegated_agent = result.scalar_one()

            redis_client = await get_shared_redis()
            sub_executor = AutonomousAgentExecutor(self._session_factory, redis_client=redis_client)
            try:
                sub_result = await asyncio.wait_for(
                    sub_executor.run(
                        agent=delegated_agent,
                        input_data={"task": task, "_delegated_from": agent.name},
                    ),
                    timeout=DELEGATION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Delegation to '{delegated_agent.name}' timed out after {DELEGATION_TIMEOUT_SECONDS}s"
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error=f"Delegation to {delegated_agent.name} timed out after {DELEGATION_TIMEOUT_SECONDS}s",
                    duration_ms=int((time.time() - start_time) * 1000),
                )

            duration_ms = int((time.time() - start_time) * 1000)
            output = sub_result.get("output", "Delegation completed with no output.")

            logger.info(
                f"Delegation to '{delegated_agent.name}' completed with status={sub_result.get('status')}"
            )

            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result={"response": str(output), "agent": delegated_agent.name},
                error=None if sub_result.get("status") != "failed" else sub_result.get("error"),
                duration_ms=duration_ms,
            )

        except Exception as e:
            logger.error(f"Delegation error for {tool_call.name}: {e}", exc_info=True)
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _execute_system_tool(
        self,
        tool_call: ToolCallRequest,
        agent: Agent,
        conversation: Conversation | None,
    ) -> ToolResult:
        """Execute a system tool and return the result."""
        from src.services.mcp_server.server import MCPContext, get_system_tool_function

        start_time = time.time()

        func = get_system_tool_function(tool_call.name)
        if not func:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=f"System tool '{tool_call.name}' not found",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        try:
            # Get user from conversation (same pattern as workflow tool execution)
            user = conversation.user if conversation else None

            # Create context from agent/conversation/user
            # session=None: system tools create their own short-lived sessions
            # via get_tool_db() fallback, avoiding long-lived connection holds
            context = MCPContext(
                user_id=str(user.id) if user else "",
                org_id=str(agent.organization_id) if agent.organization_id else None,
                is_platform_admin=user.is_superuser if user else False,
                user_email=user.email if user else "",
                user_name=user.name if user else "",
                session=None,
            )

            # Call the tool function
            result = await func(context, **tool_call.arguments)

            duration_ms = int((time.time() - start_time) * 1000)

            # Extract result from ToolResult (FastMCP format)
            # FastMCP ToolResult has 'content' (list[ContentBlock]) and 'structured_content' (dict)
            # ContentBlock items are Pydantic models (TextContent, etc.) - must serialize them
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

            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result_data,
                error=None,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.exception(f"System tool execution failed: {tool_call.name}")
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _record_ai_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int | None = None,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
        organization_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """
        Record AI usage for tracking and cost calculation.

        Args:
            provider: LLM provider name (e.g., 'openai', 'anthropic')
            model: Model identifier
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens generated
            duration_ms: Request duration in milliseconds
            conversation_id: UUID of the conversation
            message_id: UUID of the generated message
            organization_id: UUID of the organization
            user_id: UUID of the user
        """
        from src.core.cache import get_shared_redis
        from src.services.ai_usage_service import record_ai_usage

        redis_client = await get_shared_redis()
        async with self._db() as session:
            await record_ai_usage(
                session=session,
                redis_client=redis_client,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                conversation_id=conversation_id,
                message_id=message_id,
                organization_id=organization_id,
                user_id=user_id,
            )
