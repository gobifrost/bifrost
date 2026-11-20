"""Canonical summary dispatch with API authority and admitted physical compute."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from shared.agent_summary_compute import SummaryCompletionCallback, SummaryComputeInput, SummaryComputeResult
from src.core.database import get_session_factory
from src.jobs.summarize_worker import handle_summarize_message
from src.models.orm.agent_runs import AgentRun
from src.services.agent_runtime.summary_compute_execution import summary_compute_execution, summary_compute_receipt
from src.services.execution.summary_dispatch import SummaryOwnershipLost, fail_remote_summary, summary_owner_session
from src.worker.remote.compute_stage_host import AgentComputeBinding, RemoteAgentRuntimeError
from src.worker.remote.fencing import RemoteOwnerLost, remote_owner_context
from src.worker.remote.summary_compute_host import RemoteSummaryRuntime
from src.worker.remote.agent_route import DEADLINE_GRACE_SECONDS, run_timeout_seconds


@asynccontextmanager
async def _summary_authorization(
    bridge: Any, run_id: UUID, claim_id: UUID,
) -> AsyncIterator[None]:
    from src.worker.remote.agent_orchestration import _lease_ref, _remaining_owner_seconds

    ref = _lease_ref(bridge)
    with remote_owner_context(ref):
        async with summary_owner_session(get_session_factory(), run_id, ref.lease.worker_id, claim_id) as db:
            run = await db.get(AgentRun, run_id)
            if run is None or run.status != "completed" or run.summary_status != "generating":
                raise SummaryOwnershipLost("Summary attempt is no longer generating")
            async with asyncio.timeout(await _remaining_owner_seconds(db, ref)):
                yield


@dataclass
class _ActiveSummary:
    run_id: UUID
    claim_id: UUID
    task: asyncio.Task[None] | None = None
    runtime: RemoteSummaryRuntime | None = None


class ApiSummaryComputeConsumer:
    """Retain canonical summary slots while only pure compute runs physically."""

    def __init__(self, bridge: Any, *, worker_id: UUID) -> None:
        self.bridge, self.worker_id = bridge, worker_id
        self.active: dict[UUID, _ActiveSummary] = {}

    async def process_message(self, body: dict[str, Any]) -> None:
        from src.worker.remote.agent_orchestration import _lease_ref, _owner_send_authorization

        run_id, dispatch_id, claim_id = (UUID(str(body[key])) for key in ("run_id", "dispatch_id", "claim_id"))
        memory = self.bridge.memory_request_bytes(str(dispatch_id))
        if memory is None:
            raise RemoteAgentRuntimeError("Summary holds no physical memory reservation")
        if claim_id in self.active:
            raise RemoteAgentRuntimeError("Summary attempt is already orchestrating")
        active = _ActiveSummary(run_id, claim_id)
        ref = _lease_ref(self.bridge)

        async def build(summary_input: SummaryComputeInput, complete: SummaryCompletionCallback) -> SummaryComputeResult:
            if active.runtime is not None:
                raise RemoteAgentRuntimeError("Summary attempt may create only one compute stage")
            binding = AgentComputeBinding(str(uuid4()), str(uuid4()), str(run_id), str(dispatch_id))

            async def authorize_rpc() -> None:
                async with _summary_authorization(self.bridge, run_id, claim_id):
                    pass

            @asynccontextmanager
            async def authorize_send() -> AsyncIterator[None]:
                if active.runtime is not None and active.runtime.release_requested:
                    async with _owner_send_authorization(self.bridge):
                        yield
                else:
                    async with _summary_authorization(self.bridge, run_id, claim_id):
                        yield

            with summary_compute_receipt(UUID(binding.stage_id)):
                active.runtime = RemoteSummaryRuntime(
                    summary_input, complete, self.bridge, binding, claim_id=claim_id,
                    authorize_rpc=authorize_rpc, authorize_send=authorize_send,
                    memory_request_bytes=memory,
                )
                return await active.runtime.run()

        async def orchestrate() -> None:
            timed_out = False
            try:
                with remote_owner_context(ref), summary_compute_execution(build):
                    timeout = await run_timeout_seconds(run_id) + DEADLINE_GRACE_SECONDS
                    async with asyncio.timeout(timeout):
                        await handle_summarize_message(body, worker_id=self.worker_id)
            except TimeoutError:
                timed_out = True
                if active.runtime is not None:
                    active.runtime.failure_reason = "Summary compute exceeded its execution deadline."
            finally:
                if active.runtime is not None:
                    try:
                        await active.runtime.release()
                    except RemoteOwnerLost:
                        await active.runtime.abandon()
                    except (ConnectionError, TimeoutError):
                        # Same-owner reconnect finishes retained physical cleanup.
                        pass
            if timed_out and (active.runtime is None or active.runtime._released):
                with remote_owner_context(ref):
                    await fail_remote_summary(
                        worker_id=self.worker_id, run_id=run_id, claim_id=claim_id,
                        reason="Summary compute exceeded its execution deadline.",
                    )

        task = asyncio.create_task(orchestrate(), name=f"api-summary-compute:{claim_id}")
        active.task = task
        self.active[claim_id] = active

        def untrack(done: asyncio.Task[None]) -> None:
            if self.active.get(claim_id) is active and active.task is done:
                self.active.pop(claim_id, None)

        task.add_done_callback(untrack)
        await asyncio.shield(task)

    async def abandon(self) -> None:
        active = tuple(self.active.values())
        tasks = tuple(item.task for item in active if item.task is not None)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for item in active:
            if item.runtime is not None:
                await item.runtime.abandon()
            self.active.pop(item.claim_id, None)

    async def terminate(self, reason: str) -> None:
        from src.worker.remote.agent_orchestration import _lease_ref

        active = tuple(self.active.values())
        for item in active:
            if item.runtime is not None:
                item.runtime.failure_reason = reason
        tasks = tuple(item.task for item in active if item.task is not None)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with remote_owner_context(_lease_ref(self.bridge)):
            for item in active:
                if item.runtime is None or item.runtime._released:
                    await fail_remote_summary(worker_id=self.worker_id, run_id=item.run_id,
                                              claim_id=item.claim_id, reason=reason)


async def reconcile_summary_computes(bridge: Any) -> None:
    """Stop an untracked summary before settling only its exact canonical claim."""
    from src.worker.remote.agent_orchestration import _inventory_binding, _lease_ref, _release_orphan_stage

    inventory = getattr(bridge, "inventory", None) or {}
    for stage in inventory.get("agent_compute_stages", []):
        if stage.get("compute_kind") != "summary":
            continue
        stage_binding, binding = _inventory_binding(stage)
        try:
            claim_id = UUID(stage["summary_claim_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteAgentRuntimeError("Summary compute inventory claim is invalid") from exc
        if str(claim_id) != stage["summary_claim_id"]:
            raise RemoteAgentRuntimeError("Summary compute inventory claim is not canonical")
        local = bridge.agent_compute_runtimes.get(stage_binding.stage_id)
        if local is not None:
            if (not isinstance(local, RemoteSummaryRuntime) or local.binding != stage_binding
                    or local.claim_id != claim_id or local._input_sha256 != binding["input_sha256"]):
                raise RemoteAgentRuntimeError("Summary compute inventory changed a tracked attempt")
            if local.release_requested:
                await local.release()
                if local.failure_reason is not None:
                    ref = _lease_ref(bridge)
                    with remote_owner_context(ref):
                        await fail_remote_summary(
                            worker_id=ref.lease.worker_id, run_id=UUID(stage_binding.run_id),
                            claim_id=claim_id, reason=local.failure_reason,
                        )
            continue
        await _release_orphan_stage(bridge, binding)
        ref = _lease_ref(bridge)
        with remote_owner_context(ref):
            await fail_remote_summary(
                worker_id=ref.lease.worker_id, run_id=UUID(stage_binding.run_id), claim_id=claim_id,
                reason="The API summary orchestrator stopped before the compute stage completed.",
            )
