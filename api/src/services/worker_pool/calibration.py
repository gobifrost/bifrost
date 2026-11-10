"""A startup measurement belongs to the runtime that actually produced it."""

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.core.database import get_db_context
from src.models.orm.execution_workers import MANAGED_WORKER_FORMS, ExecutionWorker
from src.services.memory_requests import MemoryBudget
from src.worker.remote.fencing import fence_remote_owner


def measured_budget(worker: ExecutionWorker, environment: dict[str, Any]) -> MemoryBudget | None:
    calibration = worker.memory_calibration
    if not calibration or calibration.get("environment") != environment:
        return None
    sample = calibration.get("measurement")
    if not sample or sample.get("version") != environment["version"]:
        return None
    if sample.get("package_generation") != environment["package_generation"]:
        return None
    if sample.get("threshold") != environment["threshold"]:
        return None
    return MemoryBudget(
        limit_bytes=sample["limit_bytes"], baseline_bytes=sample["baseline_bytes"],
        threshold=sample["threshold"],
    )


async def record_memory_calibration(
    worker_id: UUID, budget: MemoryBudget, package_generation: int,
) -> None:
    """Record once, before claims open; heartbeats cannot relabel this snapshot."""
    async with get_db_context() as db:
        if not await fence_remote_owner(db, for_update=True):
            return
        worker = await db.scalar(select(ExecutionWorker).where(
            ExecutionWorker.id == worker_id,
            ExecutionWorker.form.in_(MANAGED_WORKER_FORMS),
            ExecutionWorker.state.in_(("starting", "ready", "draining")),
        ).with_for_update())
        if worker is None or not worker.memory_calibration:
            return
        calibration = worker.memory_calibration
        if "measurement" in calibration:
            return
        environment = calibration["environment"]
        if (
            environment["version"] != worker.version
            or environment["package_generation"] != package_generation
            or environment["threshold"] != budget.threshold
            or worker.memory_limit_bytes != budget.limit_bytes
        ):
            return
        worker.memory_calibration = {
            **calibration,
            "measurement": {
                "version": worker.version, "package_generation": package_generation,
                "threshold": budget.threshold, "limit_bytes": budget.limit_bytes,
                "baseline_bytes": budget.baseline_bytes,
            },
        }
        await db.commit()
