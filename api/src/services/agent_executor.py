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
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic_ai import AgentRunResultEvent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.principal import UserPrincipal
from src.models.contracts.agents import (
    AgentSwitch,
    ChatStreamChunk,
    ContextWarning,
    ToolCall,
    ToolProgress,
    ToolResult,
)
from src.models.enums import MessageRole
from src.models.orm import Agent, Conversation, Message, MessageAttachment, Workflow
from src.repositories.agents import AgentRepository
from src.services.ai_model_service import AIModelService
from src.services.llm import (
    LLMMessage,
    LLMInputFile,
    ToolCallRequest,
    ToolDefinition,
    get_llm_client,
)
from src.services.llm.pydantic_client import PydanticAIClient
from src.services.agent_runtime import (
    AgentRunBudget,
    AgentRuntimeRunner,
    ModelCallEvent,
    ModelCallObserver,
    agent_model_settings,
    bound_tool_result_for_model,
    create_agent_model,
    provider_reported_cost,
)
from src.services.agent_runtime.history import build_runtime_message_history
from src.services.model_capabilities import should_offer_tool_calling
from src.services.execution.agent_helpers import (
    find_delegated_agent,
    is_agent_system_tool,
    parse_mcp_tool_name,
    resolve_agent_tools,
)
from src.services.execution.agent_workflow_tools import (
    AgentWorkflowCaller,
    execute_agent_workflow_tool,
)
from src.services.execution.autonomous_agent_executor import (
    AutonomousAgentExecutor,
    ToolError,
)
from src.services.knowledge.search_budget import (
    KnowledgeSearchBudget,
    clamp_knowledge_result_limit,
    compact_knowledge_metadata,
    knowledge_search_rejection_payload,
    select_novel_knowledge_evidence,
)
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


def _serialize_tool_result_for_history(tool_result: ToolResult) -> str:
    """Serialize what the parent model sees, with failures taking precedence."""
    if tool_result.error:
        return bound_tool_result_for_model(tool_result.error)
    return bound_tool_result_for_model(_serialize_for_json(tool_result.result))


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

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        builder_workspace: Any = None,
        model_profile: Literal["chat", "builder"] = "chat",
        runtime_model_event_handler: ModelCallObserver | None = None,
    ):
        self._session_factory = session_factory
        self._builder_workspace = builder_workspace
        self._model_profile = model_profile
        self._runtime_model_event_handler = runtime_model_event_handler
        self._tool_workflow_id_map: dict[str, UUID] = {}  # normalized tool name → workflow UUID
        self._knowledge_search_budget = KnowledgeSearchBudget()
        self._active_usage: RunUsage | None = None
        self._active_budget: AgentRunBudget | None = None

    @property
    def active_usage(self) -> RunUsage | None:
        """Usage accumulated by the current or most recently completed run."""

        return self._active_usage

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
        *,
        user: UserPrincipal | None,
    ) -> tuple[ChatStreamChunk | None, Agent | None]:
        """
        Centralized agent switching with all rule checks.

        All agent switching paths (@mention, AI routing, etc.) should funnel
        through this method to ensure consistent behavior and rule enforcement.

        Args:
            conversation: The conversation to update
            new_agent: The agent to switch to
            reason: Why the switch happened ("@mention", "routed", etc.)
            user: Current user whose agent access must authorize the switch

        Returns:
            Tuple of optional agent_switch event and the access-checked agent.
        """
        if user is None:
            logger.warning(
                "Denied agent switch without user context: conversation_id=%s agent_id=%s reason=%s",
                conversation.id,
                new_agent.id,
                reason,
            )
            return None, None

        # Persist only after reloading the target through the repository access
        # boundary. This prevents high-level routing or mention logic from
        # binding a conversation to an otherwise inaccessible agent.
        async with self._db() as session:
            repo = AgentRepository(
                session,
                org_id=user.organization_id,
                user_id=user.user_id,
                is_superuser=user.is_superuser,
                is_external=user.is_external,
            )
            accessible_agent = await repo.get_agent_with_access_check(new_agent.id)
            if accessible_agent is None:
                logger.warning(
                    "Denied agent switch because user lacks access: user_id=%s conversation_id=%s agent_id=%s reason=%s",
                    user.user_id,
                    conversation.id,
                    new_agent.id,
                    reason,
                )
                return None, None

            conv = await session.get(Conversation, conversation.id)
            if conv:
                conv.agent_id = accessible_agent.id

        # Update in-memory object too so caller sees the change
        conversation.agent_id = accessible_agent.id

        return ChatStreamChunk(
            type="agent_switch",
            agent_switch=AgentSwitch(
                agent_id=str(accessible_agent.id),
                agent_name=accessible_agent.name,
                reason=reason,
            ),
        ), accessible_agent

    async def chat(
        self,
        agent: Agent | None,
        conversation: Conversation,
        user_message: str,
        *,
        stream: bool = True,
        enable_routing: bool = True,
        local_id: str | None = None,
        user: UserPrincipal | None = None,
        attachment_ids: list[UUID] | None = None,
        model_profile_id: UUID | None = None,
        existing_user_message_id: UUID | None = None,
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
            user: Current user for permission-aware agent routing

        Yields:
            ChatStreamChunk objects with response content, tool calls, etc.
        """
        from src.services.agent_router import AgentRouter

        start_time = time.time()
        self._knowledge_search_budget.reset()
        router = AgentRouter(
            self._session_factory,
            user_id=user.user_id if user else None,
            org_id=user.organization_id if user else None,
            is_superuser=user.is_superuser if user else False,
            is_external=user.is_external if user else False,
        )

        try:
            async with self._db() as session:
                model_service = AIModelService(session)
                effective_profile_id = (
                    agent.llm_profile_id
                    if self._model_profile == "builder"
                    and agent is not None
                    and agent.llm_profile_id is not None
                    else model_profile_id
                )
                chat_profile, resolved_config, model_capabilities = await model_service.resolve_chat_profile(
                    effective_profile_id
                )
                image_generation_enabled = await model_service.has_assignment("image_generation")
                video_generation_enabled = await model_service.has_assignment("video_generation")
            model_override = resolved_config.model

            if attachment_ids:
                from src.services.chat_attachments import validate_model_input_capabilities

                async with self._db() as session:
                    attachment_result = await session.execute(
                        select(MessageAttachment)
                        .where(MessageAttachment.artifact_id.in_(attachment_ids))
                        .where(MessageAttachment.conversation_id == conversation.id)
                    )
                    selected_attachments = attachment_result.scalars().all()
                if any(
                    attachment.content_type in IMAGE_CONTENT_TYPES
                    for attachment in selected_attachments
                ) and not model_capabilities.image_input:
                    raise ValueError(
                        f"Model profile '{chat_profile.name}' is not configured for image input. "
                        "Choose another model profile or ask an administrator to verify its capabilities."
                    )
                if any(
                    attachment.content_type in PDF_CONTENT_TYPES
                    for attachment in selected_attachments
                ) and not model_capabilities.pdf_input:
                    raise ValueError(
                        f"Model profile '{chat_profile.name}' is not configured for PDF input. "
                        "Choose another model profile or ask an administrator to verify its capabilities."
                    )

            # 1. Check for @mention agent switching
            if enable_routing:
                mentioned_agent = await router.parse_mention(user_message)
                if mentioned_agent:
                    # Switch to mentioned agent (handles events and persistence)
                    switch_chunk, switched_agent = await self._switch_agent(
                        conversation,
                        mentioned_agent,
                        "@mention",
                        user=user,
                    )
                    if switch_chunk and switched_agent:
                        # Strip @mention from message for cleaner processing
                        user_message = router.strip_mention(user_message)
                        yield switch_chunk
                        agent = switched_agent

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
                        switch_chunk, switched_agent = await self._switch_agent(
                            conversation,
                            routed_agent,
                            "routed",
                            user=user,
                        )
                        if switch_chunk and switched_agent:
                            yield switch_chunk
                            agent = switched_agent

            # 3. Save user message
            if existing_user_message_id is None:
                user_msg = await self._save_message(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content=user_message,
                    local_id=local_id,
                    attachment_ids=attachment_ids,
                )
            else:
                async with self._db() as session:
                    user_msg = await session.get(Message, existing_user_message_id)
                    if (
                        user_msg is None
                        or user_msg.conversation_id != conversation.id
                        or user_msg.role != MessageRole.USER
                    ):
                        raise ValueError("Existing user message is outside this conversation")

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
            caller = (
                {
                    "user_id": str(user.user_id),
                    "email": user.email,
                    "name": user.name,
                    "organization_id": (
                        str(user.organization_id)
                        if user.organization_id
                        else None
                    ),
                    "is_platform_admin": user.is_superuser,
                }
                if user
                else (
                    {"user_id": str(caller_user_id)}
                    if caller_user_id
                    else None
                )
            )
            tool_definitions = (
                await self._get_agent_tools(agent, caller_user_id=caller_user_id)
                if agent
                else []
            )
            if not should_offer_tool_calling(model_capabilities):
                tool_definitions = []
            else:
                from src.services.chat_artifacts import artifact_tool_definitions

                existing_tool_names = {definition.name for definition in tool_definitions}
                tool_definitions.extend(
                    definition
                    for definition in artifact_tool_definitions(
                        image_generation_enabled=image_generation_enabled,
                        video_generation_enabled=video_generation_enabled,
                    )
                    if definition.name not in existing_tool_names
                )
            logger.info(f"Agent '{agent.name if agent else 'None'}' has {len(tool_definitions)} tool definitions")
            if tool_definitions:
                logger.debug(f"Tools: {[t.name for t in tool_definitions]}")

            # 4b. Delegation tools are now included by resolve_agent_tools

            # 5. Build message history and fix any corrupted ordering
            messages = await self._build_message_history(
                agent,
                conversation,
                model_capabilities=model_capabilities,
                model_label=chat_profile.name,
            )

            # 6. Get LLM client
            async with self._db() as session:
                llm_client = await get_llm_client(session, profile_id=chat_profile.id)

            # 7. Hand the full loop to Pydantic AI. Bifrost remains responsible
            # for authorization, persistence, and its stable stream contract;
            # the runtime owns history replay, tool/result sequencing, context
            # compaction, and budget enforcement.
            max_tokens_override = agent.llm_max_tokens if agent else None
            model_name = model_override
            budget = AgentRunBudget(
                max_requests=agent.max_iterations if agent else None,
                max_total_tokens=agent.max_token_budget if agent else None,
            )
            usage = RunUsage()
            self._active_usage = usage
            self._active_budget = budget
            total_input_tokens = 0
            total_output_tokens = 0
            total_cache_read_tokens = 0
            total_cache_write_tokens = 0
            total_provider_cost = Decimal("0")
            provider_cost_seen = False

            async def record_model_event(event: ModelCallEvent) -> None:
                nonlocal total_input_tokens, total_output_tokens, model_name
                nonlocal total_cache_read_tokens, total_cache_write_tokens
                nonlocal total_provider_cost, provider_cost_seen
                if self._runtime_model_event_handler is not None:
                    try:
                        await self._runtime_model_event_handler(event)
                    except Exception:  # noqa: BLE001 - progress cannot break a run
                        logger.warning(
                            "Agent runtime model observer failed",
                            exc_info=True,
                        )
                if event.type != "response" or event.response is None:
                    return
                response_usage = event.response.usage
                total_input_tokens += response_usage.input_tokens
                total_output_tokens += response_usage.output_tokens
                total_cache_read_tokens += response_usage.cache_read_tokens
                total_cache_write_tokens += response_usage.cache_write_tokens
                request_provider_cost = provider_reported_cost(event.response)
                if request_provider_cost is not None:
                    total_provider_cost += request_provider_cost
                    provider_cost_seen = True
                if event.response.model_name:
                    model_name = event.response.model_name

            seen_tc_ids = {
                call.id
                for message in messages
                if message.role == "assistant"
                for call in message.tool_calls or []
            }
            tool_calls: dict[str, ToolCall] = {}
            tool_message_ids: dict[str, UUID] = {}
            tool_execution_ids: dict[str, str] = {}
            tool_call_ready: dict[str, asyncio.Event] = {}
            pending_tool_chunks: list[ChatStreamChunk] = []
            pending_runtime_chunks: list[ChatStreamChunk] = []

            async def record_compaction(
                before_tokens: int,
                after_tokens: int,
            ) -> None:
                pending_runtime_chunks.append(
                    ChatStreamChunk(
                        type="context_warning",
                        context_warning=ContextWarning(
                            current_tokens=after_tokens,
                            max_tokens=budget.context_target_tokens,
                            action="compacted",
                            message=(
                                "Compacted the active context from about "
                                f"{before_tokens:,} to {after_tokens:,} tokens."
                            ),
                        ),
                    )
                )

            async def execute_runtime_tool(
                name: str,
                arguments: dict[str, Any],
                tool_call_id: str,
            ) -> str:
                # Pydantic AI may schedule the tool coroutine before the
                # corresponding FunctionToolCallEvent has reached this stream
                # consumer. Wait for that event to persist the compatibility
                # message and register its IDs instead of racing the maps below.
                ready = tool_call_ready.setdefault(tool_call_id, asyncio.Event())
                await ready.wait()
                tool_call = tool_calls[tool_call_id]
                execution_id = tool_execution_ids[tool_call_id]
                message_id = tool_message_ids[tool_call_id]
                request = ToolCallRequest(
                    id=tool_call.id,
                    name=name,
                    arguments=arguments,
                )
                tool_result = await self._execute_tool(
                    request,
                    agent,
                    conversation,
                    execution_id=execution_id,
                    tool_message_id=message_id,
                    caller_user_id=caller_user_id,
                    caller=caller,
                )
                tool_history_content, result_chunks = await self.complete_tool_call(
                    agent=agent,
                    conversation=conversation,
                    tool_call=tool_call,
                    message_id=message_id,
                    execution_id=execution_id,
                    tool_result=tool_result,
                )
                if stream:
                    pending_tool_chunks.extend(result_chunks)
                return tool_history_content

            system_prompt = messages[0].content or FALLBACK_SYSTEM_PROMPT
            from src.services.chat_artifacts import (
                ARTIFACT_WORKSPACE_INSTRUCTIONS,
                BUILTIN_ARTIFACT_TOOL_NAMES,
            )

            if any(
                definition.name in BUILTIN_ARTIFACT_TOOL_NAMES
                for definition in tool_definitions
            ):
                system_prompt = (
                    f"{system_prompt.rstrip()}\n\n{ARTIFACT_WORKSPACE_INSTRUCTIONS}"
                )
            history_messages = messages[1:]
            current_prompt = user_message
            if history_messages and history_messages[-1].role == "user":
                current_prompt = PydanticAIClient.convert_user_content(
                    history_messages.pop()
                )

            runtime = AgentRuntimeRunner(
                model=create_agent_model(llm_client.config, model=model_name),
                instructions=system_prompt,
                budget=budget,
                model_settings=agent_model_settings(
                    llm_client.config,
                    max_tokens=max_tokens_override,
                    session_id=str(conversation.id),
                ),
                tool_definitions=tool_definitions,
                tool_executor=execute_runtime_tool,
                model_event_handler=record_model_event,
                compaction_event_handler=record_compaction,
                toolset_id=f"bifrost-chat-{agent.id if agent else 'default'}",
            )

            final_content = ""
            current_response_content = ""
            current_text_persisted = False
            try:
                async with runtime.run_stream_events(
                    current_prompt,
                    message_history=PydanticAIClient.convert_messages(history_messages),
                    usage_limits=budget.usage_limits(),
                    usage=usage,
                    conversation_id=str(conversation.id),
                ) as events:
                    async for event in events:
                        if pending_runtime_chunks:
                            for pending_chunk in pending_runtime_chunks:
                                yield pending_chunk
                            pending_runtime_chunks.clear()
                        if pending_tool_chunks:
                            for pending_chunk in pending_tool_chunks:
                                yield pending_chunk
                            pending_tool_chunks.clear()
                            current_response_content = ""
                            current_text_persisted = False
                        if isinstance(event, PartStartEvent) and isinstance(
                            event.part, TextPart
                        ):
                            current_response_content += event.part.content
                            if stream and event.part.content:
                                yield ChatStreamChunk(type="delta", content=event.part.content)
                        elif isinstance(event, PartDeltaEvent) and isinstance(
                            event.delta, TextPartDelta
                        ):
                            current_response_content += event.delta.content_delta
                            if stream and event.delta.content_delta:
                                yield ChatStreamChunk(type="delta", content=event.delta.content_delta)
                        elif isinstance(event, FunctionToolCallEvent):
                            part = event.part
                            if current_response_content and not current_text_persisted:
                                text_msg = await self._save_message(
                                    conversation_id=conversation.id,
                                    role=MessageRole.ASSISTANT,
                                    content=current_response_content,
                                    model=model_name,
                                )
                                current_text_persisted = True
                                if stream:
                                    yield ChatStreamChunk(
                                        type="assistant_message_end",
                                        message_id=str(text_msg.id),
                                    )
                            display_id = part.tool_call_id
                            if display_id in seen_tc_ids:
                                display_id = f"{display_id}_run{usage.requests}"
                            seen_tc_ids.add(display_id)
                            tool_call = ToolCall(
                                id=display_id,
                                name=part.tool_name,
                                arguments=part.args_as_dict(),
                            )
                            message_id, execution_id, start_chunks = (
                                await self.begin_tool_call(
                                    conversation=conversation,
                                    tool_call=tool_call,
                                )
                            )
                            tool_calls[part.tool_call_id] = tool_call
                            tool_message_ids[part.tool_call_id] = message_id
                            tool_execution_ids[part.tool_call_id] = execution_id
                            tool_call_ready.setdefault(
                                part.tool_call_id, asyncio.Event()
                            ).set()
                            if stream:
                                for start_chunk in start_chunks:
                                    yield start_chunk
                        elif isinstance(event, AgentRunResultEvent):
                            final_content = str(event.result.output or "")
                    for pending_chunk in pending_tool_chunks:
                        yield pending_chunk
                    pending_tool_chunks.clear()
            except UsageLimitExceeded:
                final_content = current_response_content or (
                    "I reached this run's limit before I could finish. I preserved "
                    "the completed tool results and progress above so the work can "
                    "continue without starting over."
                )
                yield ChatStreamChunk(
                    type="context_warning",
                    context_warning=ContextWarning(
                        current_tokens=usage.total_tokens,
                        max_tokens=budget.max_total_tokens,
                        action="warning",
                        message="The agent reached its run budget and left a resumable handoff.",
                    ),
                )

            for pending_chunk in pending_runtime_chunks:
                yield pending_chunk
            pending_runtime_chunks.clear()

            # 9. Save final assistant message (using pre-generated ID)
            duration_ms = int((time.time() - start_time) * 1000)
            assistant_msg = await self._save_message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=final_content,
                token_count_input=total_input_tokens,
                token_count_output=total_output_tokens,
                model=model_name,
                duration_ms=duration_ms,
                message_id=assistant_message_id,
            )

            # 9b. Record AI usage
            try:
                await self._record_ai_usage(
                    provider=llm_client.provider_name,
                    model=model_name,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cache_read_tokens=total_cache_read_tokens,
                    cache_write_tokens=total_cache_write_tokens,
                    provider_cost=(total_provider_cost if provider_cost_seen else None),
                    duration_ms=duration_ms,
                    conversation_id=conversation.id,
                    message_id=assistant_msg.id,
                    organization_id=agent.organization_id if agent else None,
                    user_id=conversation.user_id,
                )
            except Exception as e:
                logger.warning(f"Failed to record AI usage: {e}")

            # Pydantic's stream context can finish its graph while the final
            # persistence awaits above give the completed tool callbacks their
            # last scheduling turn. Drain immediately before `done` so the
            # compatibility terminator remains the final chunk.
            await asyncio.sleep(0)
            for pending_chunk in pending_tool_chunks:
                yield pending_chunk
            pending_tool_chunks.clear()

            # 10. Yield done chunk with final content (for non-streaming mode)
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
        self,
        agent: Agent | None,
        conversation: Conversation,
        *,
        model_capabilities: Any | None = None,
        model_label: str = "selected model",
    ) -> list[LLMMessage]:
        """Build the message history for LLM completion."""
        # Add system prompt (use agent's prompt or configurable default for agentless chat)
        if agent:
            from src.services.execution.agent_helpers import build_agent_system_prompt
            system_prompt = build_agent_system_prompt(agent, execution_context={"mode": "chat"})
        else:
            system_prompt = await self._get_default_system_prompt()
        # Get conversation messages in order
        async with self._db() as session:
            result = await session.execute(
                select(Message)
                .options(selectinload(Message.attachments))
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence)
            )
            db_messages = result.scalars().all()

            from src.services.chat_attachments import (
                ChatAttachmentService,
                is_binary_model_input,
                validate_model_input_capabilities,
            )

            if model_capabilities is not None:
                validate_model_input_capabilities(
                    [
                        attachment
                        for message in db_messages
                        for attachment in message.attachments
                    ],
                    image_input=model_capabilities.image_input,
                    pdf_input=model_capabilities.pdf_input,
                    model_label=model_label,
                )

            attachment_service = ChatAttachmentService(session)
            user_inputs: dict[UUID, tuple[str | None, list[LLMInputFile]]] = {}
            for db_message in db_messages:
                if db_message.role != MessageRole.USER or not db_message.attachments:
                    continue
                text_parts = [db_message.content] if db_message.content else []
                input_files: list[LLMInputFile] = []
                for attachment in db_message.attachments:
                    if is_binary_model_input(attachment.content_type):
                        loaded = await attachment_service.load_binary_input(attachment)
                        input_files.append(
                            LLMInputFile(
                                filename=loaded.filename,
                                media_type=loaded.content_type,
                                data=loaded.data,
                            )
                        )
                    elif attachment.extracted_text:
                        text_parts.append(
                            f"[Attached file: {attachment.filename}]\n"
                            f"{attachment.extracted_text}"
                        )
                user_inputs[db_message.id] = (
                    "\n\n".join(text_parts) if text_parts else None,
                    input_files,
                )

        return build_runtime_message_history(
            system_prompt=system_prompt,
            persisted_messages=db_messages,
            user_inputs=user_inputs,
        )

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
        attachment_ids: list[UUID] | None = None,
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
            if attachment_ids:
                from src.services.chat_attachments import ChatAttachmentService

                await ChatAttachmentService(session).bind(
                    attachment_ids=attachment_ids,
                    message_id=message.id,
                    conversation_id=conversation_id,
                )

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

    async def begin_tool_call(
        self,
        *,
        conversation: Conversation,
        tool_call: ToolCall,
        execution_id: str | None = None,
        message_id: UUID | None = None,
    ) -> tuple[UUID, str, list[ChatStreamChunk]]:
        """Persist and present one tool call for local or remote runtimes."""

        resolved_execution_id = execution_id or str(uuid4())
        message = await self._save_message(
            conversation_id=conversation.id,
            role=MessageRole.TOOL_CALL,
            tool_name=tool_call.name,
            tool_input=tool_call.arguments,
            tool_state="running",
            tool_call_id=tool_call.id,
            execution_id=resolved_execution_id,
            message_id=message_id,
        )
        chunks = [
            ChatStreamChunk(
                type="tool_call",
                tool_call=tool_call,
                execution_id=resolved_execution_id,
                message_id=str(message.id),
            ),
            ChatStreamChunk(
                type="tool_progress",
                tool_progress=ToolProgress(
                    tool_call_id=tool_call.id,
                    execution_id=resolved_execution_id,
                    status="running",
                ),
            ),
        ]
        from src.services.chat_artifacts import ARTIFACT_TOOL_NAMES

        if tool_call.name in ARTIFACT_TOOL_NAMES:
            chunks.append(
                ChatStreamChunk(
                    type="artifact_started",
                    content=str(
                        tool_call.arguments.get("filename") or "generated file"
                    ),
                    message_id=str(message.id),
                )
            )
        return message.id, resolved_execution_id, chunks

    async def save_assistant_segment(
        self,
        *,
        conversation: Conversation,
        content: str,
        model: str | None,
    ) -> Message:
        """Persist a pre-tool assistant segment for any shared runtime host."""

        return await self._save_message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=content,
            model=model,
        )

    async def complete_tool_call(
        self,
        *,
        agent: Agent | None,
        conversation: Conversation,
        tool_call: ToolCall,
        message_id: UUID,
        execution_id: str,
        tool_result: ToolResult,
    ) -> tuple[str, list[ChatStreamChunk]]:
        """Persist a tool result and produce the shared Chat/artifact events."""

        promoted_artifacts = []
        from src.services.chat_artifacts import (
            ARTIFACT_TOOL_NAMES,
            IMMEDIATE_ARTIFACT_TOOL_NAMES,
            promote_artifact_refs,
        )

        if (
            not tool_result.error
            and tool_result.result is not None
            and tool_call.name not in ARTIFACT_TOOL_NAMES
        ):
            try:
                async with self._db() as session:
                    promoted_artifacts = await promote_artifact_refs(
                        session,
                        result=tool_result.result,
                        conversation_id=conversation.id,
                        conversation_user_id=conversation.user_id,
                        message_id=message_id,
                        agent_organization_id=(
                            agent.organization_id if agent else None
                        ),
                    )
            except Exception as exc:
                tool_result = ToolResult(
                    tool_call_id=tool_result.tool_call_id,
                    tool_name=tool_result.tool_name,
                    result=None,
                    error=f"Artifact output could not be imported: {exc}",
                    duration_ms=tool_result.duration_ms,
                )

        chunks = [
            ChatStreamChunk(
                type="tool_result",
                tool_result=tool_result,
                message_id=str(message_id),
            )
        ]
        from src.models.contracts.artifacts import ArtifactRef

        if tool_call.name in ARTIFACT_TOOL_NAMES:
            if tool_result.error:
                chunks.append(
                    ChatStreamChunk(
                        type="artifact_failed",
                        content=tool_result.error,
                        message_id=str(message_id),
                    )
                )
            elif tool_call.name in IMMEDIATE_ARTIFACT_TOOL_NAMES:
                chunks.append(
                    ChatStreamChunk(
                        type="artifact_ready",
                        artifact=ArtifactRef.model_validate(tool_result.result),
                        message_id=str(message_id),
                    )
                )
        chunks.extend(
            ChatStreamChunk(
                type="artifact_ready",
                artifact=artifact,
                message_id=str(message_id),
            )
            for artifact in promoted_artifacts
        )
        persisted_result = (
            tool_result.result
            if not tool_result.error
            else {"error": tool_result.error, **(tool_result.metadata or {})}
        )
        await self._update_tool_call_message(
            message_id=message_id,
            tool_state="completed" if not tool_result.error else "error",
            tool_result=persisted_result,
            duration_ms=tool_result.duration_ms,
        )
        model_content = _serialize_tool_result_for_history(tool_result)
        await self._save_message(
            conversation_id=conversation.id,
            role=MessageRole.TOOL,
            content=model_content,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            execution_id=execution_id,
            duration_ms=tool_result.duration_ms,
        )
        return model_content, chunks

    async def execute_started_tool_call(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        tool_call: ToolCall,
        message_id: UUID,
        execution_id: str,
        caller_user_id: UUID | None,
        caller: dict[str, Any] | None,
    ) -> tuple[ToolResult, str, list[ChatStreamChunk]]:
        """Execute and finish a Bifrost-side tool for a remote shared runtime."""

        result = await self._execute_tool(
            ToolCallRequest(
                id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            ),
            agent,
            conversation,
            execution_id=execution_id,
            tool_message_id=message_id,
            caller_user_id=caller_user_id,
            caller=caller,
        )
        model_content, chunks = await self.complete_tool_call(
            agent=agent,
            conversation=conversation,
            tool_call=tool_call,
            message_id=message_id,
            execution_id=execution_id,
            tool_result=result,
        )
        return result, model_content, chunks

    async def _execute_tool(
        self,
        tool_call: ToolCallRequest,
        agent: Agent | None = None,
        conversation: Conversation | None = None,
        execution_id: str | None = None,
        tool_message_id: UUID | None = None,
        *,
        caller_user_id: UUID | None = None,
        caller: dict[str, Any] | None = None,
    ) -> ToolResult:
        """
        Execute a tool (workflow, delegation, system tool, knowledge search,
        or external MCP tool) and return the result.

        This integrates with the existing workflow execution system
        and handles agent delegation, system tools, built-in knowledge
        search, and remote MCP servers.
        """
        start_time = time.time()

        from src.models.contracts.artifacts import ArtifactRef
        from src.services.chat_artifacts import BUILTIN_ARTIFACT_TOOL_NAMES

        if tool_call.name in BUILTIN_ARTIFACT_TOOL_NAMES:
            if conversation is None or tool_message_id is None:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error="Artifact tools require an active Chat conversation.",
                    duration_ms=int((time.time() - start_time) * 1000),
                )
            try:
                from src.services.chat_artifacts import execute_artifact_tool

                async with self._db() as session:
                    _, artifact = await execute_artifact_tool(
                        session,
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments or {},
                        conversation_id=conversation.id,
                        message_id=tool_message_id,
                    )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=(
                        artifact.model_dump(mode="json")
                        if isinstance(artifact, ArtifactRef)
                        else artifact
                    ),
                    duration_ms=int((time.time() - start_time) * 1000),
                )
            except Exception as exc:
                logger.warning("Artifact generation failed for %s: %s", tool_call.name, exc)
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error=str(exc),
                    duration_ms=int((time.time() - start_time) * 1000),
                )

        # Check if this is a knowledge search tool call
        if tool_call.name == "search_knowledge" and agent:
            return await self._execute_knowledge_search(tool_call, agent)

        # Check if this is a delegation tool call
        if tool_call.name.startswith("delegate_to_") and agent:
            return await self._execute_delegation(
                tool_call,
                agent,
                conversation=conversation,
                caller=caller,
            )

        # Check if this is a system tool call
        if agent and is_agent_system_tool(agent, tool_call.name):
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
            resolved_workflow: tuple[str, str] | None = None
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
                if workflow is not None:
                    # Keep ORM access inside the owning async session. Session exit
                    # may expire attributes, and later implicit IO would fail with
                    # MissingGreenlet outside SQLAlchemy's async bridge.
                    resolved_workflow = (str(workflow.id), workflow.name)

            if resolved_workflow is None:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error=f"Tool '{tool_call.name}' not found",
                    duration_ms=int((time.time() - start_time) * 1000),
                )
            resolved_workflow_id, resolved_workflow_name = resolved_workflow

            # Get user info from conversation
            user = conversation.user if conversation else None

            # Get org_id from agent (workflows are not org-scoped)
            org_id = str(agent.organization_id) if agent and agent.organization_id else None

            execution_response = await execute_agent_workflow_tool(
                workflow_id=resolved_workflow_id,
                workflow_name=resolved_workflow_name,
                parameters=tool_call.arguments or {},
                caller=AgentWorkflowCaller(
                    user_id=str(user.id) if user else "system",
                    email=user.email if user else "system@internal.gobifrost.com",
                    name=user.name if user else "System",
                    is_platform_admin=user.is_superuser if user else False,
                ),
                organization_id=org_id,
                execution_id=execution_id,
                artifact_workspace_id=str(conversation.id) if conversation else None,
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
        """Get the profile-independent Chat instructions or use the fallback."""
        from src.services.ai_behavior_service import AIBehaviorService

        try:
            async with self._db() as session:
                prompt = await AIBehaviorService(session).get_default_system_prompt()
            if prompt:
                return prompt
        except Exception as e:
            logger.warning(f"Failed to get default AI instructions: {e}")

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

        This is a built-in tool that doesn't require a workflow. The search
        is the AGENT's own grounding (the agent is the access boundary the
        caller was granted), so it always uses the full org+global cascade
        regardless of who is chatting.
        """
        start_time = time.time()

        try:
            from src.repositories.knowledge import KnowledgeRepository
            from src.services.embeddings import get_embedding_client

            # Get search parameters
            query = tool_call.arguments.get("query", "")
            limit = clamp_knowledge_result_limit(
                tool_call.arguments.get("limit", 5)
            )

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

            decision = self._knowledge_search_budget.reserve(query)
            if not decision.allowed:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=knowledge_search_rejection_payload(decision),
                    error=None,
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
                    query_text=query,
                    limit=limit,
                    fallback=True,
                )

            duration_ms = int((time.time() - start_time) * 1000)

            # Format results for the agent
            evidence = select_novel_knowledge_evidence(
                self._knowledge_search_budget,
                [
                    (
                        doc.id,
                        {
                            "content": doc.content,
                            "namespace": doc.namespace,
                            "score": round(doc.score, 4)
                            if doc.score is not None
                            else None,
                            "key": doc.key,
                            "metadata": compact_knowledge_metadata(doc.metadata),
                        },
                    )
                    for doc in results
                ],
            )

            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result={
                    "documents": evidence.documents,
                    "count": len(evidence.documents),
                    "omitted_duplicate_evidence": evidence.omitted_duplicates,
                    "omitted_for_evidence_budget": evidence.omitted_for_budget,
                    "searches_used": decision.searches_used,
                    "searches_remaining": decision.searches_remaining,
                    "evidence_chars_used": evidence.evidence_chars_used,
                    "evidence_chars_remaining": evidence.evidence_chars_remaining,
                },
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
        *,
        conversation: Conversation | None = None,
        caller: dict[str, Any] | None = None,
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

            logger.info(f"Agent '{agent.name}' delegating to '{delegated_agent.name}' via chat")

            redis_client = await get_shared_redis()
            delegation_executor = AutonomousAgentExecutor(
                self._session_factory,
                redis_client=redis_client,
            )
            outcome = await delegation_executor.run_delegation(
                parent_agent=agent,
                tool_call=tool_call,
                conversation_id=conversation.id if conversation else None,
                caller=caller,
                _shared_usage=self._active_usage,
                _shared_budget=self._active_budget,
            )
            metadata = {
                "child_run_id": str(outcome.child_run_id),
                "agent": outcome.agent_name,
                "status": outcome.status,
            }

            logger.info(
                f"Delegation to '{outcome.agent_name}' completed with "
                f"status={outcome.status}"
            )

            if not outcome.succeeded:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=None,
                    error=(
                        outcome.error
                        or f"Delegation ended with status {outcome.status}"
                    ),
                    duration_ms=outcome.duration_ms,
                    metadata=metadata,
                )

            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result={
                    "response": str(outcome.output),
                    "agent": outcome.agent_name,
                    "status": outcome.status,
                    "child_run_id": str(outcome.child_run_id),
                },
                error=None,
                duration_ms=outcome.duration_ms,
                metadata=metadata,
            )

        except ToolError as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=None,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
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
                agent_bundle_path=agent.bundle_path,
                agent_skill_id=agent.id if agent.bundle_path else None,
                agent_solution_id=agent.solution_id,
                builder_workspace=self._builder_workspace,
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
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        provider_cost: Decimal | None = None,
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
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                provider_cost=provider_cost,
                duration_ms=duration_ms,
                conversation_id=conversation_id,
                message_id=message_id,
                organization_id=organization_id,
                user_id=user_id,
            )

    async def record_usage(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        provider_cost: Decimal | None = None,
        duration_ms: int | None = None,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
        organization_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """Record usage reported by any host of the shared agent runtime."""

        await self._record_ai_usage(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            provider_cost=provider_cost,
            duration_ms=duration_ms,
            conversation_id=conversation_id,
            message_id=message_id,
            organization_id=organization_id,
            user_id=user_id,
        )
