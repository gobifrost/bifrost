"""Regression coverage for proactive budget wind-down."""

from pydantic_ai.usage import RunUsage

from src.services.agent_runtime import AgentRunBudget


def test_small_budget_reserves_a_final_request() -> None:
    budget = AgentRunBudget(max_requests=10, max_total_tokens=20_000)

    assert budget.wind_down_total_tokens == 8_000
    assert budget.wind_down_warning_threshold == 0.4
    assert not budget.should_wind_down(RunUsage(input_tokens=7_999))
    assert budget.should_wind_down(RunUsage(input_tokens=8_000))


def test_final_allowed_request_forces_wind_down_even_with_tokens_remaining() -> None:
    budget = AgentRunBudget(max_requests=4, max_total_tokens=100_000)

    assert not budget.should_wind_down(RunUsage(requests=3, input_tokens=1_000))
    assert budget.should_wind_down(RunUsage(requests=4, input_tokens=1_000))
