"""Per-run usage summary: cost, peak context, and cache hit rate.

A run's summed token count re-counts the whole conversation on every model
call, so it grows with the number of calls rather than with the work done or
its price. The single-run surfaces show what a user can act on instead:

- ``cost``: everything the run spent, including delegated sub-runs at any
  depth and the post-run summarizer.
- ``peak_context_tokens``: the largest context this run's own conversation
  sent to the model in one call. Delegates have their own context windows,
  so they are not included.
- ``cache_hit_rate``: the share of input tokens across the run tree that was
  read from the provider's prompt cache. Input tokens include cached tokens
  for every provider (Pydantic AI normalizes this), so the ratio is
  comparable across providers.
"""

from __future__ import annotations

from collections.abc import Collection
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Integer, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.agent_runs import AgentRunUsageSummary
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.ai_usage import AIUsage


async def summarize_run_usage(
    session: AsyncSession,
    run_ids: Collection[UUID],
) -> dict[UUID, AgentRunUsageSummary]:
    """Return a usage summary for each run that recorded any model usage.

    Runs with no usage rows (queued, running, or never called a model) are
    omitted from the result.
    """
    if not run_ids:
        return {}
    roots = list(run_ids)

    tree = (
        select(
            AgentRun.id.label("root_id"),
            AgentRun.id.label("run_id"),
        )
        .where(AgentRun.id.in_(roots))
        .cte("run_tree", recursive=True)
    )
    tree = tree.union_all(
        select(tree.c.root_id, AgentRun.id).join(
            tree, AgentRun.parent_run_id == tree.c.run_id
        )
    )

    usage_rows = await session.execute(
        select(
            tree.c.root_id,
            func.sum(AIUsage.cost).label("cost"),
            func.sum(AIUsage.cost)
            .filter(tree.c.run_id != tree.c.root_id)
            .label("delegate_cost"),
            func.sum(AIUsage.input_tokens).label("input_tokens"),
            func.sum(AIUsage.cache_read_tokens).label("cache_read_tokens"),
        )
        .join(AIUsage, AIUsage.agent_run_id == tree.c.run_id)
        .group_by(tree.c.root_id)
    )

    input_tokens = literal_column("(agent_run_steps.content -> 'usage' ->> 'input_tokens')")
    context_rows = await session.execute(
        select(
            AgentRunStep.run_id,
            func.max(cast(input_tokens, Integer)).label("peak_context_tokens"),
        )
        .where(
            AgentRunStep.run_id.in_(roots),
            AgentRunStep.type == "llm_response",
        )
        .group_by(AgentRunStep.run_id)
    )
    peak_context = {row.run_id: row.peak_context_tokens for row in context_rows}

    summaries: dict[UUID, AgentRunUsageSummary] = {}
    for row in usage_rows:
        total_input = int(row.input_tokens or 0)
        summaries[row.root_id] = AgentRunUsageSummary(
            cost=_money(row.cost),
            delegate_cost=_money(row.delegate_cost),
            peak_context_tokens=peak_context.get(row.root_id),
            cache_hit_rate=(
                int(row.cache_read_tokens or 0) / total_input if total_input else None
            ),
        )
    return summaries


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
