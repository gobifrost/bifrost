"""API orchestration for credential-free physical agent-compute stages.

The physical child owns only the PydanticAI loop.  This module keeps the
canonical :class:`AgentRunConsumer` and every durable authorization decision
in the API process, while supplying that loop with a remote runtime proxy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select

from src.core.database import get_db_context
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.models.orm.agent_runs import AgentRun
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.services.agent_runtime.compute import AgentRuntimePlan, BuiltAgentRuntime
from src.services.agent_runtime.empty_output import EmptyOutputCircuitBreaker
from src.services.authorization.agent_runtime import (
    authorize_agent_execution,
    authorize_agentless_ai,
    authorize_chat_execution,
)
from src.services.authorization.enforce import load_caller
from src.services.authorization.runtime_auth import load_runtime_principal
from src.services.execution.agent_run_dispatch import FAMILY as AGENT_RUN_FAMILY
from src.services.execution.agent_run_service import announce_ended_agent_run
from src.services.execution.autonomous_agent_executor import MAX_DELEGATION_DEPTH
from src.worker.remote.agent_compute_host import (
    AgentComputeBinding,
    RemoteAgentRuntime,
    RemoteAgentRuntimeError,
)
from src.worker.remote.agent_route import RemoteAgentConsumer
from src.worker.remote.fencing import (
    LeaseRef,
    RemoteOwnerLost,
    remote_owner_context,
    require_remote_owner,
)

logger = logging.getLogger(__name__)

_MAX_AGENT_COMPUTE_STAGES = 64
_BINDING_FIELDS = ("stage_id", "route_id", "run_id", "dispatch_id", "input_sha256")


def _canonical_uuid(value: Any, field: str) -> UUID:
    if not isinstance(value, str):
        raise RemoteAgentRuntimeError(f"Agent compute {field} must be a UUID")
    try:
        result = UUID(value)
    except ValueError as exc:
        raise RemoteAgentRuntimeError(f"Agent compute {field} must be a UUID") from exc
    if str(result) != value:
        raise RemoteAgentRuntimeError(f"Agent compute {field} must be canonical")
    return result


def _lease_ref(bridge: Any) -> LeaseRef:
    ref = getattr(bridge, "lease_ref", None)
    if not isinstance(ref, LeaseRef):
        raise RemoteOwnerLost("Agent compute has no current remote worker owner")
    if getattr(bridge, "lease", None) is not ref.lease:
        raise RemoteOwnerLost("Agent compute remote worker owner changed")
    return ref


async def _remaining_owner_seconds(db: Any, ref: LeaseRef) -> float:
    reading_at = time.monotonic()
    seconds = await db.scalar(
        select(
            func.extract(
                "epoch",
                ExecutionWorker.remote_owner_lease_expires_at - func.clock_timestamp(),
            )
        ).where(ExecutionWorker.id == ref.lease.worker_id)
    )
    remaining = float(seconds or 0) - (time.monotonic() - reading_at)
    if remaining <= 0:
        raise RemoteOwnerLost("Agent compute remote worker owner lease expired")
    return remaining


async def _authorized_lineage(
    db: Any,
    *,
    ref: LeaseRef,
    root_run_id: UUID,
    active_run_id: UUID,
    authorize_caller: bool,
) -> tuple[UUID, ...]:
    """Validate the current runtime and return child-to-root run identities."""

    await require_remote_owner(db)
    lineage: list[UUID] = []
    current_id = active_run_id
    active: AgentRun | None = None
    while True:
        run = await db.get(AgentRun, current_id)
        if run is None or run.status != "running":
            raise RemoteOwnerLost("Agent compute run is no longer running")
        active = active or run
        lineage.append(run.id)
        if run.parent_run_id is None:
            break
        if len(lineage) > MAX_DELEGATION_DEPTH:
            raise RemoteOwnerLost("Agent compute delegation depth is invalid")
        current_id = run.parent_run_id

    if lineage[-1] != root_run_id:
        raise RemoteOwnerLost("Agent compute run does not belong to its claimed root")
    claim = await db.scalar(
        select(ExecutionWorkerClaim.id).where(
            ExecutionWorkerClaim.worker_id == ref.lease.worker_id,
            ExecutionWorkerClaim.family == AGENT_RUN_FAMILY,
            ExecutionWorkerClaim.subject_id == root_run_id,
        )
    )
    if claim is None:
        raise RemoteOwnerLost("Agent compute root run is no longer claimed by this worker")

    if authorize_caller:
        assert active is not None
        principal = await load_runtime_principal(
            db, {"credential_kind": "agent_run", "sub": str(active_run_id)}
        )
        if principal is None:
            raise RemoteOwnerLost("Agent compute runtime authority is unavailable")
        caller = await load_caller(db, principal)
        if caller.ctx is None:
            raise RemoteOwnerLost("Agent compute caller is unavailable")
        if active.trigger_type == "chat":
            caller = await authorize_chat_execution(db, caller)
        if active.agent_id is None:
            await authorize_agentless_ai(db, caller)
        else:
            await authorize_agent_execution(db, caller, active.agent_id)
    return tuple(lineage)


@asynccontextmanager
async def _owner_send_authorization(bridge: Any) -> AsyncIterator[None]:
    """Fence a cleanup command through its socket send, never its reply."""

    ref = _lease_ref(bridge)
    with remote_owner_context(ref):
        async with get_db_context() as db:
            await require_remote_owner(db)
            async with asyncio.timeout(await _remaining_owner_seconds(db, ref)):
                yield


@asynccontextmanager
async def _run_send_authorization(
    bridge: Any, *, root_run_id: UUID, active_run_id: UUID, cleanup: bool
) -> AsyncIterator[None]:
    """Fence a stage command and revalidate the active run before its send."""

    if cleanup:
        async with _owner_send_authorization(bridge):
            yield
        return
    ref = _lease_ref(bridge)
    with remote_owner_context(ref):
        async with get_db_context() as db:
            await _authorized_lineage(
                db,
                ref=ref,
                root_run_id=root_run_id,
                active_run_id=active_run_id,
                authorize_caller=True,
            )
            async with asyncio.timeout(await _remaining_owner_seconds(db, ref)):
                yield


async def _authorize_rpc(
    bridge: Any, *, root_run_id: UUID, active_run_id: UUID
) -> None:
    """Reauthorize before a model/provider/effect operation, then close DB."""

    ref = _lease_ref(bridge)
    with remote_owner_context(ref):
        async with get_db_context() as db:
            await _authorized_lineage(
                db,
                ref=ref,
                root_run_id=root_run_id,
                active_run_id=active_run_id,
                authorize_caller=True,
            )


async def _unsupported_capability_load(capability_id: str) -> None:
    raise RemoteAgentRuntimeError(
        f"Agent compute stage cannot load capability {capability_id!r}"
    )


@dataclass
class ApiAgentRuntimeBuilder:
    """Build remote stage proxies for one canonical API ``AgentRun`` call."""

    bridge: Any
    root_run_id: UUID
    dispatch_id: UUID
    memory_request_bytes: int
    runtimes: set[RemoteAgentRuntime]
    active_run_ids: set[UUID]

    def __init__(
        self,
        bridge: Any,
        *,
        root_run_id: UUID | str,
        dispatch_id: UUID | str,
        memory_request_bytes: int,
    ) -> None:
        self.bridge = bridge
        self.root_run_id = UUID(str(root_run_id))
        self.dispatch_id = UUID(str(dispatch_id))
        if type(memory_request_bytes) is not int or memory_request_bytes < 0:
            raise ValueError("Agent compute memory request is invalid")
        self.memory_request_bytes = memory_request_bytes
        self.runtimes = set()
        self.active_run_ids = set()

    def __call__(self, plan: AgentRuntimePlan, **callbacks: Any) -> BuiltAgentRuntime:
        """Create exactly one immutable physical stage for this runtime plan."""

        # The stage receives a canonical value copy, never an executor-owned
        # mutable object or provider configuration.
        plan = AgentRuntimePlan.from_json(plan.to_json())
        active_run_id = self._active_run_id(plan)
        required = {"model", "executor", "event_handler", "model_settings"}
        missing = required - set(callbacks)
        unexpected = set(callbacks) - {
            *required,
            "usage",
            "capability_load_authorizer",
            "composition_error_type",
            "empty_output_guard",
        }
        if missing or unexpected:
            raise ValueError(
                "Agent compute runtime callbacks do not match the canonical factory"
            )
        usage = callbacks.get("usage") or RunUsage()
        if not isinstance(usage, RunUsage):
            raise ValueError("Agent compute runtime usage must be RunUsage")
        guard = callbacks.get("empty_output_guard")
        if guard is not None and not isinstance(guard, EmptyOutputCircuitBreaker):
            raise ValueError("Agent compute empty-output guard is invalid")
        capability_load = callbacks.get("capability_load_authorizer")
        if capability_load is None:
            capability_load = _unsupported_capability_load
        _lease_ref(self.bridge)
        binding = AgentComputeBinding(
            stage_id=str(uuid4()),
            route_id=str(uuid4()),
            run_id=str(active_run_id),
            dispatch_id=str(self.dispatch_id),
        )

        @asynccontextmanager
        async def authorize_send() -> AsyncIterator[None]:
            runtime = runtime_ref[0]
            async with _run_send_authorization(
                self.bridge,
                root_run_id=self.root_run_id,
                active_run_id=active_run_id,
                cleanup=runtime.release_requested,
            ):
                yield

        runtime_ref: list[RemoteAgentRuntime] = []
        runtime = RemoteAgentRuntime(
            plan,
            self.bridge,
            binding,
            trusted_model=callbacks["model"],
            trusted_model_settings=callbacks["model_settings"],
            execute_tool=callbacks["executor"],
            tool_event=callbacks["event_handler"],
            capability_load=capability_load,
            usage=usage,
            empty_output_guard=guard,
            authorize_rpc=lambda: _authorize_rpc(
                self.bridge,
                root_run_id=self.root_run_id,
                active_run_id=active_run_id,
            ),
            authorize_send=authorize_send,
            memory_request_bytes=self.memory_request_bytes,
        )
        runtime_ref.append(runtime)
        self.runtimes.add(runtime)
        self.active_run_ids.add(active_run_id)
        return BuiltAgentRuntime(runtime, usage, guard, None, None)

    def _active_run_id(self, plan: AgentRuntimePlan) -> UUID:
        if plan.surface == "chat":
            return self.root_run_id
        try:
            return UUID(plan.runtime_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Autonomous agent compute plan has no run UUID") from exc

    async def release_all(self) -> None:
        """Release every local stage; uncertain cleanup stays registered to bridge."""

        for runtime in tuple(self.runtimes):
            try:
                await runtime.release()
            except (ConnectionError, TimeoutError):
                logger.warning(
                    "Agent compute cleanup awaits same-owner reconnect",
                    extra={"stage_id": runtime.binding.stage_id},
                )
                continue
            except RemoteOwnerLost:
                # This API owner cannot reconnect the retained callback
                # journal.  A successor must see an untracked physical stage
                # and fail it closed during inventory reconciliation.
                await runtime.abandon()
                self.runtimes.discard(runtime)
                continue
            self.runtimes.discard(runtime)

    async def abandon_all(self) -> None:
        """Discard this API owner's callback journals without a worker command."""

        for runtime in tuple(self.runtimes):
            await runtime.abandon()
        self.runtimes.clear()


@dataclass
class _ActiveOrchestration:
    root_run_id: UUID
    builder: ApiAgentRuntimeBuilder
    task: asyncio.Task[None]


class ApiComputeAgentConsumer(RemoteAgentConsumer):
    """Run the canonical consumer in API memory for ``agent.compute`` workers."""

    def __init__(self, bridge: Any, *, worker_id: UUID) -> None:
        super().__init__(bridge, worker_id=worker_id)
        self.bridge = bridge
        self._canonical = AgentRunConsumer()
        self.active: dict[UUID, _ActiveOrchestration] = {}
        from src.worker.remote.summary_orchestration import ApiSummaryComputeConsumer

        self.summaries = ApiSummaryComputeConsumer(bridge, worker_id=worker_id)

    async def process_message(self, body: dict[str, Any], *, handler: str = "agent.run") -> None:
        if handler != "agent.run":
            if (handler in {"agent.summarize", "agent.summarize_backfill"}
                    and ("summary.compute", 1) in self.bridge.compute_capabilities):
                await self.summaries.process_message(body)
                return
            await super().process_message(body, handler=handler)
            return
        run_id = UUID(str(body["run_id"]))
        dispatch_id = UUID(str(body["dispatch_id"]))
        request_bytes = self.bridge.memory_request_bytes(str(dispatch_id))
        if request_bytes is None:
            raise RuntimeError(f"Agent run {run_id} holds no memory reservation")
        if run_id in self.active:
            raise RuntimeError(f"Agent run {run_id} is already orchestrating")
        builder = ApiAgentRuntimeBuilder(
            self.bridge,
            root_run_id=run_id,
            dispatch_id=dispatch_id,
            memory_request_bytes=request_bytes,
        )

        async def orchestrate() -> None:
            from src.services.agent_runtime.compute_execution import agent_compute_execution

            try:
                with agent_compute_execution(builder):
                    await self._canonical.process_message(body)
            finally:
                await builder.release_all()

        task = asyncio.create_task(orchestrate(), name=f"api-agent-compute:{run_id}")
        self.active[run_id] = _ActiveOrchestration(run_id, builder, task)

        def release_tracking(done: asyncio.Task[None]) -> None:
            item = self.active.get(run_id)
            if item is not None and item.task is done:
                self.active.pop(run_id, None)

        task.add_done_callback(release_tracking)
        await asyncio.shield(task)

    async def abandon_orchestrations(self) -> None:
        """Wait for cancelled API callbacks to unwind after ownership loss."""

        await self.summaries.abandon()
        active = tuple(self.active.values())
        tasks = tuple(item.task for item in active)
        for item in active:
            item.task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for item in active:
            await item.builder.abandon_all()
            if self.active.get(item.root_run_id) is item:
                self.active.pop(item.root_run_id, None)
        # A completed orchestration may still retain uncertain physical cleanup.
        # Its callbacks also belong to the old API owner and must be drained.
        for runtime in tuple(self.bridge.agent_compute_runtimes.values()):
            await runtime.abandon()

    async def terminate_active_runs(self, *, error_type: str, error: str) -> None:
        """Stop API orchestration, then end only its canonically claimed runs."""

        await self.summaries.terminate(error)
        active = tuple(self.active.values())
        ref = _lease_ref(self.bridge)
        with remote_owner_context(ref):
            for item in active:
                for run_id in sorted(item.builder.active_run_ids | {item.root_run_id}, key=str):
                    await announce_ended_agent_run(run_id, error)
        for item in active:
            item.task.cancel()
        await asyncio.gather(*(item.task for item in active), return_exceptions=True)
        for item in active:
            await item.builder.release_all()
        await super().terminate_active_runs(error_type=error_type, error=error)


def _inventory_binding(stage: Any) -> tuple[AgentComputeBinding, dict[str, str]]:
    if not isinstance(stage, Mapping):
        raise RemoteAgentRuntimeError("Agent compute inventory stage is invalid")
    if any(field not in stage for field in _BINDING_FIELDS):
        raise RemoteAgentRuntimeError("Agent compute inventory binding is incomplete")
    binding = AgentComputeBinding.from_value(
        {field: stage[field] for field in ("stage_id", "route_id", "run_id", "dispatch_id")}
    )
    if str(_canonical_uuid(binding.route_id, "route_id")) != binding.route_id:
        raise RemoteAgentRuntimeError("Agent compute inventory route is invalid")
    digest = stage["input_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise RemoteAgentRuntimeError("Agent compute inventory digest is invalid")
    return binding, {**binding.to_dict(), "input_sha256": digest}


def _bound_reply(reply: Any, binding: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(reply, dict) or any(reply.get(key) != value for key, value in binding.items()):
        raise RemoteAgentRuntimeError("Agent compute reply binding changed")
    return reply


async def _release_orphan_stage(bridge: Any, binding: dict[str, str]) -> None:
    """Stop an inventoried stage using only current worker ownership."""

    status = _bound_reply(
        await bridge.request(
            "agent_compute_status", binding, authorization=_owner_send_authorization(bridge)
        ),
        binding,
    )
    if status.get("state") in {"unknown", "released"}:
        return
    _bound_reply(
        await bridge.request(
            "agent_compute_release", binding, authorization=_owner_send_authorization(bridge)
        ),
        binding,
    )


async def _claimed_orphan_lineage(
    bridge: Any, *, root_candidate: UUID
) -> tuple[UUID, ...] | None:
    """Return a bounded lineage only when this worker owns its root claim."""

    ref = _lease_ref(bridge)
    with remote_owner_context(ref):
        async with get_db_context() as db:
            try:
                await require_remote_owner(db)
            except RemoteOwnerLost:
                return None
            # Recompute the bounded parent walk for an untracked physical
            # child, then verify the canonical worker claim.
            lineage_ids: list[UUID] = []
            current = root_candidate
            while True:
                run = await db.get(AgentRun, current)
                if run is None or run.status != "running":
                    return None
                lineage_ids.append(run.id)
                if run.parent_run_id is None:
                    break
                if len(lineage_ids) > MAX_DELEGATION_DEPTH:
                    return None
                current = run.parent_run_id
            root = lineage_ids[-1]
            claim = await db.scalar(
                select(ExecutionWorkerClaim.id).where(
                    ExecutionWorkerClaim.worker_id == ref.lease.worker_id,
                    ExecutionWorkerClaim.family == AGENT_RUN_FAMILY,
                    ExecutionWorkerClaim.subject_id == root,
                )
            )
            return tuple(lineage_ids) if claim is not None else None


async def reconcile_agent_computes(bridge: Any) -> None:
    """Fail closed after API loss before the worker admits another run."""

    inventory = getattr(bridge, "inventory", None) or {}
    stages = inventory.get("agent_compute_stages", [])
    if not isinstance(stages, list) or len(stages) > _MAX_AGENT_COMPUTE_STAGES:
        raise RemoteAgentRuntimeError("Agent compute inventory is invalid")
    runtimes = getattr(bridge, "agent_compute_runtimes", None)
    if not isinstance(runtimes, dict):
        raise RemoteAgentRuntimeError("Agent compute runtime registry is unavailable")

    for stage in stages:
        if isinstance(stage, Mapping) and stage.get("compute_kind") == "summary":
            continue
        stage_binding, binding = _inventory_binding(stage)
        local = runtimes.get(stage_binding.stage_id)
        if local is not None:
            if local.binding != stage_binding or local._input_sha256 != binding["input_sha256"]:
                raise RemoteAgentRuntimeError("Agent compute inventory changed a tracked binding")
            if not local.release_requested:
                continue
            # A same-owner reconnect retains both the stage and its receipt
            # journal.  Cleanup was requested, so complete it before claims.
            await local.release()
            continue

        # This stage belongs to an API process that no longer has its callback
        # journal.  Stop it before changing any durable run, then only end a
        # run if the stage can be tied to this worker's canonical root claim.
        lineage = await _claimed_orphan_lineage(
            bridge, root_candidate=UUID(stage_binding.run_id)
        )
        await _release_orphan_stage(bridge, binding)
        if lineage is None:
            continue
        ref = _lease_ref(bridge)
        with remote_owner_context(ref):
            for run_id in reversed(lineage):
                await announce_ended_agent_run(
                    run_id,
                    "The API agent orchestrator stopped before the compute stage completed.",
                )


__all__ = [
    "ApiAgentRuntimeBuilder",
    "ApiComputeAgentConsumer",
    "reconcile_agent_computes",
]
