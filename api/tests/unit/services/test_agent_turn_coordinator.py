from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RequestUsage, RunUsage

from src.models.contracts.agents import ChatStreamChunk, ToolCall
from src.services.agent_runtime import (
    AgentRunBudget,
    AgentTurnCoordinator,
    AssistantSegmentResult,
    ModelCallEvent,
    ToolExecutionResult,
    ToolStartResult,
)


class _Runtime:
    def __init__(self, events: list[Any], *, raises: Exception | None = None) -> None:
        self.events = events
        self.raises = raises
        self.kwargs: dict[str, Any] | None = None
        self.after_tool_event: Any = None

    @asynccontextmanager
    async def run_stream_events(self, _prompt: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self.kwargs = kwargs

        async def iterator() -> AsyncIterator[Any]:
            for event in self.events:
                yield event
                if isinstance(event, FunctionToolCallEvent) and self.after_tool_event:
                    await self.after_tool_event(event.part.tool_call_id)
            if self.raises is not None:
                raise self.raises

        yield iterator()


def _text_start(content: str) -> PartStartEvent:
    return PartStartEvent(index=0, part=TextPart(content=content))


def _text_delta(content: str) -> PartDeltaEvent:
    return PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=content))


def _tool_call(tool_call_id: str = "call-1") -> FunctionToolCallEvent:
    return FunctionToolCallEvent(
        part=ToolCallPart(
            tool_name="sample_tool",
            args={"value": 1},
            tool_call_id=tool_call_id,
        )
    )


def _done(output: str) -> AgentRunResultEvent[str]:
    return AgentRunResultEvent(result=AgentRunResult(output=output))


def _coordinator(
    runtime: _Runtime,
    *,
    stream: bool = True,
    seen_tool_call_ids: set[str] | None = None,
    segment_events: tuple[ChatStreamChunk, ...] = (),
    tool_error: str | None = None,
    usage: RunUsage | None = None,
    observed_model_events: list[ModelCallEvent] | None = None,
    usage_governance: Any | None = None,
) -> tuple[AgentTurnCoordinator, list[str], list[ToolCall]]:
    persisted_segments: list[str] = []
    started_tools: list[ToolCall] = []

    async def persist_segment(content: str, _model: str) -> AssistantSegmentResult:
        persisted_segments.append(content)
        return AssistantSegmentResult(events=segment_events)

    async def start_tool(
        tool_call: ToolCall,
        _internal_call_id: str,
    ) -> ToolStartResult:
        started_tools.append(tool_call)
        return ToolStartResult(
            events=(
                ChatStreamChunk(type="tool_call", tool_call=tool_call),
            )
        )

    async def execute_tool(
        _name: str,
        _arguments: dict[str, Any],
        _internal_call_id: str,
        _display_call_id: str,
        _start_result: ToolStartResult,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            model_content="tool-result",
            events=(ChatStreamChunk(type="tool_result", content="tool-result"),),
            error=tool_error,
        )

    async def observe_model_event(event: ModelCallEvent) -> None:
        if observed_model_events is not None:
            observed_model_events.append(event)

    coordinator = AgentTurnCoordinator(
        runtime=runtime,
        current_prompt="prompt",
        message_history=[],
        usage=usage or RunUsage(),
        budget=AgentRunBudget(max_requests=5, max_total_tokens=100),
        conversation_id="conversation-1",
        model_name="test-model",
        assistant_segment_persister=persist_segment,
        tool_starter=start_tool,
        tool_executor=execute_tool,
        model_event_observer=(
            observe_model_event if observed_model_events is not None else None
        ),
        usage_governance=usage_governance,
        stream=stream,
        usage_limit_message="resumable limit",
        seen_tool_call_ids=seen_tool_call_ids,
    )
    return coordinator, persisted_segments, started_tools


def _simulate_pydantic_tool_execution(
    runtime: _Runtime,
    coordinator: AgentTurnCoordinator,
) -> None:
    async def execute(internal_call_id: str) -> None:
        await coordinator.execute_tool(
            "sample_tool",
            {"value": 1},
            internal_call_id,
        )

    runtime.after_tool_event = execute


@pytest.mark.asyncio
async def test_text_only_turn_projects_deltas_and_final_output() -> None:
    runtime = _Runtime([_text_start("hel"), _text_delta("lo"), _done("done")])
    coordinator, _segments, _tools = _coordinator(runtime)

    chunks = [chunk async for chunk in coordinator.run()]
    result = coordinator.result()

    assert [(chunk.type, chunk.content) for chunk in chunks] == [
        ("delta", "hel"),
        ("delta", "lo"),
    ]
    assert result.final_text == "done"
    assert result.tool_call_count == 0
    assert runtime.kwargs is not None
    assert runtime.kwargs["conversation_id"] == "conversation-1"


@pytest.mark.asyncio
async def test_tool_call_success_persists_segment_and_sequences_tool_events() -> None:
    segment_end = ChatStreamChunk(type="assistant_message_end", message_id="segment-1")
    runtime = _Runtime([_text_start("before"), _tool_call(), _done("done")])
    coordinator, segments, tools = _coordinator(
        runtime,
        segment_events=(segment_end,),
    )
    _simulate_pydantic_tool_execution(runtime, coordinator)

    chunks = [chunk async for chunk in coordinator.run()]
    result = coordinator.result()

    assert segments == ["before"]
    assert [tool.id for tool in tools] == ["call-1"]
    assert [(chunk.type, chunk.content, chunk.message_id) for chunk in chunks] == [
        ("delta", "before", None),
        ("assistant_message_end", None, "segment-1"),
        ("tool_call", None, None),
        ("tool_result", "tool-result", None),
    ]
    assert result.tool_call_count == 1
    assert result.tool_error_count == 0


@pytest.mark.asyncio
async def test_tool_call_error_counts_but_returns_model_content() -> None:
    runtime = _Runtime([_tool_call(), _done("done")])
    coordinator, _segments, _tools = _coordinator(runtime, tool_error="bad tool")
    _simulate_pydantic_tool_execution(runtime, coordinator)

    chunks = [chunk async for chunk in coordinator.run()]
    result = coordinator.result()

    assert [chunk.type for chunk in chunks] == ["tool_call", "tool_result"]
    assert result.tool_call_count == 1
    assert result.tool_error_count == 1
    assert result.harness_diagnostics["tools"][0] == {
        "name": "sample_tool",
        "count": 1,
        "error_count": 1,
    }


@pytest.mark.asyncio
async def test_usage_limit_emits_resumable_warning() -> None:
    runtime = _Runtime([_text_start("partial")], raises=UsageLimitExceeded("stop"))
    coordinator, _segments, _tools = _coordinator(runtime)

    chunks = [chunk async for chunk in coordinator.run()]
    result = coordinator.result()

    assert [(chunk.type, chunk.content) for chunk in chunks[:1]] == [
        ("delta", "partial")
    ]
    assert chunks[-1].type == "context_warning"
    assert chunks[-1].context_warning is not None
    assert chunks[-1].context_warning.action == "warning"
    assert result.final_text == "partial"


@pytest.mark.asyncio
async def test_compaction_event_is_projected_and_counted() -> None:
    runtime = _Runtime([_done("done")])
    coordinator, _segments, _tools = _coordinator(runtime)

    await coordinator.record_compaction(120, 60)
    chunks = [chunk async for chunk in coordinator.run()]
    result = coordinator.result()

    assert chunks[0].type == "context_warning"
    assert chunks[0].context_warning is not None
    assert chunks[0].context_warning.action == "compacted"
    assert result.compaction_count == 1


@pytest.mark.asyncio
async def test_model_accounting_and_provenance_accumulate_across_responses() -> None:
    runtime = _Runtime([_done("done")])
    usage = RunUsage(requests=2)
    observed: list[ModelCallEvent] = []
    coordinator, _segments, _tools = _coordinator(
        runtime,
        usage=usage,
        observed_model_events=observed,
    )
    first = ModelCallEvent(
        type="response",
        messages_count=2,
        tools_count=1,
        response=ModelResponse(
            parts=[],
            usage=RequestUsage(
                input_tokens=10,
                output_tokens=5,
                cache_read_tokens=3,
                cache_write_tokens=2,
            ),
            model_name="provider-model-a",
            provider_details={"cost": "0.001"},
        ),
    )
    second = ModelCallEvent(
        type="response",
        messages_count=3,
        tools_count=1,
        response=ModelResponse(
            parts=[],
            usage=RequestUsage(
                input_tokens=7,
                output_tokens=11,
                cache_read_tokens=13,
                cache_write_tokens=17,
            ),
            model_name="provider-model-b",
            provider_details={"cost": "0.004"},
        ),
    )

    await coordinator.record_model_event(first)
    await coordinator.record_model_event(second)
    [chunk async for chunk in coordinator.run()]
    result = coordinator.result()

    assert observed == [first, second]
    assert result.token_count_input == 17
    assert result.token_count_output == 16
    assert result.cache_read_tokens == 16
    assert result.cache_write_tokens == 19
    assert str(result.provider_cost) == "0.005"
    assert result.model == "provider-model-b"
    assert result.model_request_count == 2


@pytest.mark.asyncio
async def test_usage_governance_warning_winds_down_after_response_dimension_limit() -> None:
    class _Governance:
        def observe_model_usage(self, _usage: Any) -> bool:
            return True

    runtime = _Runtime([_done("done")])
    usage = RunUsage(requests=2, input_tokens=25)
    coordinator, _segments, _tools = _coordinator(
        runtime,
        usage=usage,
        usage_governance=_Governance(),
    )

    await coordinator.record_model_event(
        ModelCallEvent(
            type="response",
            messages_count=1,
            tools_count=0,
            response=ModelResponse(
                parts=[],
                usage=RequestUsage(input_tokens=10, output_tokens=20),
                model_name="test-model",
            ),
        )
    )
    chunks = [chunk async for chunk in coordinator.run()]

    assert chunks[0].type == "context_warning"
    assert chunks[0].context_warning is not None
    assert chunks[0].context_warning.action == "warning"
    assert coordinator.budget.control.force_wind_down


@pytest.mark.asyncio
async def test_duplicate_tool_call_ids_are_remapped_and_empty_seen_set_is_preserved() -> None:
    runtime = _Runtime([_tool_call("existing"), _done("done")])
    seen: set[str] = set()
    seen.add("existing")
    coordinator, _segments, tools = _coordinator(
        runtime,
        seen_tool_call_ids=seen,
    )
    _simulate_pydantic_tool_execution(runtime, coordinator)

    [chunk async for chunk in coordinator.run()]

    assert tools[0].id == "existing_run0"
    assert seen == {"existing", "existing_run0"}


@pytest.mark.asyncio
async def test_stream_false_suppresses_intermediate_chunks_but_keeps_semantics() -> None:
    runtime = _Runtime([_text_start("before"), _tool_call(), _done("done")])
    coordinator, segments, tools = _coordinator(runtime, stream=False)
    _simulate_pydantic_tool_execution(runtime, coordinator)

    chunks = [chunk async for chunk in coordinator.run()]
    result = coordinator.result()

    assert chunks == []
    assert segments == ["before"]
    assert [tool.id for tool in tools] == ["call-1"]
    assert result.final_text == "done"
    assert result.tool_call_count == 1
