"""One reservation coordinator for Kubernetes plus optional external capacity.

The coordinator is intentionally narrow: it owns the lock-held demand snapshot
and durable worker reservations.  Provider adapters own all physical I/O only
after that transaction has committed. Provider HTTP calls never hold the
shared capacity reservation lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from src.services.kubernetes_execution import KubernetesPolicy, load_kubernetes_policy
from src.services.worker_pool.controller import WorkerCapacityController, plan_capacity_launches

logger = logging.getLogger(__name__)


ExternalRefresh = Callable[[], Awaitable[WorkerCapacityController | None]]


class HybridWorkerPoolCoordinator:
    """Coordinate ordered providers without composing independent planners."""

    def __init__(
        self, kubernetes: WorkerCapacityController, refresh_external: ExternalRefresh,
    ) -> None:
        self.kubernetes = kubernetes
        self._refresh_external = refresh_external
        self.external: WorkerCapacityController | None = None

    async def reconcile(self, policy: KubernetesPolicy) -> None:
        """Reserve once, then let each healthy provider reconcile its rows."""
        try:
            self.external = await self._refresh_external()
        except Exception:
            # A Cloud outage removes only overflow from this pass.  Kubernetes
            # still gets a fresh global snapshot and continues lifecycle work.
            logger.exception("External worker capacity refresh failed; retaining Kubernetes capacity")
            self.external = None
        providers = (self.kubernetes,) if self.external is None else (self.kubernetes, self.external)
        launched = await plan_capacity_launches(policy, providers)
        await self.kubernetes.reconcile_lifecycle(policy, launched[self.kubernetes])
        if self.external is not None:
            await self.external.reconcile_lifecycle(policy, launched[self.external])


async def hybrid_worker_pool_loop(stop_event: asyncio.Event, settings: object) -> None:
    """Run Kubernetes primary capacity with Cloud overflow in one loop.

    The external profile callback is deliberately a seam: the persisted
    provider resolver can enable/disable or replace Cloud capacity
    without creating a second planner loop.
    """
    import httpx

    from src.core.database import get_db_context
    from src.jobs.platform.kubernetes_client import KubernetesConfigurationError, KubernetesJobClient
    from src.services.work_dispatch import (
        DISPATCH_WAKE_FAMILIES, WAKE_PLATFORM_JOB, WAKE_SERVICE,
        listen_for_pool_wake, wait_for_next_pass,
    )
    from src.services.worker_pool.controller import RECONCILE_SECONDS, SWEEP_SECONDS, WorkerPoolController, _withdraw_live
    from src.services.worker_pool.external_client import ExternalCapacityClient
    from src.services.worker_pool.external_controller import ExternalWorkerPoolController
    from src.services.worker_pool.manifest import resolve_job_spec_when_ready

    typed_settings = settings  # keep the public loop convenient for startup tests
    try:
        kubernetes_client = KubernetesJobClient(typed_settings)  # type: ignore[arg-type]
    except KubernetesConfigurationError as exc:
        logger.exception("Hybrid worker controller disabled: %s", exc)
        await stop_event.wait()
        return
    external_controller: ExternalWorkerPoolController | None = None
    try:
        spec = await resolve_job_spec_when_ready(
            kubernetes_client, typed_settings, stop_event, "Hybrid worker controller"  # type: ignore[arg-type]
        )
        if spec is None:
            return
        kubernetes = WorkerPoolController(kubernetes_client, typed_settings, spec)  # type: ignore[arg-type]
        async with httpx.AsyncClient() as http:
            external_client: ExternalCapacityClient | None = None
            connection_identity: tuple[str, str, object] | None = None

            async def refresh_external() -> WorkerCapacityController | None:
                nonlocal external_client, external_controller, connection_identity
                from src.services.worker_pool.connection import load_capacity_provider_connection

                async with get_db_context() as db:
                    connection = await load_capacity_provider_connection(db, typed_settings)  # type: ignore[arg-type]
                if connection is None:
                    external_controller = None
                    external_client = None
                    connection_identity = None
                    return None
                identity = (connection.endpoint, connection.token, connection.generation)
                if identity != connection_identity:
                    external_client = ExternalCapacityClient(connection.endpoint, connection.token, http=http)
                    external_controller = None
                    connection_identity = identity
                assert external_client is not None
                if external_controller is None:
                    external_controller = ExternalWorkerPoolController(
                        external_client, typed_settings, await external_client.profile(),  # type: ignore[arg-type]
                        hybrid=True, rules=connection.rules,
                    )
                else:
                    external_controller.rules = connection.rules
                    await external_controller.refresh_profile()
                return external_controller

            coordinator = HybridWorkerPoolCoordinator(kubernetes, refresh_external)
            wake = asyncio.Event()
            listener = asyncio.create_task(listen_for_pool_wake(
                getattr(typed_settings, "redis_url"),
                {**{family: wake for family in DISPATCH_WAKE_FAMILIES}, WAKE_PLATFORM_JOB: wake, WAKE_SERVICE: wake},
            ), name="hybrid-worker-pool-controller-wake")
            try:
                while not stop_event.is_set():
                    started = time.monotonic()
                    try:
                        if await kubernetes.reachability.check():
                            async with get_db_context() as db:
                                policy = await load_kubernetes_policy(db)
                            await coordinator.reconcile(policy)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Hybrid worker capacity reconciliation failed")
                    await wait_for_next_pass(
                        stop_event, wake, since=started, spacing=RECONCILE_SECONDS, sweep=SWEEP_SECONDS,
                    )
            finally:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
                for provider in (kubernetes, external_controller):
                    if provider is not None:
                        try:
                            await _withdraw_live(provider.id)
                        except Exception:
                            logger.warning("Could not withdraw hybrid worker controller", exc_info=True)
    finally:
        await kubernetes_client.close()


__all__ = ["HybridWorkerPoolCoordinator", "hybrid_worker_pool_loop"]
