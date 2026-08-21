"""Runner-neutral semantic loop for Pydantic AI turns."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

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

from src.models.contracts.agents import ChatStreamChunk, ContextWarning, ToolCall
from src.services.agent_runtime.budgets import AgentRunBudget
from src.services.agent_runtime.observed_model import ModelCallEvent
from src.services.agent_runtime.usage import provider_reported_cost
from src.services.agent_runtime.usage_governance import (
    observe_model_usage_for_governance,
)
from src.services.usage_limits import PortableUsage


@dataclass(frozen=True, slots=True)
class AssistantSegmentResult:
    events: Sequence[ChatStreamChunk] = ()


@dataclass(frozen=True, slots=True)
class ToolStartResult:
    events: Sequence[ChatStreamChunk] = ()
    handle: Any = None


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    model_content: str
    events: Sequence[ChatStreamChunk] = ()
    error: str | None = None


@dataclass(slots=True)
class AgentTurnCoordinatorResult:
    final_text: str
    token_count_input: int
    token_count_output: int
    cache_read_tokens: int
    cache_write_tokens: int
    provider_cost: Decimal | None
    model: str
    duration_ms: int
    model_request_count: int
    tool_call_count: int
    tool_error_count: int
    compaction_count: int
    harness_diagnostics: dict[str, Any] = field(default_factory=dict)


AssistantSegmentPersister = Callable[
    [str, str],
    Awaitable[AssistantSegmentResult],
]
ToolStarter = Callable[[ToolCall, str], Awaitable[ToolStartResult]]
ToolExecutor = Callable[
    [str, dict[str, Any], str, str, ToolStartResult],
    Awaitable[ToolExecutionResult],
]
ModelEventObserver = Callable[[ModelCallEvent], Awaitable[None]]


class AgentTurnCoordinator:
    """Owns semantic Pydantic AI event handling for Chat and Builder surfaces."""

    def __init__(
        self,
        *,
        runtime: Any,
        current_prompt: Any,
        message_history: Sequence[Any],
        usage: RunUsage,
        budget: AgentRunBudget,
        conversation_id: str,
        model_name: str,
        assistant_segment_persister: AssistantSegmentPersister,
        tool_starter: ToolStarter,
        tool_executor: ToolExecutor,
        model_event_observer: ModelEventObserver | None = None,
        usage_governance: Any | None = None,
        stream: bool = True,
        usage_limit_message: str,
        seen_tool_call_ids: set[str] | None = None,
    ) -> None:
        self.runtime = runtime
        self.current_prompt = current_prompt
        self.message_history = list(message_history)
        self.usage = usage
        self.budget = budget
        self.conversation_id = conversation_id
        self.model_name = model_name
        self.assistant_segment_persister = assistant_segment_persister
        self.tool_starter = tool_starter
        self.tool_executor = tool_executor
        self.model_event_observer = model_event_observer
        self.usage_governance = usage_governance
        self.stream = stream
        self.usage_limit_message = usage_limit_message
        self.seen_tool_call_ids = (
            seen_tool_call_ids if seen_tool_call_ids is not None else set()
        )

        self._tool_calls: dict[str, ToolCall] = {}
        self._tool_start_results: dict[str, ToolStartResult] = {}
        self._tool_call_ready: dict[str, asyncio.Event] = {}
        self._pending_tool_chunks: list[ChatStreamChunk] = []
        self._pending_runtime_chunks: list[ChatStreamChunk] = []
        self._totals = Counter[str]()
        self._total_provider_cost = Decimal("0")
        self._provider_cost_seen = False
        self._tool_counts: Counter[str] = Counter()
        self._tool_error_counts: Counter[str] = Counter()
        self._compaction_count = 0
        self._final_text = ""
        self._current_response = ""
        self._current_segment_persisted = False
        self._started_at = time.monotonic()

    async def record_model_event(self, event: ModelCallEvent) -> None:
        if self.model_event_observer is not None:
            await self.model_event_observer(event)
        if event.type != "response" or event.response is None:
            return
        response_usage = event.response.usage
        self._totals["input"] += response_usage.input_tokens
        self._totals["output"] += response_usage.output_tokens
        self._totals["cache_read"] += response_usage.cache_read_tokens
        self._totals["cache_write"] += response_usage.cache_write_tokens
        cost = provider_reported_cost(event.response)
        if cost is not None:
            self._total_provider_cost += cost
            self._provider_cost_seen = True
        if event.response.model_name:
            self.model_name = event.response.model_name
        if observe_model_usage_for_governance(
            self.usage_governance,
            self.budget,
            PortableUsage(
                model_requests=1,
                input_tokens=response_usage.input_tokens,
                output_tokens=response_usage.output_tokens,
                cache_read_tokens=response_usage.cache_read_tokens,
                cache_write_tokens=response_usage.cache_write_tokens,
            ),
        ):
            self._pending_runtime_chunks.append(
                ChatStreamChunk(
                    type="context_warning",
                    context_warning=ContextWarning(
                        current_tokens=self.usage.total_tokens,
                        max_tokens=self.budget.max_total_tokens,
                        action="warning",
                        message=(
                            "The run reached a configured usage allowance "
                            "and is stopping cleanly before more model "
                            "requests."
                        ),
                    ),
                )
            )

    async def record_compaction(self, before_tokens: int, after_tokens: int) -> None:
        self._compaction_count += 1
        self._pending_runtime_chunks.append(
            ChatStreamChunk(
                type="context_warning",
                context_warning=ContextWarning(
                    current_tokens=after_tokens,
                    max_tokens=self.budget.context_target_tokens,
                    action="compacted",
                    message=(
                        "Compacted the active context from about "
                        f"{before_tokens:,} to {after_tokens:,} tokens."
                    ),
                ),
            )
        )

    async def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        internal_call_id: str,
    ) -> str:
        ready = self._tool_call_ready.setdefault(internal_call_id, asyncio.Event())
        await ready.wait()
        tool_call = self._tool_calls[internal_call_id]
        start_result = self._tool_start_results[internal_call_id]
        self._tool_counts[name] += 1
        result = await self.tool_executor(
            name,
            arguments,
            internal_call_id,
            tool_call.id,
            start_result,
        )
        if result.error:
            self._tool_error_counts[name] += 1
        if self.stream:
            self._pending_tool_chunks.extend(result.events)
        return result.model_content

    async def run(self) -> AsyncIterator[ChatStreamChunk]:
        try:
            async with self.runtime.run_stream_events(
                self.current_prompt,
                message_history=self.message_history,
                usage_limits=self.budget.usage_limits(),
                usage=self.usage,
                conversation_id=self.conversation_id,
            ) as events:
                async for event in events:
                    async for chunk in self._drain_pending():
                        yield chunk
                    if isinstance(event, PartStartEvent) and isinstance(
                        event.part,
                        TextPart,
                    ):
                        self._current_response += event.part.content
                        if self.stream and event.part.content:
                            yield ChatStreamChunk(
                                type="delta",
                                content=event.part.content,
                            )
                    elif isinstance(event, PartDeltaEvent) and isinstance(
                        event.delta,
                        TextPartDelta,
                    ):
                        self._current_response += event.delta.content_delta
                        if self.stream and event.delta.content_delta:
                            yield ChatStreamChunk(
                                type="delta",
                                content=event.delta.content_delta,
                            )
                    elif isinstance(event, FunctionToolCallEvent):
                        async for chunk in self._handle_tool_call(event):
                            yield chunk
                    elif isinstance(event, AgentRunResultEvent):
                        self._final_text = str(event.result.output or "")
                async for chunk in self._drain_pending(force=True):
                    yield chunk
        except UsageLimitExceeded:
            self._final_text = self._current_response or self.usage_limit_message
            yield ChatStreamChunk(
                type="context_warning",
                context_warning=ContextWarning(
                    current_tokens=self.usage.total_tokens,
                    max_tokens=self.budget.max_total_tokens,
                    action="warning",
                    message="The agent reached its run budget and left a resumable handoff.",
                ),
            )

    async def _handle_tool_call(
        self,
        event: FunctionToolCallEvent,
    ) -> AsyncIterator[ChatStreamChunk]:
        if self._current_response and not self._current_segment_persisted:
            result = await self.assistant_segment_persister(
                self._current_response,
                self.model_name,
            )
            self._current_segment_persisted = True
            if self.stream:
                for chunk in result.events:
                    yield chunk
        part = event.part
        display_id = part.tool_call_id
        if display_id in self.seen_tool_call_ids:
            display_id = f"{display_id}_run{self.usage.requests}"
        self.seen_tool_call_ids.add(display_id)
        tool_call = ToolCall(
            id=display_id,
            name=part.tool_name,
            arguments=part.args_as_dict(),
        )
        start_result = await self.tool_starter(tool_call, part.tool_call_id)
        self._tool_calls[part.tool_call_id] = tool_call
        self._tool_start_results[part.tool_call_id] = start_result
        self._tool_call_ready.setdefault(part.tool_call_id, asyncio.Event()).set()
        if self.stream:
            for chunk in start_result.events:
                yield chunk
        self._current_response = ""
        self._current_segment_persisted = False

    async def _drain_pending(
        self,
        *,
        force: bool = False,
    ) -> AsyncIterator[ChatStreamChunk]:
        del force
        while self._pending_runtime_chunks:
            yield self._pending_runtime_chunks.pop(0)
        while self._pending_tool_chunks:
            yield self._pending_tool_chunks.pop(0)
            self._current_response = ""
            self._current_segment_persisted = False

    def result(self) -> AgentTurnCoordinatorResult:
        duration_ms = int((time.monotonic() - self._started_at) * 1000)
        tool_call_count = sum(self._tool_counts.values())
        tool_error_count = sum(self._tool_error_counts.values())
        diagnostics = {
            "tool_call_count": tool_call_count,
            "tool_error_count": tool_error_count,
            "compaction_count": self._compaction_count,
            "retry_count": 0,
            "truncated": False,
            "tools": [
                {
                    "name": name,
                    "count": count,
                    "error_count": self._tool_error_counts[name],
                }
                for name, count in self._tool_counts.most_common(32)
            ],
            "other_tool_call_count": sum(
                count for _name, count in self._tool_counts.most_common()[32:]
            ),
        }
        return AgentTurnCoordinatorResult(
            final_text=self._final_text,
            token_count_input=self._totals["input"],
            token_count_output=self._totals["output"],
            cache_read_tokens=self._totals["cache_read"],
            cache_write_tokens=self._totals["cache_write"],
            provider_cost=(
                self._total_provider_cost if self._provider_cost_seen else None
            ),
            model=self.model_name,
            duration_ms=duration_ms,
            model_request_count=self.usage.requests,
            tool_call_count=tool_call_count,
            tool_error_count=tool_error_count,
            compaction_count=self._compaction_count,
            harness_diagnostics=diagnostics,
        )


__all__ = [
    "AgentTurnCoordinator",
    "AgentTurnCoordinatorResult",
    "AssistantSegmentResult",
    "ToolExecutionResult",
    "ToolStartResult",
]
