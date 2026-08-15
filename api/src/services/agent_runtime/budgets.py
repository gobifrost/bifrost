"""Run-budget and context-management policy for Bifrost agents."""

from dataclasses import dataclass
from datetime import timedelta

from pydantic_ai.capabilities import AgentCapability
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness.compaction import (
    ClampOversizedMessages,
    ClearToolResults,
    LimitWarner,
    SlidingWindow,
    TieredCompaction,
)
from pydantic_ai_harness.overflowing_tool_output import LocalFileStore, OverflowingToolOutput

DEFAULT_CONTEXT_TARGET_TOKENS = 24_000
"""Keep the active request well below typical provider context windows.

This is a cost-control target, not a claim about a model's maximum context size.
"""


@dataclass(frozen=True)
class AgentRunBudget:
    """One shared budget expressed in the units Pydantic AI actually enforces."""

    max_requests: int
    max_total_tokens: int
    context_target_tokens: int = DEFAULT_CONTEXT_TARGET_TOKENS
    warning_threshold: float = 0.7

    def usage_limits(self) -> UsageLimits:
        """Return pre-request-enforced limits for the full agentic loop."""

        return UsageLimits(
            request_limit=self.max_requests,
            total_tokens_limit=self.max_total_tokens,
            count_tokens_before_request=True,
        )

    def child_subtree(
        self,
        *,
        current_requests: int,
        current_total_tokens: int,
        child_max_requests: int,
        child_max_total_tokens: int,
    ) -> "AgentRunBudget":
        """Bound a delegated subtree by both child and inherited ceilings.

        Limits are absolute because Pydantic AI receives the shared cumulative
        ``RunUsage`` object. A child can therefore spend its configured local
        allowance from the current point, but cannot reset or escape the root
        run's remaining budget.
        """

        return AgentRunBudget(
            max_requests=min(
                self.max_requests,
                current_requests + child_max_requests,
            ),
            max_total_tokens=min(
                self.max_total_tokens,
                current_total_tokens + child_max_total_tokens,
            ),
            context_target_tokens=self.context_target_tokens,
            warning_threshold=self.warning_threshold,
        )


def build_runtime_capabilities(budget: AgentRunBudget) -> list[AgentCapability[object]]:
    """Build the standard context and wind-down policy.

    Cheap, deterministic compaction runs before lossy sliding-window trimming.
    Oversized tool output is spilled once when produced, so it is not re-sent in
    full on every later request. LimitWarner gives the model time to finish notes
    and communicate partial progress before the hard guard rejects a request.
    """

    target = budget.context_target_tokens
    retained_tail = max(4_000, int(target * 0.75))
    return [
        OverflowingToolOutput(
            store=LocalFileStore(cleanup_after=timedelta(hours=6)),
        ),
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
                SlidingWindow(
                    max_tokens=1,
                    keep_tokens=retained_tail,
                    preserve_first_user_message=True,
                ),
            ],
            target_tokens=target,
        ),
        LimitWarner(
            max_iterations=budget.max_requests,
            max_context_tokens=target,
            max_total_tokens=budget.max_total_tokens,
            warning_threshold=budget.warning_threshold,
            critical_remaining_iterations=2,
        ),
    ]
