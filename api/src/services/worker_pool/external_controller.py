"""External resource adapter for canonical physical-worker capacity."""

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import and_, select, update

from shared.models import WorkerCapacityProfile, WorkerCapacityReservation
from src.config import Settings
from src.core.database import get_db_context
from src.models.orm.execution_workers import ExecutionWorker
from src.services.kubernetes_execution import KubernetesPolicy
from src.services.worker_pool.controller import (
    RECONCILE_SECONDS, SWEEP_SECONDS, LiveControllers, WorkerCapacityController, _now, _withdraw_live,
)
from src.services.worker_pool.external_client import CapacityProtocolError, ExternalCapacityClient
from src.services.worker_pool.external_policy import validate_external_policy
from src.services.worker_pool.liveness import silent_executor
from src.services.worker_pool.recovery import announce_lost_runs, end_runs_of_lost_worker
from src.services.worker_pool.sizing import JobSizing
from src.worker.remote.protocol import WORKER_DRAIN_GRACE_SECONDS
from src.services.work_dispatch import listen_for_pool_wake, wait_for_next_pass

logger = logging.getLogger(__name__)


class ExternalWorkerPoolController(WorkerCapacityController):
    def __init__(self, client: ExternalCapacityClient, settings: Settings, profile: WorkerCapacityProfile) -> None:
        super().__init__(settings, worker_form="external")
        self.client = client
        self.profile = profile
        self.version = profile.worker_version
        self.live = LiveControllers(ids=frozenset({self.id}), versions=frozenset({self.version}))
        self.cleanup_states = ("lost", "retired")

    def _managed_scope(self) -> Any:
        return and_(ExecutionWorker.form == "external", ExecutionWorker.capacity_scope == self.profile.scope)

    def _cleanup_scope(self) -> Any:
        # An outage must not turn an unconfirmed physical deletion into an orphan.
        return and_(
            ExecutionWorker.state.in_(self.cleanup_states),
            ExecutionWorker.capacity_resource_deleted_at.is_(None),
        )

    def _binding_fields(self, worker_id: UUID) -> dict[str, Any]:
        return {"capacity_scope": self.profile.scope, "version": self.profile.worker_version}

    def _sizing(self, policy: KubernetesPolicy, workers: Any) -> JobSizing:
        try:
            validate_external_policy(
                policy, self.profile, max_lifetime_seconds=self.settings.pool_max_lifetime_seconds,
            )
        except ValueError as exc:
            raise CapacityProtocolError(f"Worker policy differs from provider capabilities: {exc}") from exc
        return super()._sizing(policy, workers)

    async def reconcile(self) -> None:
        current = await self.client.profile()
        if current.scope != self.profile.scope:
            raise CapacityProtocolError("Capacity provider scope changed; drain the original provider first")
        self.profile = current
        self.version = current.worker_version
        await super().reconcile()

    async def reconcile_worker(self, worker_id: UUID, policy: KubernetesPolicy) -> None:
        self._sizing(policy, [])
        # Provider I/O never holds a DB connection or worker row lock. UUID
        # addressing and conditional commits make concurrent reconcilers safe.
        async with get_db_context() as db:
            worker = await db.scalar(select(ExecutionWorker).where(
                ExecutionWorker.id == worker_id, self._managed_scope(),
            ))
            if worker is None:
                return
            state, uid, limit = worker.state, worker.capacity_resource_uid, worker.memory_limit_bytes
            version = worker.version
            created_at = worker.created_at
            deleted_at = worker.capacity_resource_deleted_at
        if state == "reserved" and version != self.profile.worker_version:
            # A provider rollout cannot relabel an already reserved incarnation.
            # Fence it before cleanup; queued demand may reserve a fresh UUID.
            await self._record_loss(worker_id, uid, "provider_version_changed", False)
            return
        if state in self.cleanup_states and deleted_at is not None:
            return
        resource = await self.client.get(worker_id, self.profile.scope)
        if uid is not None and resource is not None and resource.resource_uid != uid:
            raise CapacityProtocolError("Provider resource UID changed for an existing incarnation")
        if state in self.cleanup_states:
            if resource is not None and resource.state != "deleted":
                await self.client.remove(worker_id, self.profile.scope, resource.resource_uid)
            # GET absence/a deleted tombstone or a validated DELETE response confirms
            # cleanup. Persist only for this still-terminal, unchanged incarnation.
            async with get_db_context() as db:
                await db.execute(update(ExecutionWorker).where(
                    ExecutionWorker.id == worker_id, self._managed_scope(),
                    ExecutionWorker.state.in_(self.cleanup_states),
                    ExecutionWorker.capacity_resource_uid == uid,
                    ExecutionWorker.capacity_resource_deleted_at.is_(None),
                ).values(capacity_resource_deleted_at=_now()))
                await db.commit()
            return
        if resource is not None and resource.worker_version != version:
            raise CapacityProtocolError("Provider resource build changed for an existing incarnation")
        if state == "reserved":
            created = resource is None
            if resource is None:
                if limit is None:
                    raise CapacityProtocolError("Reserved worker has no immutable memory limit")
                resource = await self.client.prepare(WorkerCapacityReservation(
                    worker_id=worker_id, worker_version=version,
                    memory_limit_bytes=limit,
                    max_lifetime_seconds=self.settings.pool_max_lifetime_seconds,
                    drain_grace_seconds=WORKER_DRAIN_GRACE_SECONDS,
                ), self.profile.scope)
            if resource.state != "prepared":
                async with get_db_context() as db:
                    fresh = await db.scalar(select(ExecutionWorker.state).where(ExecutionWorker.id == worker_id))
                if fresh != "reserved":
                    return
                reason = "provider_started_before_binding" if resource.state in ("starting", "running") else "provider_resource_stopped"
                await self._record_loss(worker_id, uid, reason, False)
                return
            async with get_db_context() as db:
                bound = await db.scalar(update(ExecutionWorker).where(
                    ExecutionWorker.id == worker_id, self._managed_scope(),
                    ExecutionWorker.state == "reserved", ExecutionWorker.capacity_resource_uid.is_(None),
                ).values(
                    capacity_resource_uid=resource.resource_uid, state="starting",
                    launched_by=self.id if created else None,
                ).returning(ExecutionWorker.id))
                await db.commit()
            if bound is not None:
                await self.client.start(worker_id, self.profile.scope, resource.resource_uid)
            return
        if state not in ("starting", "ready", "draining"):
            return
        reason = None
        if resource is None or resource.state in ("deleted", "stopped"):
            reason = "provider_resource_stopped"
        elif state == "starting":
            if _now() - created_at >= timedelta(seconds=self.settings.pool_start_timeout_seconds):
                reason = "start_timeout"
            elif resource.state == "prepared":
                assert uid is not None
                await self.client.start(worker_id, self.profile.scope, uid)
                return
        elif resource.state == "prepared":
            reason = "provider_resource_regressed"
        elif self.reachability.steady():
            reason = "heartbeat_lost"
        if reason is not None:
            await self._record_loss(worker_id, uid, reason, resource is not None and resource.out_of_memory)

    async def _record_loss(self, worker_id: UUID, uid: str | None, reason: str, out_of_memory: bool) -> None:
        async with get_db_context() as db:
            worker = await db.scalar(select(ExecutionWorker).where(
                ExecutionWorker.id == worker_id, self._managed_scope(),
                ExecutionWorker.capacity_resource_uid == uid,
                ExecutionWorker.state.in_(("reserved", "starting", "ready", "draining")),
            ).with_for_update())
            if worker is None:
                return
            if reason == "heartbeat_lost" and not await db.scalar(select(ExecutionWorker.id).where(
                ExecutionWorker.id == worker_id, silent_executor(),
            )):
                return
            if reason == "start_timeout" and worker.state != "starting":
                return
            self._lose(worker, "out_of_memory" if out_of_memory else reason, _now())
            ended = await end_runs_of_lost_worker(db, worker.id, out_of_memory=out_of_memory)
            await db.commit()
        await announce_lost_runs(ended)


async def external_worker_pool_loop(stop_event: asyncio.Event, settings: Settings) -> None:
    """Run only when explicitly selected; Kubernetes is never a failure fallback."""
    assert settings.worker_capacity_url and settings.worker_capacity_token
    async with httpx.AsyncClient() as http:
        client = ExternalCapacityClient(
            settings.worker_capacity_url, settings.worker_capacity_token.get_secret_value(), http=http,
        )
        controller: ExternalWorkerPoolController | None = None
        wake = asyncio.Event()
        listener = asyncio.create_task(listen_for_pool_wake(settings.redis_url, wake))
        try:
            while not stop_event.is_set():
                started = time.monotonic()
                try:
                    if controller is None:
                        controller = ExternalWorkerPoolController(client, settings, await client.profile())
                    if await controller.reachability.check():
                        await controller.reconcile()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("External worker capacity reconciliation failed")
                await wait_for_next_pass(
                    stop_event, wake, since=started, spacing=RECONCILE_SECONDS, sweep=SWEEP_SECONDS,
                )
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            if controller is not None:
                await _withdraw_live(controller.id)
