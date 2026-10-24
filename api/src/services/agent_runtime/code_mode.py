"""Native CodeMode primitives for Bifrost agent runtimes.

The sandbox is supplied by Pydantic AI Harness and has no Bifrost runner,
workflow, filesystem, or operator setup of its own. Its nested calls use the
same live Bifrost toolset as ordinary calls. Only those nested values bypass
the model-history result cap; the single public ``run_code`` value is capped
before it returns to the model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart, ToolReturn
from pydantic_ai.tools import RunContext, ToolDefinition, ToolSelector
from pydantic_ai_harness.code_mode import CodeMode, CodeModeResourceLimits

from src.services.agent_runtime.toolset import (
    bound_tool_result_for_model,
    unbounded_model_tool_results,
)

DEFAULT_CODE_MODE_RESOURCE_LIMITS: CodeModeResourceLimits = {
    "max_duration_secs": 10,
    "max_memory": 128 * 1024 * 1024,
    "max_suspensions": 128,
}
"""Deliberate local-sandbox backstops for the experimental capability."""

DEFAULT_CODE_MODE_MAX_TOOL_CALLS = 16
"""Nested dispatch budget for one model-visible ``run_code`` call."""

DEFAULT_CODE_MODE_MAX_RETRIES = 3
"""Native model-correction attempts after a failed ``run_code`` snippet."""

CODE_MODE_INSTRUCTIONS = """Use run_code to combine related tool calls and keep intermediate data in code.
Return compact evidence: selected records, counts, identifiers, verification, errors, and uncertainty.
Sandbox variables are local to this run; use persisted receipts and identifiers on later turns.
Follow the existing write approval and verification rules.
"""
"""Local policy supplement for the native CodeMode capability."""


class _CodeModeResultBoundary(AbstractCapability[object]):
    """Scope unbounded nested values and cap the sole model-visible result."""

    def get_instructions(self) -> str:
        """Give the model guidance for the one public CodeMode return boundary."""
        return CODE_MODE_INSTRUCTIONS

    async def wrap_tool_execute(
        self,
        ctx: RunContext[object],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> Any:
        del ctx, tool_def
        if call.tool_name != "run_code":
            return await handler(args)

        # CodeMode runs nested calls while its outer run_code lifecycle is
        # active. ContextVar propagation keeps the exception local to those
        # calls, including awaits, and resets before another model tool runs.
        with unbounded_model_tool_results() as scope:
            scope.outer_tool_call_id = call.tool_call_id
            try:
                result = await handler(args)
            except BaseException:
                if scope.cancellation:
                    raise scope.cancellation
                raise
        if scope.cancellation:
            raise scope.cancellation
        if isinstance(result, ToolReturn):
            return replace(
                result,
                return_value=bound_tool_result_for_model(result.return_value),
            )
        return bound_tool_result_for_model(result)


def build_code_mode_capabilities(
    *,
    tools: ToolSelector[object] = "all",
    resource_limits: CodeModeResourceLimits | None = None,
    max_tool_calls: int = DEFAULT_CODE_MODE_MAX_TOOL_CALLS,
    max_retries: int = DEFAULT_CODE_MODE_MAX_RETRIES,
) -> Sequence[AbstractCapability[object]]:
    """Build native CodeMode with Bifrost's local result boundary.

    Its caller chooses the model-visible catalog. CodeMode wraps only
    caller-selected tools; eager and speculative execution stay off
    so a completed model response still determines every effect.
    """
    return (
        CodeMode(
            tools=tools,
            dynamic_catalog=True,
            resource_limits=resource_limits or DEFAULT_CODE_MODE_RESOURCE_LIMITS,
            max_tool_calls=max_tool_calls,
            max_retries=max_retries,
            eager=False,
            speculate=None,
        ),
        _CodeModeResultBoundary(),
    )
