"""Runtime usage-governance adapter tests."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.sandbox_runner_protocol import SandboxRuntimeUsageGovernanceSnapshot
from src.services.agent_runtime import AgentRunBudget
from src.services.agent_runtime.usage_governance import (
    RuntimeUsageGovernance,
    runtime_usage_governance_from_snapshot,
    runtime_usage_organization_id,
)
from src.services.usage_limits import (
    PortableUsage,
    UsageCeilings,
    UsageLimitPeriod,
    UsageLimitPolicy,
    UsageLimitScope,
    UsageLimitSubject,
)


@pytest.mark.asyncio
async def test_most_specific_per_run_policy_wins_then_agent_budget_intersects() -> None:
    governance = RuntimeUsageGovernance(
        subject=UsageLimitSubject(),
        policies=(
            UsageLimitPolicy(
                scope=UsageLimitScope.PLATFORM,
                per_run=UsageCeilings(model_requests=1, total_tokens=50),
            ),
            UsageLimitPolicy(
                scope=UsageLimitScope.SOLUTION,
                per_run=UsageCeilings(model_requests=10, total_tokens=200),
            ),
        ),
        aggregate_usage_by_scope_period={},
    )

    budget = await governance.constrain_budget(
        AsyncMock(),
        AgentRunBudget(max_requests=3, max_total_tokens=100),
    )

    assert budget.max_requests == 3
    assert budget.max_total_tokens == 100


@pytest.mark.asyncio
async def test_aggregate_remaining_allowance_constrains_budget_cumulatively() -> None:
    governance = RuntimeUsageGovernance(
        subject=UsageLimitSubject(),
        policies=(
            UsageLimitPolicy(
                scope=UsageLimitScope.PLATFORM,
                aggregate=UsageCeilings(model_requests=10, total_tokens=1_000),
                aggregate_period=UsageLimitPeriod.MONTHLY,
            ),
        ),
        aggregate_usage_by_scope_period={
            (UsageLimitScope.PLATFORM, UsageLimitPeriod.MONTHLY): PortableUsage(
                model_requests=8,
                input_tokens=900,
            )
        },
    )

    budget = await governance.constrain_budget(
        AsyncMock(),
        AgentRunBudget(max_requests=20, max_total_tokens=5_000),
    )

    assert budget.max_requests == 2
    assert budget.max_total_tokens == 100


def test_response_only_dimensions_trigger_wind_down_after_observed_response() -> None:
    governance = RuntimeUsageGovernance(
        subject=UsageLimitSubject(),
        policies=(
            UsageLimitPolicy(
                scope=UsageLimitScope.USER,
                per_run=UsageCeilings(output_tokens=10, cache_read_tokens=5),
            ),
        ),
        aggregate_usage_by_scope_period={},
    )

    assert governance.observe_model_usage(
        PortableUsage(output_tokens=11, cache_read_tokens=1)
    )


def test_observed_run_usage_counts_toward_aggregate_response_dimensions() -> None:
    governance = RuntimeUsageGovernance(
        subject=UsageLimitSubject(),
        policies=(
            UsageLimitPolicy(
                scope=UsageLimitScope.PLATFORM,
                aggregate=UsageCeilings(output_tokens=25, cache_read_tokens=15),
                aggregate_period=UsageLimitPeriod.MONTHLY,
            ),
        ),
        aggregate_usage_by_scope_period={
            (UsageLimitScope.PLATFORM, UsageLimitPeriod.MONTHLY): PortableUsage(
                output_tokens=10,
                cache_read_tokens=5,
            )
        },
    )

    assert not governance.observe_model_usage(
        PortableUsage(output_tokens=10, cache_read_tokens=5)
    )
    assert governance.observe_model_usage(
        PortableUsage(output_tokens=6, cache_read_tokens=6)
    )


def test_runner_snapshot_preserves_remote_response_dimension_decisions() -> None:
    subject = UsageLimitSubject()
    local = RuntimeUsageGovernance(
        subject=subject,
        policies=(
            UsageLimitPolicy(
                scope=UsageLimitScope.PLATFORM,
                aggregate=UsageCeilings(output_tokens=25),
                aggregate_period=UsageLimitPeriod.MONTHLY,
            ),
        ),
        aggregate_usage_by_scope_period={
            (UsageLimitScope.PLATFORM, UsageLimitPeriod.MONTHLY): PortableUsage(
                output_tokens=10,
            )
        },
    )
    remote = runtime_usage_governance_from_snapshot(
        subject,
        local.runner_snapshot(),
    )
    assert remote is not None

    first = PortableUsage(output_tokens=10)
    second = PortableUsage(output_tokens=6)

    assert local.observe_model_usage(first) == remote.observe_model_usage(first)
    assert local.observe_model_usage(second) == remote.observe_model_usage(second)


def test_runner_snapshot_wire_contract_rejects_unknown_usage_dimensions() -> None:
    with pytest.raises(ValidationError):
        SandboxRuntimeUsageGovernanceSnapshot.model_validate(
            {
                "policies": [
                    {
                        "scope": "platform",
                        "per_run": {"made_up_tokens": 5},
                    }
                ],
            }
        )


def test_runner_snapshot_wire_contract_rejects_unknown_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        SandboxRuntimeUsageGovernanceSnapshot.model_validate(
            {
                "policies": [],
                "aggregate_usage": [],
                "unexpected": True,
            }
        )


def test_runtime_usage_organization_falls_back_to_actor_home_org() -> None:
    actor_org_id = uuid4()
    target_org_id = uuid4()

    assert (
        runtime_usage_organization_id(
            resource_organization_id=None,
            requester_organization_id=actor_org_id,
            target_kind="solution",
        )
        == actor_org_id
    )
    assert (
        runtime_usage_organization_id(
            resource_organization_id=None,
            requester_organization_id=actor_org_id,
            target_kind="global_repo",
        )
        == actor_org_id
    )
    assert (
        runtime_usage_organization_id(
            resource_organization_id=target_org_id,
            requester_organization_id=actor_org_id,
            target_kind="organization",
        )
        == target_org_id
    )
