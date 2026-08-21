"""One construction path for every Bifrost Pydantic AI agent loop."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent as PydanticAgent

from src.services.agent_runtime.budgets import (
    AgentRunBudget,
    CompactionEventHandler,
    build_runtime_capabilities,
)
from src.services.agent_runtime.observed_model import ModelCallObserver, ObservedModel
from src.services.agent_runtime.toolset import (
    BifrostToolset,
    ToolEventHandler,
    ToolExecutor,
)
from src.services.llm.base import ToolDefinition


class AgentRuntimeRunner:
    """Configured Pydantic runtime shared by Chat, Agents, and Builder.

    Surfaces retain their own persistence and presentation adapters, but model
    observation, tool adaptation, compaction capabilities, retries, and end
    strategy are assembled only here.
    """

    def __init__(
        self,
        *,
        model: Any,
        instructions: str,
        budget: AgentRunBudget,
        model_settings: Any,
        tool_definitions: Sequence[ToolDefinition] = (),
        tool_executor: ToolExecutor | None = None,
        model_event_handler: ModelCallObserver | None = None,
        tool_event_handler: ToolEventHandler | None = None,
        compaction_event_handler: CompactionEventHandler | None = None,
        toolset_id: str = "bifrost",
    ) -> None:
        observed_model = (
            ObservedModel(model, model_event_handler)
            if model_event_handler is not None
            else model
        )
        if tool_definitions and tool_executor is None:
            raise ValueError("tool_executor is required when tools are configured")
        toolsets = []
        if tool_definitions:
            assert tool_executor is not None
            toolsets.append(
                BifrostToolset(
                    tool_definitions,
                    tool_executor,
                    event_handler=tool_event_handler,
                    toolset_id=toolset_id,
                )
            )
        self._runtime = PydanticAgent(
            observed_model,
            instructions=instructions,
            toolsets=toolsets,
            capabilities=build_runtime_capabilities(
                budget,
                compaction_event_handler=compaction_event_handler,
            ),
            model_settings=model_settings,
            retries=1,
            end_strategy="exhaustive",
        )

    async def run(self, prompt: Any, **kwargs: Any) -> Any:
        return await self._runtime.run(prompt, **kwargs)

    def run_stream_events(self, prompt: Any, **kwargs: Any) -> Any:
        return self._runtime.run_stream_events(prompt, **kwargs)


__all__ = ["AgentRuntimeRunner"]
