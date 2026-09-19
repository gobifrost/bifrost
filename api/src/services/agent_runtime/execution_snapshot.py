"""Immutable execution snapshots: pin Agent configuration at enqueue.

PostgreSQL is authoritative for every admitted AgentRun. The snapshot
captures the resolved prompt, model identity, tool schemas, delegation
grants, system-tool grants, and effective limits so a resumed run never
silently adopts a changed Agent. Secrets are never snapshotted: model
identity travels as profile/provider/model names and credentials are
re-resolved from the live credential store at execution time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent
from src.services.execution.agent_helpers import (
    build_agent_system_prompt,
    resolve_agent_tools,
)
from src.services.llm.factory import get_llm_config

EXECUTION_SNAPSHOT_VERSION = 1


class SnapshotError(Exception):
    """Agent configuration cannot be snapshotted for durable execution."""


async def snapshot_agent(
    session: AsyncSession,
    agent: Agent,
    *,
    caller_user_id: UUID | None = None,
) -> dict[str, Any]:
    """Resolve and freeze everything a run needs to execute without Redis.

    Must be called in the same transaction that admits the AgentRun.
    """
    if not agent.is_active:
        raise SnapshotError(f"Agent '{agent.name}' is paused.")
    llm_config = await get_llm_config(session, profile_id=agent.llm_profile_id)
    tool_definitions, tool_id_map = await resolve_agent_tools(
        agent, session, caller_user_id=caller_user_id
    )
    return build_execution_snapshot(
        agent=agent,
        system_prompt=build_agent_system_prompt(
            agent, execution_context={"mode": "autonomous"}
        ),
        llm_profile_id=agent.llm_profile_id,
        llm_provider=llm_config.provider,
        llm_model=llm_config.model,
        llm_max_tokens=agent.llm_max_tokens,
        tool_definitions=tool_definitions,
        tool_id_map=tool_id_map,
    )


def build_execution_snapshot(
    *,
    agent: Agent,
    system_prompt: str,
    llm_profile_id: UUID | None,
    llm_provider: str,
    llm_model: str,
    llm_max_tokens: int | None,
    tool_definitions: Any,
    tool_id_map: dict[str, UUID],
) -> dict[str, Any]:
    """Assemble the immutable snapshot dict from resolved pieces."""
    return {
        "format_version": EXECUTION_SNAPSHOT_VERSION,
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "agent_updated_at": (
            agent.updated_at.isoformat()
            if getattr(agent, "updated_at", None) is not None
            else None
        ),
        "system_prompt": system_prompt,
        "model": {
            "profile_id": str(llm_profile_id) if llm_profile_id else None,
            "provider": llm_provider,
            "model": llm_model,
            "llm_max_tokens": llm_max_tokens,
        },
        "tools": [
            {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.parameters,
                "target_id": str(tool_id_map[definition.name])
                if definition.name in tool_id_map
                else None,
            }
            for definition in tool_definitions
        ],
        "delegated_agents": [
            {"id": str(d.id), "name": d.name}
            for d in (agent.delegated_agents or [])
            if d.is_active
        ],
        "system_tools": list(agent.system_tools or []),
        "knowledge_sources": list(agent.knowledge_sources or []),
        "roles": sorted(str(role.id) for role in (agent.roles or [])),
        "limits": {
            "max_iterations": agent.max_iterations,
            "max_token_budget": agent.max_token_budget,
            "max_run_timeout": agent.max_run_timeout,
        },
        "snapshotted_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Return the snapshot or raise a clear recovery error for legacy rows."""
    if not snapshot:
        raise SnapshotError(
            "AgentRun predates durable execution snapshots and has no "
            "immutable configuration; it cannot resume without guessing live "
            "Agent configuration. Re-enqueue the work."
        )
    if snapshot.get("format_version") != EXECUTION_SNAPSHOT_VERSION:
        raise SnapshotError(
            f"Unsupported execution snapshot version "
            f"{snapshot.get('format_version')!r}; runtime supports "
            f"{EXECUTION_SNAPSHOT_VERSION}."
        )
    return snapshot


def snapshot_tool_targets(snapshot: dict[str, Any]) -> dict[str, UUID]:
    """Rebuild the tool-name -> workflow/connection ID map from a snapshot."""
    targets: dict[str, UUID] = {}
    for tool in snapshot.get("tools", []):
        target_id = tool.get("target_id")
        if target_id is not None:
            targets[tool["name"]] = UUID(str(target_id))
    return targets
