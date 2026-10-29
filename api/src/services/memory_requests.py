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

from sqlalchemy import BigInteger, case, cast, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.workflows import Workflow

MIB = 1024 * 1024

# A workflow with no measured runs, and inline scripts.
DEFAULT_WORKFLOW_REQUEST_BYTES = 64 * MIB
# Added to a workflow's learned peak growth.
WORKFLOW_REQUEST_MARGIN_BYTES = 16 * MIB
# An agent run adds what its child forked from the warm agent template
# allocates (the template's shared pages are not counted). Its heavy work
# (tool calls) is workflow runs, admitted on their own. A summary runs in
# the API process. Measured peak child private memory was 62-85 MiB
# (docs/benchmarks/2026-10-28-agent-runs-on-workers.md).
AGENT_RUN_REQUEST_BYTES = 96 * MIB
SUMMARY_REQUEST_BYTES = 16 * MIB
# Each smaller run moves the learned peak this fraction of the way down.
LEARNED_DECAY = 10
# A Job worker's own memory (parent and fork template) before one of that
# size has reported its budget.
JOB_BASELINE_ESTIMATE_BYTES = 768 * MIB
# Sized Job limits are rounded up to this.
JOB_LIMIT_STEP_BYTES = 256 * MIB
# Real memory at which the executor stops the run that has grown the most,
# before the kernel has to kill something.
DANGEROUS_MEMORY_RATIO = 0.95
# Where the worker controller publishes the largest budget it can launch.
LAUNCHABLE_KEY = "bifrost:worker-pool:largest-launchable-bytes"


def workflow_request_sql(workflow):  # type: ignore[no-untyped-def]
    """``workflow_request_bytes`` as a SQL expression over a workflow table."""
    # 64-bit throughout: the declared column is 32-bit MiB.
    return func.coalesce(
        cast(workflow.memory_request_mib, BigInteger) * MIB,
        workflow.learned_memory_bytes + WORKFLOW_REQUEST_MARGIN_BYTES,
        DEFAULT_WORKFLOW_REQUEST_BYTES,
    )


def workflow_request_bytes(
    learned_memory_bytes: int | None, declared_mib: int | None = None
) -> int:
    """A workflow run's request: the declared setting, else its learned peak
    growth plus a margin, else the default."""
    if declared_mib is not None:
        return declared_mib * MIB
    if learned_memory_bytes is None:
        return DEFAULT_WORKFLOW_REQUEST_BYTES
    return learned_memory_bytes + WORKFLOW_REQUEST_MARGIN_BYTES


async def record_peak_growth(
    session: AsyncSession, workflow_id: UUID, growth_bytes: int, code_hash: str | None
) -> None:
    """Fold one run's peak growth into its workflow's learned peak.

    A decaying maximum: a larger run raises it at once; a smaller one moves
    it 1/LEARNED_DECAY of the way down. The learned peak belongs to a code
    version: the first run of changed code (``code_hash``) replaces it, so
    a fixed memory problem is forgotten at once. One primary-key update.
    """
    learned = Workflow.learned_memory_bytes
    learned_hash = Workflow.learned_memory_code_hash
    new_code = learned_hash.is_distinct_from(code_hash) if code_hash is not None else False
    await session.execute(
        update(Workflow)
        .where(Workflow.id == workflow_id)
        .values(
            learned_memory_bytes=case(
                (learned.is_(None) | new_code | (learned <= growth_bytes), growth_bytes),
                else_=learned - (learned - growth_bytes) / LEARNED_DECAY,
            ),
            learned_memory_code_hash=func.coalesce(code_hash, learned_hash),
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


def sized_job_limit_bytes(
    request_bytes: int, threshold: float, baseline_bytes: int, max_limit_bytes: int
) -> int:
    """The smallest Job memory limit (a JOB_LIMIT_STEP_BYTES multiple) whose
    budget fits ``request_bytes``, never above ``max_limit_bytes`` (the
    planner sizes only requests that limit's budget holds)."""
    limit = JOB_LIMIT_STEP_BYTES
    while job_budget_bytes(limit, threshold, baseline_bytes) < request_bytes:
        limit += JOB_LIMIT_STEP_BYTES
    return min(limit, max_limit_bytes)


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


def memory_ratio() -> float:
    """Real memory in use: of the container's limit, or of the host's
    memory when the container has no limit."""
    from src.services.execution.memory_monitor import get_cgroup_memory

    current, limit = get_cgroup_memory()
    if limit > 0 and current >= 0:
        return current / limit
    info = _meminfo()
    return (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"]


def under_pressure(threshold: float) -> bool:
    return memory_ratio() > threshold


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
    "DANGEROUS_MEMORY_RATIO",
    "memory_ratio",
    "under_pressure",
    "workflow_request_bytes",
    "workflow_request_sql",
]
