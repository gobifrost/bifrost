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
        llm_endpoint=llm_config.endpoint,
        llm_openai_transport=llm_config.openai_transport,
        llm_anthropic_prompt_cache_supported=(
            llm_config.anthropic_prompt_cache_supported
        ),
        llm_default_max_tokens=llm_config.default_max_tokens,
        llm_extra_params=llm_config.extra_params,
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
    llm_endpoint: str | None = None,
    llm_openai_transport: str | None = None,
    llm_anthropic_prompt_cache_supported: bool | None = None,
    llm_default_max_tokens: int | None = None,
    llm_extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the immutable snapshot dict from resolved pieces."""
    organization_id = getattr(agent, "organization_id", None)
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
            # Credentials deliberately remain live, but every other resolved
            # profile setting is part of this immutable execution contract.
            "endpoint": llm_endpoint,
            "openai_transport": llm_openai_transport,
            "anthropic_prompt_cache_supported": (
                llm_anthropic_prompt_cache_supported
            ),
            "default_max_tokens": llm_default_max_tokens,
            "extra_params": dict(llm_extra_params or {}),
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
        # Snapshot construction is also used by bounded admission fixtures.
        # Older/partial Agent projections do not always include this optional
        # relationship key; absence means no organization scope, not a crash.
        "organization_id": str(organization_id) if organization_id else None,
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


def is_synthetic_snapshot(snapshot: dict[str, Any] | None) -> bool:
    """True when a snapshot carries the evaluation-synthetic marker."""
    if not snapshot:
        return False
    marker = snapshot.get("evaluation") or {}
    return marker.get("mode") == "evaluation_synthetic" and bool(
        marker.get("evaluation_only")
    )


def synthetic_snapshot_from_candidate(
    candidate_snapshot: dict[str, Any],
    *,
    case_input_hash: str | None = None,
) -> dict[str, Any]:
    """Documented test-run entry point: candidate -> engine execution snapshot.

    Only the Evaluation service calls this. Production enqueue
    (``snapshot_agent``) never produces evaluation-marked snapshots, and
    production paths reject them. The frozen candidate content — prompt,
    model, tools, delegates, limits — is copied verbatim so a resumed
    synthetic run never re-reads live Agent configuration.
    """
    if not is_synthetic_snapshot(candidate_snapshot):
        raise SnapshotError(
            "Synthetic execution snapshots require an evaluation-marked "
            "candidate snapshot; production snapshots cannot run in "
            "evaluation_synthetic mode."
        )
    snapshot = {
        "format_version": EXECUTION_SNAPSHOT_VERSION,
        "agent_id": candidate_snapshot.get("agent_id"),
        "agent_name": candidate_snapshot.get("agent_name"),
        "agent_updated_at": candidate_snapshot.get("agent_updated_at"),
        "system_prompt": candidate_snapshot.get("system_prompt"),
        "model": dict(candidate_snapshot.get("model") or {}),
        "tools": list(candidate_snapshot.get("tools") or []),
        "delegated_agents": list(candidate_snapshot.get("delegated_agents") or []),
        "system_tools": list(candidate_snapshot.get("system_tools") or []),
        "knowledge_sources": list(
            candidate_snapshot.get("knowledge_sources") or []
        ),
        "roles": list(candidate_snapshot.get("roles") or []),
        "organization_id": candidate_snapshot.get("organization_id"),
        "limits": dict(candidate_snapshot.get("limits") or {}),
        "evaluation": {
            "mode": "evaluation_synthetic",
            "evaluation_only": True,
            "candidate_hash": candidate_snapshot.get("snapshot_hash"),
            "case_input_hash": case_input_hash,
        },
        "snapshotted_at": datetime.now(timezone.utc).isoformat(),
    }
    return validate_snapshot(snapshot)
