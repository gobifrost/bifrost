"""Trusted execution-origin values that survive asynchronous dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ExecutionOrigin = Literal["agent", "human", "unknown"]


@dataclass(frozen=True)
class ExecutionProvenance:
    """Origin facts recorded before a run crosses a process boundary.

    ``unknown`` is deliberately conservative.  It represents rows created
    before provenance existed and any unattended path that cannot prove a
    direct human or an Agent origin.
    """

    origin: ExecutionOrigin
    agent_id: UUID | None = None


def execution_provenance(
    origin: str | None, agent_id: str | UUID | None
) -> ExecutionProvenance:
    """Validate the only provenance shape allowed into durable execution."""
    if origin is None or origin == "unknown":
        if agent_id is not None:
            raise ValueError("Unknown execution provenance cannot name an agent")
        return ExecutionProvenance("unknown")
    if origin == "human":
        if agent_id is not None:
            raise ValueError("Human execution provenance cannot name an agent")
        return ExecutionProvenance("human")
    if origin != "agent":
        raise ValueError("Execution provenance origin is invalid")
    if agent_id is None:
        raise ValueError("Agent execution provenance requires an agent")
    try:
        return ExecutionProvenance("agent", UUID(str(agent_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Agent execution provenance has an invalid agent") from exc


def durable_execution_provenance(
    origin: str | None, agent_id: str | UUID | None
) -> ExecutionProvenance:
    """Read historic durable provenance without granting from malformed data."""
    try:
        return execution_provenance(origin, agent_id)
    except ValueError:
        return ExecutionProvenance("unknown")
