"""One sizing rule for every Kubernetes Job that runs Bifrost work.

Worker Jobs (the reusable executors) and isolated pods (one build-class
platform job each) are sized by the same code:

- **Standard:** request the deployment's worker memory request, limit
  *Worker memory* (Burstable).
- **Sized:** work a standard Job's budget cannot hold gets the smallest
  limit whose budget does (``sized_job_limit_bytes``), up to *Largest
  worker*, and requests that whole limit, so it is scheduled only where the
  memory exists.
- **Too large:** work no Job up to *Largest worker* can hold is failed
  visibly (``unfit``), never launched.

A Job's budget is its limit at the memory threshold less its own process
baseline, which Job workers report once ready.

A worker Job's row records its class and limit when it is reserved
(``kubernetes_client.memory_resources`` renders them); an isolated pod's
row records its request and limit when it is claimed
(``platform_jobs.IsolatedPods``). Either is created as recorded, whatever
the settings are by then.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.execution_workers import ExecutionWorker
from src.services.memory_requests import (
    JOB_BASELINE_ESTIMATE_BYTES,
    MIB,
    job_budget_bytes,
    sized_job_limit_bytes,
)

if TYPE_CHECKING:
    from src.services.kubernetes_execution import KubernetesPolicy

LIVE_STATES = ("reserved", "starting", "ready", "draining")


def launch_limit(worker: Any, standard_limit: int) -> int:
    """The memory limit a Job worker launched (or will launch) with. A
    standard Job reserved by the release before Settings -> Kubernetes
    recorded none; it takes the current Worker memory."""
    return worker.memory_limit_bytes or standard_limit


def job_baseline(workers: Any, standard_limit: int, threshold: float) -> int:
    """A Job's own memory, from the largest any Job worker reported (its
    limit at the threshold, less the budget it measured)."""
    reported = [
        int(launch_limit(worker, standard_limit) * threshold) - worker.memory_budget_bytes
        for worker in workers
        if worker.form == "job" and worker.memory_budget_bytes is not None
    ]
    return max(reported, default=JOB_BASELINE_ESTIMATE_BYTES)


@dataclass(frozen=True)
class JobSizing:
    """Settings -> Kubernetes sizes, at the threshold and baseline in effect."""

    standard_limit_bytes: int
    largest_limit_bytes: int
    threshold: float
    baseline_bytes: int

    @classmethod
    def of(cls, policy: KubernetesPolicy, threshold: float, baseline_bytes: int) -> JobSizing:
        standard = policy.worker_memory_mib * MIB
        return cls(
            standard_limit_bytes=standard,
            largest_limit_bytes=max(standard, policy.largest_worker_mib * MIB),
            threshold=threshold,
            baseline_bytes=baseline_bytes,
        )

    def budget_bytes(self, limit_bytes: int) -> int:
        return job_budget_bytes(limit_bytes, self.threshold, self.baseline_bytes)

    @property
    def standard_budget_bytes(self) -> int:
        return self.budget_bytes(self.standard_limit_bytes)

    @property
    def largest_budget_bytes(self) -> int:
        return self.budget_bytes(self.largest_limit_bytes)

    def sized_limit_bytes(self, request_bytes: int) -> int:
        return sized_job_limit_bytes(
            request_bytes, self.threshold, self.baseline_bytes, self.largest_limit_bytes
        )

    def pod_limit_bytes(self, request_bytes: int) -> int | None:
        """The limit of the one Job that holds ``request_bytes``: standard
        when its budget holds it, else sized; None when no Job can."""
        if request_bytes <= self.standard_budget_bytes:
            return self.standard_limit_bytes
        if request_bytes <= self.largest_budget_bytes:
            return self.sized_limit_bytes(request_bytes)
        return None


async def load_job_sizing(
    db: AsyncSession, policy: KubernetesPolicy, threshold: float
) -> JobSizing:
    """Sizes for this pass, with the baseline live Job workers report."""
    workers = (
        await db.execute(select(ExecutionWorker).where(ExecutionWorker.state.in_(LIVE_STATES)))
    ).scalars().all()
    baseline = job_baseline(workers, policy.worker_memory_mib * MIB, threshold)
    return JobSizing.of(policy, threshold, baseline)


__all__ = [
    "JobSizing",
    "LIVE_STATES",
    "job_baseline",
    "launch_limit",
    "load_job_sizing",
]
