"""The API-resident executor owns summary admission only; agent runs run on workers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

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


async def test_api_agent_executor_registers_its_own_build() -> None:
    executor = ApiAgentExecutor(_admission())
    worker_id = uuid4()
    with (
        patch("src.worker.api_agent.get_version", return_value="api-build"),
        patch("src.worker.api_agent.register_static_worker", new=AsyncMock(return_value=worker_id)) as register,
    ):
        assert await executor._initialize_runtime()

    assert executor.incarnation_id == worker_id
    assert register.await_args.kwargs["version"] == "api-build"


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
