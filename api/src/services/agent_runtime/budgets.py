"""Run-budget and context-management policy for Bifrost agents."""

from dataclasses import dataclass, replace
from pydantic_ai.capabilities import AbstractCapability, AgentCapability
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_ai_harness.compaction import (
    ClampOversizedMessages,
    ClearToolResults,
    SlidingWindowCompaction,
    TieredCompaction,
    WarnNearLimits,
)
from pydantic_ai_harness.warn_on_cache_busts import WarnOnCacheBusts

DEFAULT_CONTEXT_TARGET_TOKENS = 24_000
"""Keep the active request well below typical provider context windows.

This is a cost-control target, not a claim about a model's maximum context size.
"""

FINAL_RESPONSE_RESERVE_TOKENS = 4_000
"""Output allowance reserved after the active context for a graceful handoff."""

MIN_WIND_DOWN_FRACTION = 0.4
"""Never force wind-down before this fraction of the configured local allowance."""


@dataclass(frozen=True)
class AgentRunBudget:
    """One shared budget expressed in the units Pydantic AI actually enforces."""

    max_requests: int | None = None
    max_total_tokens: int | None = None
    context_target_tokens: int = DEFAULT_CONTEXT_TARGET_TOKENS
    warning_threshold: float = 0.7
    initial_requests: int = 0
    initial_total_tokens: int = 0

    @property
    def wind_down_total_tokens(self) -> int | None:
        """Absolute cumulative usage at which finalization must begin.

        A percentage-only warning can arrive too late when one request consumes
        most of the remaining budget. Reserve one compacted context plus a
        bounded final answer, while capping that reserve for small local
        allowances. Delegated children calculate this boundary from the usage at
        which they started, so inherited parent spend cannot wind them down
        immediately.
        """

        if self.max_total_tokens is None:
            return None

        allowance = max(1, self.max_total_tokens - self.initial_total_tokens)
        desired_reserve = self.context_target_tokens + FINAL_RESPONSE_RESERVE_TOKENS
        maximum_reserve = int(allowance * (1 - MIN_WIND_DOWN_FRACTION))
        reserve = min(desired_reserve, maximum_reserve)
        configured_threshold = self.initial_total_tokens + int(
            allowance * self.warning_threshold
        )
        reserved_threshold = self.max_total_tokens - reserve
        return min(configured_threshold, reserved_threshold)

    @property
    def wind_down_warning_threshold(self) -> float:
        """WarnNearLimits threshold aligned with the finalization boundary."""

        if self.max_total_tokens is None:
            return self.warning_threshold
        wind_down_total_tokens = self.wind_down_total_tokens
        assert wind_down_total_tokens is not None
        return max(
            1 / self.max_total_tokens,
            wind_down_total_tokens / self.max_total_tokens,
        )

    def should_wind_down(self, usage: RunUsage) -> bool:
        """Return true while the next request must be reserved for a handoff."""

        request_limit_reached = (
            self.max_requests is not None and usage.requests >= self.max_requests
        )
        wind_down_total_tokens = self.wind_down_total_tokens
        token_limit_reached = (
            wind_down_total_tokens is not None
            and usage.total_tokens >= wind_down_total_tokens
        )
        return request_limit_reached or token_limit_reached

    def usage_limits(self) -> UsageLimits:
        """Return pre-request-enforced limits for the full agentic loop."""

        return UsageLimits(
            request_limit=self.max_requests,
            total_tokens_limit=self.max_total_tokens,
            count_tokens_before_request=self.max_total_tokens is not None,
        )

    def child_subtree(
        self,
        *,
        current_requests: int,
        current_total_tokens: int,
        child_max_requests: int | None,
        child_max_total_tokens: int | None,
    ) -> "AgentRunBudget":
        """Bound a delegated subtree by both child and inherited ceilings.

        Limits are absolute because Pydantic AI receives the shared cumulative
        ``RunUsage`` object. A child can therefore spend its configured local
        allowance from the current point, but cannot reset or escape the root
        run's remaining budget.
        """

        def subtree_ceiling(
            inherited_ceiling: int | None,
            current_usage: int,
            child_allowance: int | None,
        ) -> int | None:
            child_ceiling = (
                current_usage + child_allowance
                if child_allowance is not None
                else None
            )
            if inherited_ceiling is None:
                return child_ceiling
            if child_ceiling is None:
                return inherited_ceiling
            return min(inherited_ceiling, child_ceiling)

        return AgentRunBudget(
            max_requests=subtree_ceiling(
                self.max_requests,
                current_requests,
                child_max_requests,
            ),
            max_total_tokens=subtree_ceiling(
                self.max_total_tokens,
                current_total_tokens,
                child_max_total_tokens,
            ),
            context_target_tokens=self.context_target_tokens,
            warning_threshold=self.warning_threshold,
            initial_requests=current_requests,
            initial_total_tokens=current_total_tokens,
        )


@dataclass
class BudgetWindDown(AbstractCapability[object]):
    """Reserve one tool-free request and turn stale tool intent into a handoff."""

    budget: AgentRunBudget

    @staticmethod
    def _display_tool_name(tool_name: str) -> str:
        normalized = tool_name.removeprefix("wf_").removeprefix("delegate_to_")
        return normalized.replace("_", " ").strip().title()

    @classmethod
    def _handoff_text(
        cls,
        *,
        request_context: ModelRequestContext,
        pending_calls: list[ToolCallPart],
    ) -> str:
        completed_names: list[str] = []
        for message in request_context.messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    name = cls._display_tool_name(part.tool_name)
                    if name and name not in completed_names:
                        completed_names.append(name)

        pending_names: list[str] = []
        for call in pending_calls:
            name = cls._display_tool_name(call.tool_name)
            if name and name not in pending_names:
                pending_names.append(name)

        lines = [
            "I've reached the configured run budget, so I'm stopping cleanly here."
        ]
        if completed_names:
            lines.append(f"Completed before the limit: {'; '.join(completed_names)}.")
        if pending_names:
            lines.append(f"Not completed: {'; '.join(pending_names)}.")
        lines.append(
            "The completed steps and tool results are preserved in this run. "
            "No remaining tool actions were run."
        )
        return "\n\n".join(lines)

    async def prepare_tools(
        self,
        ctx: RunContext[object],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        if self.budget.should_wind_down(ctx.usage):
            return []
        return tool_defs

    async def after_model_request(
        self,
        ctx: RunContext[object],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        if not self.budget.should_wind_down(ctx.usage):
            return response

        pending_calls = [
            part for part in response.parts if isinstance(part, ToolCallPart)
        ]
        if not pending_calls:
            return response
        parts = [part for part in response.parts if not isinstance(part, ToolCallPart)]
        handoff = self._handoff_text(
            request_context=request_context,
            pending_calls=pending_calls,
        )
        text_parts = [part for part in parts if isinstance(part, TextPart)]
        if text_parts:
            text_parts[-1].content = f"{text_parts[-1].content.rstrip()}\n\n{handoff}"
        else:
            parts.append(TextPart(content=handoff))
        return replace(response, parts=parts, finish_reason="stop")


def build_runtime_capabilities(budget: AgentRunBudget) -> list[AgentCapability[object]]:
    """Build the standard context and wind-down policy.

    Cheap, deterministic compaction runs before lossy sliding-window trimming.
    Tool output is bounded at the Bifrost tool boundary, before it enters model
    history. WarnNearLimits gives the model time to finish notes and communicate
    partial progress before the hard guard rejects a request. BudgetWindDown
    then removes every function tool for the reserved final request and
    normalizes any stale provider tool call into a final response.
    """

    target = budget.context_target_tokens
    retained_tail = max(4_000, int(target * 0.75))
    return [
        WarnOnCacheBusts[object](min_prefix_tokens=1_024),
        TieredCompaction(
            tiers=[
                ClampOversizedMessages(
                    max_part_tokens=max(4_000, target // 2),
                    keep_head_chars=2_000,
                    keep_tail_chars=2_000,
                ),
                ClearToolResults(
                    max_tokens=1,
                    keep_pairs=3,
                    min_clear_tokens=1_000,
                ),
                SlidingWindowCompaction(
                    max_tokens=1,
                    keep_tokens=retained_tail,
                    preserve_first_user_message=True,
                ),
            ],
            target_tokens=target,
        ),
        WarnNearLimits[object](
            max_iterations=budget.max_requests,
            max_context_tokens=target,
            max_total_tokens=budget.max_total_tokens,
            warning_threshold=budget.wind_down_warning_threshold,
            critical_remaining_iterations=2,
        ),
        BudgetWindDown(budget),
    ]
