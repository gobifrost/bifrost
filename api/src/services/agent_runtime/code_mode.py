"""Native CodeMode primitives for Bifrost agent runtimes.

The sandbox is supplied by Pydantic AI Harness and has no Bifrost runner,
workflow, filesystem, or operator setup of its own. Its nested calls use the
same live Bifrost toolset as ordinary calls. Only those nested values bypass
the model-history result cap; the single public ``run_code`` value is capped
before it returns to the model. Automatic policy composes these primitives with
the catalog-private Bifrost sandbox SDK.
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

CODE_MODE_INSTRUCTIONS = """Use run_code as an execution boundary: printed output AND the final expression are
sent to the model. A bare tool call or a variable containing a full response as the last expression
returns that whole response. Keep intermediate records inside code.
Sandbox variables are local to the current run. On a later chat turn, use persisted compact
receipts and identifiers; do not assume earlier variables survive or repeat completed mutations.

After discovering exact callable names and input schemas, complete a related read phase in one
snippet: fetch, decode, extract identifiers, perform dependent reads, then filter/project/aggregate.
Derive subsequent IDs from earlier results in code instead of copying them through model turns.
If a semantic decision is needed before another read, return only the candidate facts for that
decision. Follow existing write approval and verification rules; do not replay completed mutations.

Tool results can be JSON strings even when their return type is Any. Decode JSON text before field
access. If the shape is unknown, inspect only top-level keys, collection counts and item field
names, then compose the read phase. Do not print raw responses to learn their shape.

End with a compact result containing the relevant records/fields, counts, target IDs, verification,
errors and uncertainty. Select matching rows and fields explicitly. Do not print, implicitly return,
or string-slice entire responses: character clipping is not semantic filtering and can lose facts.
For example, after decoding a schema-confirmed result, project the needed fields from matching
items rather than returning the item collection. Preserve evidence needed for the decision,
including missing coverage and unknown effective permissions. Full original results and execution
receipts remain in the application audit outside model context.
"""
"""Generic orchestration guidance for the native CodeMode capability."""


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
    tools: ToolSelector[object],
    resource_limits: CodeModeResourceLimits | None = None,
    max_tool_calls: int = DEFAULT_CODE_MODE_MAX_TOOL_CALLS,
    max_retries: int = DEFAULT_CODE_MODE_MAX_RETRIES,
) -> Sequence[AbstractCapability[object]]:
    """Build the small, explicit capability set used by the CodeMode spike.

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
