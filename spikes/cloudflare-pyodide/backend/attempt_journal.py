"""Local host-side transport experiment; never an authoritative work queue.

Only canonical owners may launch or acknowledge a physical route. Retained
routes survive transport reconnects, not process loss. Process loss requires
canonical recovery; this object must never replay execution from its contents.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.worker.remote.fencing import LeaseRef, remote_owner_context
from src.worker.remote.ownership import RemoteOwnerLease, owns_remote_session
from src.worker.remote.protocol import CredentialRouteChanged


@dataclass
class Route:
    execution_id: str
    task: asyncio.Task[dict[str, Any]]
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
    ) -> asyncio.Task[dict[str, Any]]:
        async with self._lock:
            if self._retired:
                raise RuntimeError("launcher retired")
            if not await owns_remote_session(lease):
                raise CredentialRouteChanged("stale physical dispatch owner")
            previous = self.routes.get(route_id)
            if previous is not None:
                if previous.execution_id != execution_id:
                    raise ValueError("route identity conflict")
                return previous.task
            if len(self.routes) >= self.capacity:
                raise RuntimeError("journal full; canonical retirement required")
            task = asyncio.create_task(launch())
            self.routes[route_id] = Route(execution_id, task)
            return task

    def inventory(self) -> list[dict[str, Any]]:
        return [{"route_id": key, "execution_id": route.execution_id,
                 "terminal": route.task.done(), "acknowledged": route.acknowledged}
                for key, route in self.routes.items()]

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
