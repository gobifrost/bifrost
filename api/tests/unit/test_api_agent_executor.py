"""The API-resident executor owns summary admission only; agent runs run on workers."""

from __future__ import annotations

import asyncio

from src.services.memory_requests import MIB
from src.worker.admission import Admission
from src.worker.api_agent import ApiAgentExecutor


def _admission(*, budget: int = 256 * MIB) -> Admission:
    return Admission(
        budget_bytes=budget,
        under_pressure=lambda: False,
        running_workflow_ids=set,
        workflow_growth=dict,
    )


def test_api_agent_executor_advertises_only_summaries() -> None:
    executor = ApiAgentExecutor(_admission())

    assert executor._claimable() == (
        ("agent.summarize", 1),
        ("agent.summarize_backfill", 1),
    )
    assert not executor.hosts_remote_work


def test_api_agent_executor_shares_the_supplied_api_admission() -> None:
    admission = _admission()
    executor = ApiAgentExecutor(admission)

    assert executor.admission is admission
    admission.hold("already-running", 256 * MIB)
    assert executor.admission.free_bytes() == 0


async def test_api_agent_executors_cannot_spend_one_api_budget_twice() -> None:
    admission = _admission(budget=100 * MIB)
    first = ApiAgentExecutor(admission)
    second = ApiAgentExecutor(admission)
    seen: list[int | None] = []

    async def reserve(executor: ApiAgentExecutor, key: str) -> None:
        assert executor.admission is admission
        async with admission.claiming() as fits:
            seen.append(fits)
            if fits is not None and fits >= 60 * MIB:
                admission.hold(key, 60 * MIB)
            await asyncio.sleep(0)

    await asyncio.gather(reserve(first, "first"), reserve(second, "second"))

    assert seen == [100 * MIB, 40 * MIB]
    assert admission.held_bytes() == 60 * MIB
