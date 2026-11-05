"""An executor's memory gate (#769 stage 6, src.worker.admission)."""

from __future__ import annotations

import asyncio
import time

from src.services.memory_requests import MIB
from src.worker import admission as admission_module
from src.worker.admission import START_PAUSE_SECONDS, UNPAUSED_STARTS, Admission


def _gate(
    *,
    budget: int = 1000 * MIB,
    running: set[str] | None = None,
    growth: dict[str, int] | None = None,
    pressured: list[bool] | None = None,
) -> Admission:
    children = running if running is not None else set()
    grown = growth if growth is not None else {}
    pressure = pressured if pressured is not None else [False]
    return Admission(
        budget_bytes=budget,
        under_pressure=lambda: pressure[0],
        running_workflow_ids=lambda: set(children),
        workflow_growth=lambda: dict(grown),
    )


def test_a_run_reserves_its_request_or_its_growth_when_larger() -> None:
    gate = _gate(running={"small", "grown"}, growth={"small": 10 * MIB, "grown": 300 * MIB})
    gate.hold("small", 64 * MIB, workflow=True)
    gate.hold("grown", 64 * MIB, workflow=True)
    gate.started("small")
    gate.started("grown")
    gate.hold("agent", 32 * MIB)

    assert gate.held_bytes() == 64 * MIB + 300 * MIB + 32 * MIB


def test_a_workflow_run_is_held_while_routed_and_until_its_child_ends() -> None:
    children: set[str] = set()
    gate = _gate(running=children)
    gate.hold("run", 100 * MIB, workflow=True)
    assert gate.held_bytes() == 100 * MIB, "no child yet: still being routed"

    children.add("run")
    gate.started("run")
    assert gate.held_bytes() == 100 * MIB

    children.clear()
    assert gate.held_bytes() == 0
    assert gate.starting_workflows() == 0


def test_request_bytes_reports_the_request_for_an_active_hold() -> None:
    gate = _gate()
    gate.hold("workflow", 100 * MIB, workflow=True)

    assert gate.request_bytes("workflow") == 100 * MIB
    gate.release("workflow")
    assert gate.request_bytes("workflow") is None


async def test_over_the_threshold_a_claim_takes_nothing() -> None:
    pressured = [True]
    gate = _gate(pressured=pressured)
    async with gate.claiming() as fits:
        assert fits is None

    pressured[0] = False
    async with gate.claiming() as fits:
        assert fits == 1000 * MIB


async def test_claims_are_serialized_so_two_loops_never_spend_the_same_bytes() -> None:
    gate = _gate(budget=100 * MIB)
    seen: list[int | None] = []

    async def claim(key: str) -> None:
        async with gate.claiming() as fits:
            seen.append(fits)
            await asyncio.sleep(0)  # the database claim
            gate.hold(key, 60 * MIB)

    await asyncio.gather(claim("a"), claim("b"))

    assert seen == [100 * MIB, 40 * MIB]


async def test_after_a_few_starts_claims_wait_for_the_last_start_to_land(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(admission_module, "START_PAUSE_SECONDS", 0.05)
    children: set[str] = set()
    gate = _gate(running=children)
    for index in range(UNPAUSED_STARTS):
        gate.hold(f"task-{index}", MIB)
    gate.hold("run", MIB, workflow=True)

    claimed = asyncio.Event()

    async def claim() -> None:
        async with gate.claiming():
            claimed.set()

    waiter = asyncio.create_task(claim())
    await asyncio.sleep(0.15)
    assert not claimed.is_set(), "the last run is still being routed to its child"

    children.add("run")
    landed = time.monotonic()
    gate.started("run")
    await asyncio.wait_for(waiter, timeout=1)
    assert time.monotonic() - landed >= 0.05


async def test_the_first_starts_are_not_paused() -> None:
    gate = _gate()
    began = time.monotonic()
    for index in range(UNPAUSED_STARTS):
        async with gate.claiming():
            gate.hold(f"task-{index}", MIB)
    assert time.monotonic() - began < START_PAUSE_SECONDS
