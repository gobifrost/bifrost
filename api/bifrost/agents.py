"""Bifrost SDK — Agent invocation from workflows."""
from __future__ import annotations

import json
import logging
import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Literal

from ._context import _execution_context
from .client import get_client, raise_for_status_with_detail
from .models import AgentRun, AgentRunHandle, AgentRunPending

logger = logging.getLogger(__name__)


def _poll_interval(elapsed_seconds: float) -> float:
    if elapsed_seconds < 30:
        return 2.0
    if elapsed_seconds < 300:
        return 5.0
    return 10.0


def _workflow_return_margin(timeout_seconds: int) -> float:
    return min(30.0, max(5.0, timeout_seconds * 0.15))


class AgentPausedError(Exception):
    """Raised when an agent run is requested for a paused agent.

    The enqueue endpoint returns HTTP 200 with a structured paused body. The SDK
    surfaces it as a typed exception so workflow code does not silently receive
    ``None`` and continue as if the agent had completed.
    """

    def __init__(self, message: str, *, agent_id: str | None = None):
        super().__init__(message)
        self.agent_id = agent_id


class agents:
    """Agent execution operations."""

    @staticmethod
    async def enqueue(
        agent_name: str,
        input: dict[str, Any] | None = None,
        *,
        output_schema: dict[str, Any] | None = None,
    ) -> AgentRunHandle:
        """Queue an agent and return as soon as the run is accepted."""
        client = get_client()
        response = await client.post(
            "/api/agent-runs/enqueue",
            json={
                "agent_name": agent_name,
                "input": input or {},
                "output_schema": output_schema,
            },
        )
        raise_for_status_with_detail(response)
        data = response.json()

        if isinstance(data, dict) and data.get("status") == "paused":
            raise AgentPausedError(
                data.get("message") or f"Agent '{agent_name}' is paused.",
                agent_id=data.get("agent_id"),
            )

        return AgentRunHandle.model_validate(data)

    @staticmethod
    async def get_run(run_id: str) -> AgentRun:
        """Get the current status and result for an agent run."""
        client = get_client()
        response = await client.get(f"/api/agent-runs/{run_id}")
        if response.status_code == 404:
            raise ValueError(f"Agent run not found: {run_id}")
        if response.status_code == 403:
            raise PermissionError(f"Access denied to agent run: {run_id}")
        raise_for_status_with_detail(response)
        return AgentRun.model_validate(response.json())

    @staticmethod
    async def run(
        agent_name: str,
        input: dict[str, Any] | None = None,
        *,
        output_schema: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | str | AgentRunPending:
        """Run an agent and wait for the result.

        Args:
            agent_name: Name of the agent to run.
            input: Structured input data for the agent.
            output_schema: JSON Schema for the expected output.
            timeout: Optional maximum seconds to wait. The agent keeps running
                if this wait expires. Inside a workflow, the wait also ends
                shortly before the workflow's execution deadline.

        Returns:
            Agent output, or AgentRunPending with the run ID if the wait ends.

        Raises:
            RuntimeError: If the agent run fails.
            ValueError: If the agent is not found.
            AgentPausedError: If the target agent is paused (is_active=False).
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        handle = await agents.enqueue(
            agent_name, input, output_schema=output_schema,
        )
        return await agents.wait(handle.run_id, output_schema=output_schema, timeout=timeout)

    @staticmethod
    async def wait(
        run_id: str,
        *,
        output_schema: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | str | AgentRunPending:
        """Wait for a previously enqueued run using short status requests."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")

        started_waiting = time.monotonic()
        wait_deadline = started_waiting + timeout if timeout is not None else None
        context = _execution_context.get()
        workflow_deadline = context.workflow_deadline if context else None
        workflow_return_margin = 0.0
        if workflow_deadline is not None:
            workflow_timeout_seconds = context.workflow_timeout_seconds if context else None
            if workflow_timeout_seconds is None:
                raise ValueError("workflow deadline requires workflow_timeout_seconds")
            workflow_return_margin = _workflow_return_margin(workflow_timeout_seconds)
        if workflow_deadline is not None and workflow_deadline.tzinfo is None:
            raise ValueError("workflow_deadline must include a timezone")

        last_status: Literal["queued", "running", "cancelling"] | None = None
        while True:
            remaining: float | None = None
            reason: Literal["wait_timeout", "workflow_deadline"] = "wait_timeout"
            if wait_deadline is not None:
                remaining = wait_deadline - time.monotonic()
            if workflow_deadline is not None:
                workflow_remaining = (
                    workflow_deadline - datetime.now(timezone.utc)
                ).total_seconds() - workflow_return_margin
                if remaining is None or workflow_remaining < remaining:
                    remaining = workflow_remaining
                    reason = "workflow_deadline"

            if remaining is not None and remaining <= 0:
                return AgentRunPending(
                    run_id=run_id, last_known_status=last_status, reason=reason,
                )

            try:
                if remaining is None:
                    run = await agents.get_run(run_id)
                else:
                    run = await asyncio.wait_for(agents.get_run(run_id), timeout=remaining)
            except asyncio.TimeoutError:
                return AgentRunPending(
                    run_id=run_id, last_known_status=last_status, reason=reason,
                )

            if run.status in {"completed", "budget_exceeded"}:
                output = run.output
                if not output_schema and isinstance(output, dict) and set(output) == {"text"}:
                    return output["text"]
                if output_schema and isinstance(output, str):
                    try:
                        return json.loads(output)
                    except json.JSONDecodeError:
                        pass
                return output  # type: ignore[return-value]
            if run.status in {"failed", "timeout", "cancelled"}:
                raise RuntimeError(f"Agent run {run_id} {run.status}: {run.error or run.status}")
            if run.status not in {"queued", "running", "cancelling"}:
                raise RuntimeError(f"Agent run {run_id} has unexpected status: {run.status}")
            if run.status == "running":
                last_status = "running"
            elif run.status == "cancelling":
                last_status = "cancelling"
            else:
                last_status = "queued"

            # Recompute the remaining budget after every request, so the sleep
            # cannot consume the workflow's return margin.
            sleep_for = _poll_interval(time.monotonic() - started_waiting)
            if wait_deadline is not None:
                sleep_for = min(sleep_for, max(0, wait_deadline - time.monotonic()))
            if workflow_deadline is not None:
                sleep_for = min(
                    sleep_for,
                    max(0, (workflow_deadline - datetime.now(timezone.utc)).total_seconds()
                        - workflow_return_margin),
                )
            await asyncio.sleep(sleep_for)
