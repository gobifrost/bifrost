"""How an executor admits work by memory (#769 stage 6).

Every kind of work (workflow runs, agent runs, summaries, local platform
jobs and services) is admitted through one gate:

1. Work reserves memory: its request, or what a running workflow run has
   actually grown by if that is more.
2. An executor claims work only while the work's request fits its free
   budget (budget minus what it holds) and its real memory is under the
   threshold, and only while its claims are open (a remote executor closes
   them while a reconnected launcher's inventory is reconciled).
3. Once it holds ``UNPAUSED_STARTS`` pieces of work, it waits
   ``START_PAUSE_SECONDS`` after each start (a workflow run starts when its
   child is forked) before claiming again, so the real-memory check sees
   what the last start did (Temporal's ``rampThrottle``).

Claims are serialized: a loop reads the free budget, claims from PostgreSQL
and holds the claimed request inside ``claiming()``, so two loops never
spend the same free bytes. See docs/plans/2026-10-23-769-stage6-resource-requests.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)

UNPAUSED_STARTS = 2
START_PAUSE_SECONDS = 0.1


@dataclass
class _Hold:
    request_bytes: int
    # A workflow run is held from its claim until its child ends; while it
    # is still being routed to a child, it counts as starting.
    workflow: bool
    starting: bool


class Admission:
    def __init__(
        self,
        *,
        budget_bytes: int,
        under_pressure: Callable[[], bool],
        running_workflow_ids: Callable[[], set[str]],
        workflow_growth: Callable[[], dict[str, int]],
        claims_open: Callable[[], bool] = lambda: True,
    ) -> None:
        """``running_workflow_ids`` and ``workflow_growth`` come from the
        process pool: the runs whose children are alive, and how much each
        has grown since it started. ``claims_open`` is false while the
        executor may not take new work for a reason other than memory."""
        self.budget_bytes = budget_bytes
        self._under_pressure = under_pressure
        self._claims_open = claims_open
        self._running_workflow_ids = running_workflow_ids
        self._workflow_growth = workflow_growth
        self._holds: dict[str, _Hold] = {}
        self._lock = asyncio.Lock()
        self._last_start = 0.0
        self._pressured = False

    def held_bytes(self) -> int:
        """What the held work reserves (rule 1)."""
        running = self._running_workflow_ids()
        for key in [k for k, h in self._holds.items() if h.workflow and not h.starting]:
            if key not in running:
                del self._holds[key]
        growth = self._workflow_growth()
        return sum(
            max(hold.request_bytes, growth.get(key, 0)) if hold.workflow else hold.request_bytes
            for key, hold in self._holds.items()
        )

    def free_bytes(self) -> int:
        return max(0, self.budget_bytes - self.held_bytes())

    def request_bytes(self, key: str) -> int | None:
        """Return the original request held for ``key``, if it is active."""
        hold = self._holds.get(key)
        return hold.request_bytes if hold is not None else None

    def pressured(self) -> bool:
        """Real memory over the threshold; logs each change."""
        pressured = self._under_pressure()
        if pressured != self._pressured:
            self._pressured = pressured
            if pressured:
                logger.warning("Executor is over its memory threshold; claiming nothing new")
            else:
                logger.info("Executor is back under its memory threshold")
        return pressured

    @asynccontextmanager
    async def claiming(self) -> AsyncIterator[int | None]:
        """Claim one piece of work: yields the bytes a claim may take, or
        None while claims are closed or memory is over the threshold (claim
        nothing). Hold what was claimed before leaving the block."""
        async with self._lock:
            await self._pace()
            if not self._claims_open():
                yield None
                return
            yield None if self.pressured() else self.free_bytes()

    async def _pace(self) -> None:
        """Rule 3: after ``UNPAUSED_STARTS``, wait for the last start to
        land and then ``START_PAUSE_SECONDS``."""
        while len(self._holds) >= UNPAUSED_STARTS:
            starting = any(hold.starting for hold in self._holds.values())
            remaining = START_PAUSE_SECONDS - (time.monotonic() - self._last_start)
            if not starting and remaining <= 0:
                return
            await asyncio.sleep(START_PAUSE_SECONDS if starting else remaining)
            # Work that ended meanwhile no longer counts.
            self.held_bytes()

    def hold(self, key: str, request_bytes: int, *, workflow: bool = False) -> None:
        """Hold claimed work's request. A workflow run starts when its child
        is forked (``started``); other work has started once held."""
        self._holds[key] = _Hold(request_bytes, workflow=workflow, starting=workflow)
        if not workflow:
            self._last_start = time.monotonic()

    def started(self, key: str) -> None:
        """A workflow run's child is forked (or routing ended): from here it
        is held until the child ends."""
        hold = self._holds.get(key)
        if hold is not None and hold.starting:
            hold.starting = False
            self._last_start = time.monotonic()

    def release(self, key: str) -> None:
        self._holds.pop(key, None)

    def starting_workflows(self) -> int:
        """Claimed workflow runs not yet routed to a child."""
        return sum(1 for hold in self._holds.values() if hold.workflow and hold.starting)


__all__ = ["Admission", "START_PAUSE_SECONDS", "UNPAUSED_STARTS"]
