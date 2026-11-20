"""API-owned model callback for a credential-free summary compute stage."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from shared.agent_summary_compute import (
    SummaryCompletion, SummaryCompletionCallback, SummaryComputeInput,
    SummaryComputeResult, build_summary_completion_request,
)
from shared.agent_summary_compute_wire import (
    summary_completion_request_to_json, summary_completion_to_json,
    summary_compute_result_from_json, summary_input_to_json,
)
from src.worker.remote.compute_stage_host import (
    AgentComputeBinding, ComputeStageHost, RemoteAgentRuntimeError,
)


class RemoteSummaryRuntime(ComputeStageHost):
    """One immutable summary input and one API-authorized provider completion."""

    rpc_operations = frozenset({"summary_model_request"})

    def __init__(
        self, summary_input: SummaryComputeInput, complete: SummaryCompletionCallback,
        bridge: Any, binding: AgentComputeBinding, *, claim_id: UUID,
        authorize_rpc: Callable[[], Awaitable[None]],
        authorize_send: Callable[[], AbstractAsyncContextManager[None]],
        memory_request_bytes: int,
    ) -> None:
        super().__init__(bridge, binding, authorize_rpc=authorize_rpc,
                         authorize_send=authorize_send, memory_request_bytes=memory_request_bytes)
        self.summary_input = summary_input
        self.claim_id = claim_id
        self._complete = complete
        self._request = build_summary_completion_request(summary_input)
        self._request_json = summary_completion_request_to_json(self._request)
        self._completion: SummaryCompletion | None = None
        self._completion_lock = asyncio.Lock()
        self.failure_reason: str | None = None

    async def run(self) -> SummaryComputeResult:
        self._claim_invocation()
        async with self._run_lock:
            await self._start_stage({
                "plan": {"surface": "summary", "input": summary_input_to_json(self.summary_input),
                         "claim_id": str(self.claim_id)},
                "model_name": self.summary_input.model,
                "model_profile_payload_b64": "",
                "usage_initial": {}, "user_prompt": "", "history": [],
            })
            terminal = await self._wait_terminal()
            if terminal.get("ok") is not True:
                raise RemoteAgentRuntimeError(str(terminal.get("error") or "Summary compute stage failed"))
            result = summary_compute_result_from_json(terminal.get("result"))
            if result.completion != self._completion:
                raise RemoteAgentRuntimeError("Summary compute changed the API-owned completion receipt")
            if self._completion is None and (result.failure is None or result.failure.kind != "provider"):
                raise RemoteAgentRuntimeError("Summary compute returned an outcome without a provider completion")
            return result

    async def _reply_frames(
        self, request_id: str, op: str, payload: dict[str, Any], stream: bool,
    ) -> AsyncIterator[dict[str, Any]]:
        if stream or op != "summary_model_request" or payload != {"request": self._request_json}:
            raise RemoteAgentRuntimeError("Summary compute changed its authorized model request")
        async with self._completion_lock:
            await self.authorize_rpc()
            if self._completion is None:
                # The callback records provider usage before returning. Keep its
                # result in this owner even when the physical reply is lost.
                self._completion = await self._complete(self._request)
            completion_json = summary_completion_to_json(self._completion)
        yield {"result": completion_json}
