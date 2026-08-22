"""Delegated-agent coverage for inherited budget wind-down."""

from pydantic_ai.usage import RunUsage

from src.services.agent_runtime import AgentRunBudget


def test_child_wind_down_is_measured_from_its_inherited_usage_baseline() -> None:
    parent = AgentRunBudget(max_requests=20, max_total_tokens=100_000)
    child = parent.child_subtree(
        current_requests=7,
        current_total_tokens=60_000,
        child_max_requests=5,
        child_max_total_tokens=25_000,
    )

    assert child.initial_requests == 7
    assert child.initial_total_tokens == 60_000
    assert child.wind_down_total_tokens == 70_000
    assert not child.should_wind_down(
        RunUsage(requests=7, input_tokens=60_000)
    )
    assert child.should_wind_down(
        RunUsage(requests=7, input_tokens=70_000)
    )
