"""Credential-free remote execution of one agent-run summary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from shared.agent_summary_compute import (
    SummaryCompletion,
    SummaryCompletionRequest,
    SummaryComputeInput,
    compute_summary,
)
from shared.agent_summary_compute_wire import (
    SummaryComputeWireError,
    summary_completion_from_json,
    summary_completion_request_to_json,
    summary_compute_result_to_json,
    summary_input_from_json,
)


class SummaryComputeChannel(Protocol):
    """The callback surface supplied by ``AgentComputeChannel`` at runtime."""

    async def request(self, op: str, payload: dict[str, Any], *, stream: bool = False) -> Any: ...


def _summary_input(plan: Mapping[str, Any]) -> SummaryComputeInput:
    if (
        set(plan) != {"surface", "input", "claim_id"}
        or plan.get("surface") != "summary"
    ):
        raise SummaryComputeWireError("Summary compute plan does not match the wire contract")
    claim_id = plan["claim_id"]
    if not isinstance(claim_id, str):
        raise SummaryComputeWireError("Summary compute claim_id must be a canonical UUID")
    try:
        if str(UUID(claim_id)) != claim_id:
            raise ValueError
    except ValueError as exc:
        raise SummaryComputeWireError(
            "Summary compute claim_id must be a canonical UUID"
        ) from exc
    return summary_input_from_json(plan["input"])


async def run_summary_compute(
    plan: Mapping[str, Any], channel: SummaryComputeChannel
) -> dict[str, Any]:
    """Run one canonical summary through the API-owned model callback."""
    summary_input = _summary_input(plan)

    async def complete(request: SummaryCompletionRequest) -> SummaryCompletion:
        reply = await channel.request(
            "summary_model_request",
            {"request": summary_completion_request_to_json(request)},
        )
        return summary_completion_from_json(reply)

    return summary_compute_result_to_json(
        await compute_summary(summary_input, complete)
    )
