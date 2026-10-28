"""API side of agent runs executed by a remote launcher.

An ``ApiExecutor`` claims ``agent.run`` and hands the claimed message to
:class:`RemoteAgentConsumer`. The launcher forks the run into a child of its
agent template and reports ``agent_exit`` when that child ends. The child
records the run's outcome itself (the ``AgentRun`` row, the sync result, chat
events). This side:

- keeps a route for every child it launched until the launcher reports the
  child's exit or proves it absent, so a lost reply never launches a run twice;
- ends the run and wakes its callers when the child exits without having
  recorded an outcome (a crash, an OOM kill, a terminate);
- enforces the run's timeout and its cancellation deadline by terminating the
  child, because the launcher sets no deadline of its own;
- re-attaches children an earlier API owner launched (inventory adoption).

Cancelling a wait (detach or lost ownership) never kills the child; the next
owner adopts it. A drain whose grace runs out is different: the executor then
retires its launcher, which kills every child, so it first terminates the
remaining children and ends their runs (:meth:`terminate_active_runs`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select

from src.core.database import get_db_context
from src.core.redis_client import get_redis_client
from src.jobs.schedulers.execution_cleanup import DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent, Conversation
from src.services.execution.agent_run_dispatch import release_agent_run
from src.services.execution.agent_run_service import announce_ended_agent_run

logger = logging.getLogger(__name__)

# How often a waiting run checks its deadlines.
SUPERVISE_SECONDS = 5.0
# Beyond the run's own timeout, which its child enforces: room to record it.
DEADLINE_GRACE_SECONDS = 120.0
# How long a cancelled run's child has to stop by itself.
CANCEL_GRACE_SECONDS = 30.0
# SIGTERM to SIGKILL when the API terminates a child.
TERMINATE_GRACE_SECONDS = 5.0
# After a terminate was acknowledged, how long the exit report may take
# before the launcher is taken not to have the child at all.
EXIT_REPORT_SECONDS = 30.0

EXIT_ERROR = "The process running this agent stopped before it finished"
LAUNCH_ERROR = "The worker could not start this agent run"
MISSING_ERROR = "The worker no longer has the process running this agent"


class AgentTransport(Protocol):
    """The launcher control channel (``host.Bridge``)."""

    async def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a launcher command and return its reply."""
        raise NotImplementedError

    def memory_request_bytes(self, key: str) -> int | None:
        """The memory request held for ``key``, if it is active."""
        raise NotImplementedError


@dataclass
class AgentRoute:
    route_id: str
    run_id: UUID
    exited: asyncio.Future[None] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class RemoteAgentConsumer:
    """Runs claimed ``agent.run`` dispatches in launcher children."""

    def __init__(self, transport: AgentTransport, *, worker_id: UUID) -> None:
        self.transport = transport
        self.worker_id = worker_id
        self.routes: dict[str, AgentRoute] = {}

    def tracks(self, route_id: Any) -> bool:
        return isinstance(route_id, str) and route_id in self.routes

    async def process_message(self, body: dict[str, Any]) -> None:
        """Launch one claimed run and wait until its child has ended."""
        run_id = UUID(str(body["run_id"]))
        dispatch_id = str(body["dispatch_id"])
        request_bytes = self.transport.memory_request_bytes(dispatch_id)
        if request_bytes is None:
            raise RuntimeError(f"Agent run {run_id} holds no memory reservation")
        route = AgentRoute(route_id=f"agent:{run_id}:{uuid4()}", run_id=run_id)
        self.routes[route.route_id] = route
        try:
            await self.transport.request("agent_run", {
                "route_id": route.route_id,
                "run_id": str(run_id),
                "message": body,
                "memory_request_bytes": request_bytes,
            })
        except (ConnectionError, TimeoutError):
            # The launcher may have forked before its reply was lost. Keep
            # the route: its exit, or inventory proving it absent, settles it.
            logger.warning("Agent run launch reply uncertain", extra={"run_id": str(run_id)})
        except MemoryError:
            # Refused before forking: the run waits for a worker with room.
            self.routes.pop(route.route_id, None)
            await release_agent_run(
                worker_id=self.worker_id, run_id=run_id, dispatch_id=UUID(dispatch_id)
            )
            return
        except Exception as exc:
            self.routes.pop(route.route_id, None)
            logger.error("Launcher refused an agent run", extra={"run_id": str(run_id)}, exc_info=True)
            await announce_ended_agent_run(run_id, f"{LAUNCH_ERROR}: {exc}")
            return
        await self.supervise(route)

    def adopt(self, route_id: str, run_id: UUID) -> AgentRoute:
        """Attach to a child an earlier owner launched."""
        route = self.routes.get(route_id)
        if route is None:
            route = AgentRoute(route_id=route_id, run_id=run_id)
            self.routes[route_id] = route
        return route

    async def supervise(self, route: AgentRoute) -> None:
        """Wait for the child's exit, terminating it past the run's deadlines."""
        loop = asyncio.get_running_loop()
        timeout = await run_timeout_seconds(route.run_id)
        deadline = loop.time() + timeout + DEADLINE_GRACE_SECONDS
        cancel_deadline: float | None = None
        terminated_at: float | None = None
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(route.exited), SUPERVISE_SECONDS)
                return
            except TimeoutError:
                pass
            now = loop.time()
            if terminated_at is not None:
                if now - terminated_at >= EXIT_REPORT_SECONDS:
                    await self._settle(route, MISSING_ERROR)
                    return
                continue
            if now >= deadline:
                reason = ("TimeoutError", f"Agent run exceeded its {timeout}s timeout")
            else:
                if cancel_deadline is None and await get_redis_client().check_agent_run_cancel_flag(
                    str(route.run_id)
                ):
                    cancel_deadline = now + CANCEL_GRACE_SECONDS
                if cancel_deadline is None or now < cancel_deadline:
                    continue
                reason = ("CancelledError", "Agent run did not stop after it was cancelled")
            try:
                await self.transport.request("terminate", {
                    "route_id": route.route_id,
                    "error_type": reason[0],
                    "error": reason[1],
                    "grace_seconds": TERMINATE_GRACE_SECONDS,
                })
            except (ConnectionError, TimeoutError, RuntimeError):
                logger.warning("Could not terminate an agent child; retrying", exc_info=True)
                continue
            terminated_at = now

    async def handle_exit(self, event: dict[str, Any]) -> None:
        """The launcher reported a child's exit (before its ACK)."""
        route = self.routes.get(str(event.get("route_id")))
        if route is None:
            return
        if event.get("ok") is not True:
            detail = event.get("error") or event.get("error_type") or "exited abnormally"
            await self._settle(route, f"{EXIT_ERROR}: {detail}")
            return
        self._resolve(route)

    async def reject_missing(self, inventory: dict[str, Any]) -> None:
        """Settle routes the launcher's inventory proves absent: never launched."""
        observed = {
            entry.get("route_id")
            for entry in [*inventory.get("routes", []), *inventory.get("terminal_routes", [])]
            if isinstance(entry, dict)
        }
        for route_id, route in tuple(self.routes.items()):
            if route_id not in observed:
                await self._settle(route, MISSING_ERROR)

    async def terminate_active_runs(self, *, error_type: str, error: str) -> None:
        """Terminate every child still running and end its run with ``error``.

        For an executor whose drain grace ran out: retiring its launcher next
        kills whatever is still running without an exit report, so each run is
        ended here, waking its callers, while the launcher still listens.
        """
        routes = list(self.routes.values())
        # Their exit reports, if any arrive, find nothing waiting and are ACKed.
        self.routes.clear()

        async def end(route: AgentRoute) -> None:
            try:
                await self.transport.request("terminate", {
                    "route_id": route.route_id,
                    "error_type": error_type,
                    "error": error,
                    "grace_seconds": TERMINATE_GRACE_SECONDS,
                })
            except (ConnectionError, TimeoutError, RuntimeError):
                # Retiring the launcher stops the child anyway.
                logger.warning("Could not terminate an agent child at shutdown", exc_info=True)
            try:
                await announce_ended_agent_run(route.run_id, error)
            except Exception:
                logger.exception(
                    "Could not end an agent run at shutdown", extra={"run_id": str(route.run_id)}
                )
            if not route.exited.done():
                route.exited.set_result(None)

        await asyncio.gather(*(end(route) for route in routes))

    def detach(self) -> None:
        """Stop waiting without touching the children (ownership moved on)."""
        for route in self.routes.values():
            route.exited.cancel()
        self.routes.clear()

    async def _settle(self, route: AgentRoute, error: str) -> None:
        try:
            await announce_ended_agent_run(route.run_id, error)
        finally:
            self._resolve(route)

    def _resolve(self, route: AgentRoute) -> None:
        self.routes.pop(route.route_id, None)
        if not route.exited.done():
            route.exited.set_result(None)


async def run_timeout_seconds(run_id: UUID) -> int:
    """The longest timeout the run's child may apply: its agent's or its
    conversation's agent's, else the default."""
    async with get_db_context() as db:
        run = await db.get(AgentRun, run_id)
        timeouts = [run.agent.max_run_timeout if run is not None and run.agent is not None else None]
        if run is not None and run.conversation_id is not None:
            timeouts.append(await db.scalar(
                select(Agent.max_run_timeout)
                .join(Conversation, Conversation.agent_id == Agent.id)
                .where(Conversation.id == run.conversation_id)
            ))
    return max((value for value in timeouts if value), default=DEFAULT_AGENT_RUN_TIMEOUT_SECONDS)


__all__ = ["AgentRoute", "RemoteAgentConsumer", "run_timeout_seconds"]
