"""Local host-side transport experiment; never an authoritative work queue.

Only canonical owners may launch or acknowledge a physical route. Retained
routes survive transport reconnects, not process loss. Process loss requires
canonical recovery; this object must never replay execution from its contents.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.worker.remote.fencing import LeaseRef, remote_owner_context
from src.worker.remote.ownership import RemoteOwnerLease, owns_remote_session
from src.worker.remote.protocol import CredentialRouteChanged

if TYPE_CHECKING:
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
    from src.services.execution.process_pool import ExecutionInfo


class AttemptResult:
    """Shared result receipt; cancelling a waiter does not cancel collection.

    This protects transport ownership only. It neither stops the provider nor
    establishes physical termination, and intentionally exposes no cancel API.
    """

    def __init__(self, task: asyncio.Task[dict[str, Any]]) -> None:
        self._task = task

    def __await__(self):
        return asyncio.shield(self._task).__await__()


@dataclass
class Route:
    execution_id: str
    task: asyncio.Task[dict[str, Any]]
    receipt: AttemptResult
    acknowledged: bool = False


class AttemptJournal:
    """Bounded physical-route deduplication, including completed route tombstones.

    The canonical API must validate attempt ownership before supplying a route.
    Session fencing here is additional protection, not attempt authorization.
    No cancellation operation is exposed: task cancellation cannot prove physical
    termination of a Python isolate.
    """

    def __init__(self, *, capacity: int = 16) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.routes: dict[str, Route] = {}
        self._lock = asyncio.Lock()
        self._retired = False

    async def dispatch(
        self, lease: RemoteOwnerLease, route_id: str, execution_id: str,
        launch: Callable[[], Awaitable[dict[str, Any]]],
    ) -> AttemptResult:
        async with self._lock:
            if self._retired:
                raise RuntimeError("launcher retired")
            if not await owns_remote_session(lease):
                raise CredentialRouteChanged("stale physical dispatch owner")
            previous = self.routes.get(route_id)
            if previous is not None:
                if previous.execution_id != execution_id:
                    raise ValueError("route identity conflict")
                return previous.receipt
            if len(self.routes) >= self.capacity:
                raise RuntimeError("journal full; canonical retirement required")
            task = asyncio.create_task(launch())
            receipt = AttemptResult(task)
            self.routes[route_id] = Route(execution_id, task, receipt)
            return receipt

    def inventory(self) -> list[dict[str, Any]]:
        """Report collection, never infer physical termination from task state."""
        entries = []
        for key, route in self.routes.items():
            state, error_type = "pending", None
            if route.task.cancelled():
                state, error_type = "cancelled", "CancelledError"
            elif route.task.done():
                error = route.task.exception()
                state = "result_available" if error is None else "failed"
                error_type = type(error).__name__ if error is not None else None
            entries.append({"route_id": key, "execution_id": route.execution_id,
                            "collection_state": state, "collection_error_type": error_type,
                            "physical_termination_verified": False, "acknowledged": route.acknowledged})
        return entries

    async def reconcile_terminal(
        self, lease: RemoteOwnerLease, route_id: str,
        accept: Callable[[str, str, dict[str, Any]], Awaitable[bool]],
    ) -> bool:
        """ACK only explicit canonical acceptance; exceptions retain the result.

        `accept` must use upstream transactional owner/attempt fencing and durable
        idempotency. A preflight session check alone cannot fence a commit.
        """
        async with self._lock:
            if not await owns_remote_session(lease):
                raise CredentialRouteChanged("stale terminal owner")
            route = self.routes[route_id]
            if route.acknowledged:
                return True
            if not route.task.done():
                return False
            result = route.task.result()
            with remote_owner_context(LeaseRef(lease)):
                if not await accept(route_id, route.execution_id, result):
                    return False
            if not await owns_remote_session(lease):
                raise CredentialRouteChanged("owner replaced before terminal ACK")
            route.acknowledged = True
            return True

    async def retire(self) -> None:
        """Forget tombstones only when this whole launcher is canonically retired."""
        async with self._lock:
            if any(not route.acknowledged for route in self.routes.values()):
                raise RuntimeError("unacknowledged physical routes remain")
            self.routes.clear()
            self._retired = True

    async def reconcile_workflow(
        self, lease: RemoteOwnerLease, route_id: str,
        consumer: WorkflowExecutionConsumer, execution: ExecutionInfo,
    ) -> bool:
        """Use upstream's normal-return terminal-consumption/ACK contract.

        A consumed stale replay is not a claim that this result was persisted.
        This mirrors Bridge._deliver_terminal; exceptions retain transport state.
        ExecutionInfo and consumer identity belong to the canonical parent, never
        the guest. The caller must acquire the dispatch claim before launching.
        """
        if consumer.owner_worker_id != lease.worker_id:
            raise ValueError("consumer belongs to another worker")

        async def consume(_route: str, subject: str, result: dict[str, Any]) -> bool:
            if execution.execution_id != subject or result.get("execution_id") != subject:
                raise ValueError("terminal execution identity conflict")
            if not isinstance(result.get("success"), bool):
                raise ValueError("canonical result envelope required")
            await consumer._handle_result(execution.attach_transport_metadata(result))
            return True

        return await self.reconcile_terminal(lease, route_id, consume)

    async def reconcile_retained_workflow(
        self, lease: RemoteOwnerLease, route_id: str, event: dict[str, Any],
        consumer: WorkflowExecutionConsumer, execution: ExecutionInfo,
    ) -> bool:
        """Consume an upstream launcher-retained result after collection failed.

        Only a trusted parent receiving the authenticated upstream transport may
        supply this event. This is not a guest/public result-submission API.
        No result is reconstructed from the failed task and no code is rerun.
        The caller ACKs the upstream event only after this returns True.
        """
        if consumer.owner_worker_id != lease.worker_id:
            raise ValueError("consumer belongs to another worker")
        if (event.get("event") != "result" or event.get("route_id") != route_id
            or not isinstance(event.get("_event_id"), str) or not event["_event_id"]):
            raise ValueError("upstream retained result event required")
        result = event.get("result")
        if (not isinstance(result, dict) or result.get("execution_id") != execution.execution_id
            or not isinstance(result.get("success"), bool)):
            raise ValueError("canonical retained result envelope required")
        async with self._lock:
            if not await owns_remote_session(lease):
                raise CredentialRouteChanged("stale retained result owner")
            route = self.routes[route_id]
            if route.execution_id != execution.execution_id:
                raise ValueError("retained execution identity conflict")
            if route.acknowledged:
                return True
            if not route.task.done():
                return False
            if not route.task.cancelled() and route.task.exception() is None:
                raise ValueError("local result already available; use normal reconciliation")
            with remote_owner_context(LeaseRef(lease)):
                await consumer._handle_result(execution.attach_transport_metadata(result))
            if not await owns_remote_session(lease):
                raise CredentialRouteChanged("owner replaced before retained result ACK")
            route.acknowledged = True
            return True
