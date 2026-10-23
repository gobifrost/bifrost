"""Work requests memory from an executor (#769 stage 6).

Every dispatch carries the memory its work is expected to add. An executor
claims work while the request fits its free budget (budget minus what the
work it holds requested) and its real memory is under the threshold; the
worker controller sizes capacity from the same numbers. See
docs/plans/2026-10-23-769-stage6-resource-requests.md.

Requests are typical, not worst case: the real-memory check catches the
unusual run, and the kernel stays the backstop.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.workflows import Workflow

MIB = 1024 * 1024

# A workflow with no measured runs, and inline scripts.
DEFAULT_WORKFLOW_REQUEST_BYTES = 64 * MIB
# Added to a workflow's learned peak growth.
WORKFLOW_REQUEST_MARGIN_BYTES = 16 * MIB
# Agent runs and summaries run in the executor's own process; their heavy
# work (tool calls) is workflow runs, admitted on their own.
AGENT_RUN_REQUEST_BYTES = 32 * MIB
SUMMARY_REQUEST_BYTES = 16 * MIB
# Each smaller run moves the learned peak this fraction of the way down.
LEARNED_DECAY = 20
# A Job worker's own memory (parent and fork template) before one of that
# size has reported its budget.
JOB_BASELINE_ESTIMATE_BYTES = 768 * MIB
# Sized Job limits are rounded up to this.
JOB_LIMIT_STEP_BYTES = 256 * MIB
# Where the worker controller publishes the largest budget it can launch.
LAUNCHABLE_KEY = "bifrost:worker-pool:largest-launchable-bytes"


def workflow_request_bytes(learned_memory_bytes: int | None) -> int:
    """A workflow run's request: its learned peak growth plus a margin."""
    if learned_memory_bytes is None:
        return DEFAULT_WORKFLOW_REQUEST_BYTES
    return learned_memory_bytes + WORKFLOW_REQUEST_MARGIN_BYTES


async def record_peak_growth(session: AsyncSession, workflow_id: UUID, growth_bytes: int) -> None:
    """Fold one run's peak growth into its workflow's learned peak.

    A decaying maximum: a larger run raises it at once; a smaller one moves
    it 1/LEARNED_DECAY of the way down. One primary-key update, no scan.
    """
    learned = Workflow.learned_memory_bytes
    await session.execute(
        update(Workflow)
        .where(Workflow.id == workflow_id)
        .values(
            learned_memory_bytes=case(
                (learned.is_(None) | (learned <= growth_bytes), growth_bytes),
                else_=learned - (learned - growth_bytes) / LEARNED_DECAY,
            )
        )
    )


@dataclass(frozen=True)
class MemoryBudget:
    """What an executor may hand to work, measured once it is ready."""

    limit_bytes: int
    baseline_bytes: int
    threshold: float

    @property
    def budget_bytes(self) -> int:
        return max(0, int(self.limit_bytes * self.threshold) - self.baseline_bytes)


def job_budget_bytes(limit_bytes: int, threshold: float, baseline_bytes: int) -> int:
    """The budget a Job worker with this memory limit will report."""
    return max(0, int(limit_bytes * threshold) - baseline_bytes)


def sized_job_limit_bytes(request_bytes: int, threshold: float, baseline_bytes: int) -> int:
    """The smallest Job memory limit whose budget fits ``request_bytes``."""
    needed = -(-(request_bytes + baseline_bytes) * 100 // int(threshold * 100))
    return -(-needed // JOB_LIMIT_STEP_BYTES) * JOB_LIMIT_STEP_BYTES


def measure_budget(threshold: float) -> MemoryBudget:
    """The container's memory limit (the host's memory when it has none),
    and what this executor already uses."""
    from src.services.execution.memory_monitor import get_cgroup_memory

    current, limit = get_cgroup_memory()
    if limit <= 0:
        limit = host_memory_bytes()
    if current < 0:
        current = 0
    return MemoryBudget(limit_bytes=limit, baseline_bytes=current, threshold=threshold)


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    with open("/proc/meminfo") as meminfo:
        for line in meminfo:
            key, _, rest = line.partition(":")
            values[key] = int(rest.split()[0]) * 1024
    return values


def host_memory_bytes() -> int:
    return _meminfo()["MemTotal"]


def under_pressure(threshold: float) -> bool:
    """Real memory over ``threshold`` of the container's limit, or of the
    host's memory when the container has no limit."""
    from src.services.execution.memory_monitor import get_cgroup_memory

    current, limit = get_cgroup_memory()
    if limit > 0 and current >= 0:
        return current / limit > threshold
    info = _meminfo()
    return (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"] > threshold


__all__ = [
    "AGENT_RUN_REQUEST_BYTES",
    "JOB_BASELINE_ESTIMATE_BYTES",
    "LAUNCHABLE_KEY",
    "job_budget_bytes",
    "sized_job_limit_bytes",
    "DEFAULT_WORKFLOW_REQUEST_BYTES",
    "MemoryBudget",
    "SUMMARY_REQUEST_BYTES",
    "WORKFLOW_REQUEST_MARGIN_BYTES",
    "host_memory_bytes",
    "measure_budget",
    "record_peak_growth",
    "under_pressure",
    "workflow_request_bytes",
]
