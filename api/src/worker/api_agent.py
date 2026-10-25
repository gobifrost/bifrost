"""API-resident executor for agent runs and summaries.

The API process has one shared :class:`Admission`, so agent work is admitted
against the memory it actually uses. Remote launchers deliberately do not
claim these handlers: their capacity is reserved for workflow children,
services, and platform jobs.
"""

from __future__ import annotations

from src.services.memory_requests import MemoryBudget
from src.services.worker_pool.claims import register_static_worker
from src.worker.admission import Admission
from src.worker.pool_main import PoolWorker

API_AGENT_HANDLERS: tuple[tuple[str, int], ...] = (
    ("agent.run", 1),
    ("agent.summarize", 1),
    ("agent.summarize_backfill", 1),
)


class ApiAgentExecutor(PoolWorker):
    """A static executor sharing its API process's pre-claim memory gate."""

    def __init__(self, admission: Admission) -> None:
        super().__init__(
            handlers=API_AGENT_HANDLERS,
            admission=admission,
            hosts_remote_work=False,
        )

    async def _initialize_runtime(self) -> bool:
        """The FastAPI lifespan owns database initialization and shutdown."""
        self.incarnation_id = await register_static_worker(
            hostname="api-agent",
            supported_handlers=[handler for handler, _version in API_AGENT_HANDLERS],
        )
        return True

    async def _close_runtime(self) -> None:
        """Never close the database runtime shared with the API server."""

    def _measure_budget(self) -> MemoryBudget:
        # ``Admission`` was constructed from the API host's measured budget.
        # Reporting exactly that value keeps persistence and pre-claim gating
        # on one capacity definition.
        assert self.admission is not None
        return MemoryBudget(
            limit_bytes=self.admission.budget_bytes,
            baseline_bytes=0,
            threshold=1.0,
        )


__all__ = ["API_AGENT_HANDLERS", "ApiAgentExecutor"]
