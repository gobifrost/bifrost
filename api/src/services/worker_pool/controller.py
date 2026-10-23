"""Capacity controller for reusable workers (runs in the control plane).

One owner for worker capacity: every control replica runs this, and launch
decisions are serialized by a transaction advisory lock, so replicas never
over-launch. Each incarnation is a durable row first; its Job is created
suspended under a deterministic name, its UID persisted, and only then
started. A restarted controller adopts existing Jobs by name and UID.

Workers drain themselves when idle. The controller only marks a worker
``lost`` when it misses its start deadline or its heartbeat, or its Job
disappears; work it held is released by the dispatch reconciler, and runs it
had started follow their domain's terminal sweeps (never replayed).
"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from src.config import Settings, get_settings
from src.core.database import get_db_context
from src.jobs.platform.kubernetes_client import (
    KubernetesApiError,
    KubernetesConfigurationError,
    KubernetesJobClient,
)
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.work_dispatches import WorkDispatch
from src.services.worker_pool.claims import CAPACITY_LOCK
from src.services.worker_pool.manifest import (
    WorkerJobSpec,
    build_worker_job_manifest,
    resolve_worker_job_spec,
    worker_job_name,
)
from src.services.worker_pool.recovery import announce_ended_runs, end_runs_of_lost_worker

logger = logging.getLogger(__name__)

LIVE_STATES = ("reserved", "starting", "ready", "draining")
HEARTBEAT_LOST_AFTER = timedelta(seconds=30)
# How long a lost worker's Job is kept under watch for deletion.
LOST_JOB_CLEANUP = timedelta(hours=1)
RECONCILE_SECONDS = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerPoolController:
    def __init__(
        self, client: KubernetesJobClient, settings: Settings, spec: WorkerJobSpec
    ) -> None:
        self.client = client
        self.settings = settings
        self.spec = spec

    async def reconcile(self) -> None:
        async with get_db_context() as db:
            ids = (
                await db.scalars(
                    select(ExecutionWorker.id).where(
                        ExecutionWorker.form == "job",
                        ExecutionWorker.kubernetes_namespace == self.client.namespace,
                        ExecutionWorker.state.in_(LIVE_STATES)
                        | (
                            (ExecutionWorker.state == "lost")
                            & (ExecutionWorker.ended_at >= _now() - LOST_JOB_CLEANUP)
                        ),
                    )
                )
            ).all()
        for worker_id in ids:
            await self.reconcile_worker(worker_id)
        for worker_id in await self.launch_for_demand():
            await self.reconcile_worker(worker_id)

    async def reconcile_worker(self, worker_id: UUID) -> None:
        async with get_db_context() as db:
            worker = await db.scalar(
                select(ExecutionWorker)
                .where(ExecutionWorker.id == worker_id)
                .with_for_update(skip_locked=True)
            )
            if worker is None or worker.kubernetes_job_name is None:
                return
            name = worker.kubernetes_job_name
            remote = await self.client.get_job(name)
            if remote is not None and worker.kubernetes_job_uid is not None:
                if remote["metadata"]["uid"] != worker.kubernetes_job_uid:
                    # Never adopt or delete a replacement that reused the name.
                    raise RuntimeError("Worker Job UID does not match its incarnation")
            now = _now()

            if worker.state == "lost":
                if remote is not None:
                    await self.client.delete_job(name, remote["metadata"]["uid"])
                return

            if worker.state == "reserved":
                if remote is None:
                    remote = await self.client.create_job(
                        build_worker_job_manifest(worker.id, worker.slots, self.settings, self.spec)
                    )
                worker.kubernetes_job_uid = remote["metadata"]["uid"]
                worker.state = "starting"
                await db.commit()
                # The binding is durable; only now may the pod start.
                await self.client.start_job(name, remote["metadata"]["uid"])
                return

            reason = None
            if remote is None:
                reason = "job_missing"
            elif worker.state == "starting":
                if remote.get("spec", {}).get("suspend"):
                    await self.client.start_job(name, remote["metadata"]["uid"])
                elif now - worker.created_at >= timedelta(
                    seconds=self.settings.pool_start_timeout_seconds
                ):
                    reason = "start_timeout"
            elif worker.heartbeat_at is None or now - worker.heartbeat_at >= HEARTBEAT_LOST_AFTER:
                reason = "heartbeat_lost"
            ended: list[UUID] = []
            if reason is not None:
                self._lose(worker, reason, now)
                ended = await end_runs_of_lost_worker(db, worker.id)
            await db.commit()
        await announce_ended_runs(ended)

    @staticmethod
    def _lose(worker: ExecutionWorker, reason: str, now: datetime) -> None:
        logger.warning(
            "Reusable worker lost",
            extra={"worker_id": str(worker.id), "reason": reason, "state": worker.state},
        )
        worker.state = "lost"
        worker.end_reason = reason
        worker.ended_at = now

    async def launch_for_demand(self) -> list[UUID]:
        """Reserve incarnations for unclaimed pool work, within ceilings."""
        async with get_db_context() as db:
            locked = (
                await db.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": CAPACITY_LOCK},
                )
            ).scalar_one()
            if not locked:
                return []
            demand = await db.scalar(
                select(func.count(WorkDispatch.id)).where(
                    WorkDispatch.assigned_worker_id.is_(None),
                )
            ) or 0
            workers = (
                await db.execute(
                    select(
                        ExecutionWorker.form,
                        ExecutionWorker.state,
                        ExecutionWorker.slots,
                        ExecutionWorker.active_runs,
                        ExecutionWorker.memory_pressured,
                    ).where(ExecutionWorker.state.in_(LIVE_STATES))
                )
            ).all()
            # Static executors claim pool work too, so their free slots count;
            # the Job ceiling applies to Jobs only. An executor over its memory
            # threshold claims nothing, so it has no free slots.
            free = sum(
                slots if state in ("reserved", "starting") else max(0, slots - active)
                for _form, state, slots, active, pressured in workers
                if state != "draining" and not pressured
            )
            jobs = sum(1 for form, *_rest in workers if form == "job")
            undrained_jobs = sum(
                1 for form, state, *_rest in workers if form == "job" and state != "draining"
            )
            slots = self.settings.pool_worker_slots
            needed = max(
                math.ceil(max(0, demand - free) / slots),
                self.settings.pool_min_workers - undrained_jobs,
            )
            launch = min(needed, self.settings.pool_max_workers - jobs)
            launched: list[UUID] = []
            for _ in range(max(0, launch)):
                worker_id = uuid4()
                db.add(
                    ExecutionWorker(
                        id=worker_id,
                        form="job",
                        state="reserved",
                        resource_class="standard",
                        slots=slots,
                        kubernetes_namespace=self.client.namespace,
                        kubernetes_job_name=worker_job_name(worker_id),
                    )
                )
                launched.append(worker_id)
            await db.commit()
        if launched:
            logger.info(
                "Launching reusable workers",
                extra={"count": len(launched), "demand": demand, "free_slots": free},
            )
        return launched


async def worker_pool_loop(stop_event: asyncio.Event, settings: Any | None = None) -> None:
    settings = settings or get_settings()
    try:
        client = KubernetesJobClient(settings)
        spec = await resolve_worker_job_spec(client, settings)
    except (KubernetesConfigurationError, KubernetesApiError) as exc:
        # Pool work waits visibly (and fails at its claim deadline) instead of
        # taking down the process that hosts this loop.
        logger.error("Reusable worker controller disabled: %s", exc)
        await stop_event.wait()
        return
    controller = WorkerPoolController(client, settings, spec)
    try:
        while not stop_event.is_set():
            try:
                await controller.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Reusable worker reconciliation failed (%s): %s",
                    type(exc).__name__,
                    exc,
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=RECONCILE_SECONDS)
    finally:
        await client.close()


__all__ = ["WorkerPoolController", "worker_pool_loop"]
