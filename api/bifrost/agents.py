"""Bifrost SDK — Agent invocation from workflows."""
from __future__ import annotations

import json
import logging
from typing import Any

from .client import get_client, raise_for_status_with_detail
from .models import AgentRun, AgentRunHandle

logger = logging.getLogger(__name__)


class AgentPausedError(Exception):
    """Raised when an agent run is requested for a paused agent.

    The execute endpoint returns HTTP 200 with a structured paused body so that
    webhook senders can treat pause as a graceful state. The SDK helper, however,
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
        timeout: int = 1800,
    ) -> dict[str, Any] | str:
        """Run an agent and wait for the result.

        Args:
            agent_name: Name of the agent to run.
            input: Structured input data for the agent.
            output_schema: JSON Schema for the expected output.
            timeout: Maximum seconds to wait (default 30 min).

        Returns:
            Structured dict if output_schema was provided, otherwise string.

        Raises:
            RuntimeError: If the agent run fails.
            ValueError: If the agent is not found.
            AgentPausedError: If the target agent is paused (is_active=False).
        """
        client = get_client()
        response = await client.post(
            "/api/agent-runs/execute",
            json={
                "agent_name": agent_name,
                "input": input or {},
                "output_schema": output_schema,
                "timeout": timeout,
            },
            # Client read-timeout must outlast the server-side cap, otherwise
            # httpx tears down the connection before the server can return.
            timeout=timeout + 10,
        )
        raise_for_status_with_detail(response)
        data = response.json()

        if isinstance(data, dict) and data.get("status") == "paused":
            raise AgentPausedError(
                data.get("message") or f"Agent '{agent_name}' is paused.",
                agent_id=data.get("agent_id"),
            )

        if data.get("error"):
            raise RuntimeError(f"Agent run failed: {data['error']}")

        output = data.get("output")
        if output_schema and isinstance(output, str):
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return output
        return output  # type: ignore[return-value]
