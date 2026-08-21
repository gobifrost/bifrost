"""Shared usage-governance adapter for Pydantic AI runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from shared.sandbox_runner_protocol import (
    SandboxRuntimeUsageAggregateSnapshot,
    SandboxRuntimeUsageCeilings,
    SandboxRuntimeUsageGovernanceSnapshot,
    SandboxRuntimeUsagePolicySnapshot,
    SandboxRuntimeUsageSnapshot,
)
from src.services.agent_runtime.budgets import AgentRunBudget
from src.services.usage_limits import (
    PortableUsage,
    UsageCeilings,
    UsageLimitPeriod,
    UsageLimitPolicy,
    UsageLimitScope,
    UsageLimitSubject,
    evaluate_usage_limits,
    load_period_usage,
    load_usage_limit_policies,
    record_supported_period_usage,
    resolve_effective_per_run_limit,
)


@dataclass
class RuntimeUsageGovernance:
    """Persisted policy projection for one runtime subject."""

    subject: UsageLimitSubject
    policies: tuple[UsageLimitPolicy, ...]
    aggregate_usage_by_scope_period: dict[
        tuple[UsageLimitScope, UsageLimitPeriod],
        PortableUsage,
    ]
    observed_run_usage: PortableUsage = PortableUsage()

    async def constrain_budget(
        self,
        session: AsyncSession,
        budget: AgentRunBudget,
        *,
        at: datetime | None = None,
    ) -> AgentRunBudget:
        """Intersect persisted request/token ceilings with the runtime budget."""

        effective = _intersect_per_run_winner(budget, self.policies)
        for policy in self.policies:
            effective = await _intersect_aggregate_policy(
                effective,
                policy,
                self.aggregate_usage_by_scope_period.get(
                    (policy.scope, policy.aggregate_period),
                    PortableUsage(),
                ),
            )
        return effective

    def observe_model_usage(
        self,
        usage: PortableUsage,
    ) -> bool:
        """Track separately reported dimensions and return true if at a limit.

        Pydantic can enforce request and total-token ceilings before a request.
        It cannot know output/cache totals until a provider response arrives.
        Those dimensions therefore wind down after the first over-limit response
        rather than pretending the response could be rejected beforehand.
        """

        previous = self.observed_run_usage
        self.observed_run_usage = self.observed_run_usage + usage
        projected_aggregate_usage = {
            key: persisted + previous
            for key, persisted in self.aggregate_usage_by_scope_period.items()
        }
        decision = evaluate_usage_limits(
            policies=list(self.policies),
            current_per_run=previous,
            requested=usage,
            aggregate_usage_by_scope_period=projected_aggregate_usage,
        )
        return not decision.allowed

    def runner_snapshot(self) -> SandboxRuntimeUsageGovernanceSnapshot:
        """Serialize policy decisions for DB-free isolated runners."""

        return SandboxRuntimeUsageGovernanceSnapshot(
            policies=[
                SandboxRuntimeUsagePolicySnapshot(
                    scope=policy.scope.value,
                    per_run=SandboxRuntimeUsageCeilings(
                        **policy.per_run.configured()
                    ),
                    aggregate=SandboxRuntimeUsageCeilings(
                        **policy.aggregate.configured()
                    ),
                    aggregate_period=policy.aggregate_period.value,
                )
                for policy in self.policies
            ],
            aggregate_usage=[
                SandboxRuntimeUsageAggregateSnapshot(
                    scope=scope.value,
                    period=period.value,
                    usage=_portable_usage_to_snapshot(usage),
                )
                for (scope, period), usage in self.aggregate_usage_by_scope_period.items()
            ],
        )

    async def record_runner_completion(
        self,
        session: AsyncSession,
        *,
        runner_duration_ms: int | None = None,
        sandbox_compute_ms: int | None = None,
        at: datetime | None = None,
    ) -> None:
        """Record terminal runtime dimensions exactly once per owning run/turn."""

        usage = PortableUsage(
            runner_duration_ms=runner_duration_ms or 0,
            sandbox_compute_ms=sandbox_compute_ms or 0,
        )
        if usage.runner_duration_ms == 0 and usage.sandbox_compute_ms == 0:
            return
        await record_supported_period_usage(session, self.subject, usage, at=at)


async def build_runtime_usage_governance(
    session: AsyncSession,
    subject: UsageLimitSubject,
) -> RuntimeUsageGovernance:
    """Load persisted governance for a runtime subject."""

    policies = tuple(await load_usage_limit_policies(session, subject))
    aggregate_usage: dict[tuple[UsageLimitScope, UsageLimitPeriod], PortableUsage] = {}
    for period in {policy.aggregate_period for policy in policies}:
        for scope, usage in (
            await load_period_usage(session, subject, period=period)
        ).items():
            aggregate_usage[(scope, period)] = usage
    return RuntimeUsageGovernance(
        subject=subject,
        policies=policies,
        aggregate_usage_by_scope_period=aggregate_usage,
    )


def _intersect_optional(current: int | None, candidate: int | None) -> int | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate)


def _portable_usage_to_snapshot(usage: PortableUsage) -> SandboxRuntimeUsageSnapshot:
    return SandboxRuntimeUsageSnapshot(
        model_requests=usage.model_requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        runner_duration_ms=usage.runner_duration_ms,
        sandbox_compute_ms=usage.sandbox_compute_ms,
    )


def _portable_usage_from_snapshot(values: SandboxRuntimeUsageSnapshot) -> PortableUsage:
    return PortableUsage(
        model_requests=values.model_requests,
        input_tokens=values.input_tokens,
        output_tokens=values.output_tokens,
        cache_read_tokens=values.cache_read_tokens,
        cache_write_tokens=values.cache_write_tokens,
        runner_duration_ms=values.runner_duration_ms,
        sandbox_compute_ms=values.sandbox_compute_ms,
    )


def runtime_usage_governance_from_snapshot(
    subject: UsageLimitSubject,
    snapshot: SandboxRuntimeUsageGovernanceSnapshot | None,
) -> RuntimeUsageGovernance | None:
    """Reconstruct runtime governance from a DB-free runner context."""

    if not snapshot:
        return None
    policies = tuple(
        UsageLimitPolicy(
            scope=UsageLimitScope(item.scope),
            per_run=UsageCeilings(
                **item.per_run.model_dump(exclude_none=True),
            ),
            aggregate=UsageCeilings(
                **item.aggregate.model_dump(exclude_none=True),
            ),
            aggregate_period=UsageLimitPeriod(item.aggregate_period),
        )
        for item in snapshot.policies
    )
    aggregate_usage = {
        (
            UsageLimitScope(item.scope),
            UsageLimitPeriod(item.period),
        ): _portable_usage_from_snapshot(item.usage)
        for item in snapshot.aggregate_usage
    }
    return RuntimeUsageGovernance(
        subject=subject,
        policies=policies,
        aggregate_usage_by_scope_period=aggregate_usage,
    )


def observe_model_usage_for_governance(
    governance: RuntimeUsageGovernance | None,
    budget: AgentRunBudget,
    usage: PortableUsage,
) -> bool:
    """Apply post-response governance and force shared wind-down if needed."""

    if governance is None:
        return False
    at_limit = governance.observe_model_usage(usage)
    if at_limit:
        budget.control.force_wind_down = True
    return at_limit


def _absolute_ceiling(
    *,
    initial: int,
    allowance: int | None,
) -> int | None:
    if allowance is None:
        return None
    return initial + max(0, allowance)


def _intersect_per_run_winner(
    budget: AgentRunBudget,
    policies: tuple[UsageLimitPolicy, ...],
) -> AgentRunBudget:
    _scope, ceilings = resolve_effective_per_run_limit(list(policies))
    return AgentRunBudget(
        max_requests=_intersect_optional(
            budget.max_requests,
            _absolute_ceiling(
                initial=budget.initial_requests,
                allowance=ceilings.model_requests,
            ),
        ),
        max_total_tokens=_intersect_optional(
            budget.max_total_tokens,
            _absolute_ceiling(
                initial=budget.initial_total_tokens,
                allowance=ceilings.total_tokens,
            ),
        ),
        context_target_tokens=budget.context_target_tokens,
        warning_threshold=budget.warning_threshold,
        initial_requests=budget.initial_requests,
        initial_total_tokens=budget.initial_total_tokens,
        control=budget.control,
    )


async def _intersect_aggregate_policy(
    budget: AgentRunBudget,
    policy: UsageLimitPolicy,
    current: PortableUsage,
) -> AgentRunBudget:
    if not policy.aggregate.has_any():
        return budget
    return AgentRunBudget(
        max_requests=_intersect_optional(
            budget.max_requests,
            _absolute_ceiling(
                initial=budget.initial_requests,
                allowance=(
                    policy.aggregate.model_requests - current.model_requests
                    if policy.aggregate.model_requests is not None
                    else None
                ),
            ),
        ),
        max_total_tokens=_intersect_optional(
            budget.max_total_tokens,
            _absolute_ceiling(
                initial=budget.initial_total_tokens,
                allowance=(
                    policy.aggregate.total_tokens - current.total_tokens
                    if policy.aggregate.total_tokens is not None
                    else None
                ),
            ),
        ),
        context_target_tokens=budget.context_target_tokens,
        warning_threshold=budget.warning_threshold,
        initial_requests=budget.initial_requests,
        initial_total_tokens=budget.initial_total_tokens,
        control=budget.control,
    )


def runtime_usage_subject(
    *,
    organization_id,
    user_id,
    solution_id,
) -> UsageLimitSubject:
    return UsageLimitSubject(
        organization_id=organization_id,
        user_id=user_id,
        solution_id=solution_id,
    )


def runtime_usage_organization_id(
    *,
    resource_organization_id,
    requester_organization_id,
    target_kind: str | None = None,
):
    """Resolve the organization bucket for runtime usage accounting.

    Personal/private and global-repository Builder work may have a null
    Solution organization, but still belongs under the requester's org budget
    in the Platform → Org → User → Solution hierarchy when that actor has one.
    """

    if resource_organization_id is not None:
        return resource_organization_id
    return requester_organization_id


def terminal_usage_recorded_at() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "RuntimeUsageGovernance",
    "build_runtime_usage_governance",
    "observe_model_usage_for_governance",
    "runtime_usage_subject",
    "runtime_usage_organization_id",
    "runtime_usage_governance_from_snapshot",
    "terminal_usage_recorded_at",
]
