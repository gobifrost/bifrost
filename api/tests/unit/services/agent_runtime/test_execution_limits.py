"""Execution limits: timeout resolution, names, and legacy compatibility."""

from __future__ import annotations

from src.services.agent_runtime.settings import (
    DEFAULT_RUN_TIMEOUT_SECONDS,
    effective_run_timeout_seconds,
)


class TestTimeoutResolution:
    def test_snapshot_wins_over_live_agent(self):
        assert (
            effective_run_timeout_seconds(60, 600, default=1800) == 60.0
        )

    def test_agent_used_when_snapshot_unset(self):
        assert (
            effective_run_timeout_seconds(None, 600, default=1800) == 600.0
        )

    def test_zero_disables_from_either_source(self):
        assert effective_run_timeout_seconds(0, 600, default=1800) is None
        assert effective_run_timeout_seconds(None, 0, default=1800) is None

    def test_unset_falls_back_to_default(self):
        assert (
            effective_run_timeout_seconds(None, None, default=1800) == 1800.0
        )
        assert effective_run_timeout_seconds(
            None, None, default=DEFAULT_RUN_TIMEOUT_SECONDS
        ) == float(DEFAULT_RUN_TIMEOUT_SECONDS)

    def test_public_names_and_meanings_preserved(self):
        """Limit names keep their historical meanings.

        - ``max_iterations``: model-turn limit.
        - ``max_token_budget``: run token budget.
        - ``max_run_timeout``: active-run safety limit (0 = disabled).
        - ``agents.run(timeout=...)``: caller wait duration, never cancels.
        - workflow ``timeout_seconds``: scoped to a workflow/tool execution.
        """
        from src.services.agent_runtime.budgets import AgentRunBudget

        budget = AgentRunBudget(max_requests=5, max_total_tokens=1000)
        limits = budget.usage_limits()
        assert limits.request_limit == 5
        assert limits.total_tokens_limit == 1000
