"""Evaluation-only Agent candidate snapshots.

A candidate freezes a base published Agent plus caller-supplied overlays
(prompt, model/profile, tool IDs, delegated agents, limits, output schema)
into an immutable snapshot at creation time. Creating or running a
candidate never mutates the base Agent or its relationship tables, and
production enqueue paths reject evaluation snapshots — only the
Evaluation service may create a synthetic AgentRun from one.

Snapshot shape reuses the durable ``execution_snapshot`` vocabulary
(``format_version``, ``model``, ``tools``, ``delegated_agents``,
``system_tools``, ``limits``) plus an ``evaluation`` marker block, so the
synthetic runner can feed the same resume/contract machinery.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.models.contracts.agent_evaluations import CandidateOverlay
from src.services.agent_evaluations.simulator_models import canonical_hash

CANDIDATE_SNAPSHOT_VERSION = 1

EVALUATION_MARKER = "evaluation_synthetic"


class CandidateError(Exception):
    """A candidate overlay cannot be built or is not usable."""


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    """Canonical SHA-256 over the frozen snapshot (base revision + overlays)."""
    return canonical_hash(snapshot)


def build_candidate_snapshot(
    *,
    base_agent_id: UUID,
    base_agent_name: str,
    base_agent_updated_at: str | None,
    base_system_prompt: str,
    base_model: dict[str, Any],
    base_tools: list[dict[str, Any]],
    base_delegated_agents: list[dict[str, str]],
    base_system_tools: list[str],
    base_limits: dict[str, Any],
    overlays: CandidateOverlay,
    overlay_tool_definitions: list[dict[str, Any]] | None = None,
    base_tool_definitions: list[dict[str, Any]] | None = None,
    overlay_delegated_agents: list[dict[str, str]] | None = None,
    overlay_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the complete candidate snapshot. Pure function of its inputs.

    Later Agent/tool edits cannot change the returned dict: every override
    is resolved here and the result is hashed by the caller.
    """
    system_prompt = overlays.system_prompt or base_system_prompt
    model = dict(overlay_model if overlay_model is not None else base_model)
    if overlays.llm_profile_id is not None:
        model["profile_id"] = str(overlays.llm_profile_id)
    if overlays.llm_max_tokens is not None:
        model["llm_max_tokens"] = overlays.llm_max_tokens
    tools = (
        list(overlay_tool_definitions)
        if overlay_tool_definitions is not None
        else list(base_tool_definitions if base_tool_definitions is not None else base_tools)
    )
    delegated = (
        list(overlay_delegated_agents)
        if overlay_delegated_agents is not None
        else list(base_delegated_agents)
    )
    system_tools = (
        list(overlays.system_tools)
        if overlays.system_tools is not None
        else list(base_system_tools)
    )
    limits = dict(base_limits)
    if overlays.max_iterations is not None:
        limits["max_iterations"] = overlays.max_iterations
    if overlays.max_token_budget is not None:
        limits["max_token_budget"] = overlays.max_token_budget
    if overlays.max_run_timeout is not None:
        limits["max_run_timeout"] = overlays.max_run_timeout
    snapshot: dict[str, Any] = {
        "format_version": CANDIDATE_SNAPSHOT_VERSION,
        "agent_id": str(base_agent_id),
        "agent_name": base_agent_name,
        "agent_updated_at": base_agent_updated_at,
        "system_prompt": system_prompt,
        "model": model,
        "tools": tools,
        "delegated_agents": delegated,
        "system_tools": system_tools,
        "limits": limits,
        "output_schema": (
            overlays.output_schema if overlays.output_schema is not None else None
        ),
        "evaluation": {
            "mode": EVALUATION_MARKER,
            "evaluation_only": True,
        },
    }
    snapshot["snapshot_hash"] = snapshot_hash(
        {k: v for k, v in snapshot.items() if k != "snapshot_hash"}
    )
    return snapshot


def is_evaluation_snapshot(snapshot: dict[str, Any] | None) -> bool:
    """True when a snapshot is marked evaluation-only."""
    if not snapshot:
        return False
    marker = snapshot.get("evaluation") or {}
    return bool(marker.get("evaluation_only"))


def assert_production_snapshot(snapshot: dict[str, Any] | None) -> None:
    """Reject evaluation snapshots on production enqueue paths."""
    if is_evaluation_snapshot(snapshot):
        raise CandidateError(
            "Evaluation candidate snapshots cannot run in production; "
            "only the Evaluation service may create synthetic runs from one."
        )


def validate_overlay_tool_ids(
    overlay_tool_ids: list[UUID] | None,
    resolvable_tool_ids: set[str],
) -> None:
    """Reject overlays referencing tools the caller could not attach normally."""
    if overlay_tool_ids is None:
        return
    unknown = [str(tid) for tid in overlay_tool_ids if str(tid) not in resolvable_tool_ids]
    if unknown:
        raise CandidateError(
            "Candidate references inactive, unpublished, or inaccessible "
            f"tools: {', '.join(sorted(unknown))}."
        )


def validate_overlay_delegate_ids(
    overlay_delegate_ids: list[UUID] | None,
    resolvable_delegate_ids: set[str],
) -> None:
    if overlay_delegate_ids is None:
        return
    unknown = [
        str(aid) for aid in overlay_delegate_ids if str(aid) not in resolvable_delegate_ids
    ]
    if unknown:
        raise CandidateError(
            "Candidate references inaccessible delegated agents: "
            f"{', '.join(sorted(unknown))}."
        )


async def create_candidate(
    session,
    *,
    base_agent,
    overlays: CandidateOverlay,
    resolvable_tool_ids: set[str],
    resolvable_delegate_ids: set[str],
    overlay_tool_definitions: list[dict[str, Any]] | None = None,
    base_tool_definitions: list[dict[str, Any]] | None = None,
    overlay_delegated_agents: list[dict[str, str]] | None = None,
    base_execution_snapshot: dict[str, Any] | None = None,
    overlay_model: dict[str, Any] | None = None,
    owner_org_id: UUID | None = None,
    name: str | None = None,
    created_by: str | None = None,
):
    """Persist an immutable candidate snapshot for an accessible base Agent.

    Enforces tenant alignment (caller passes only same-org resolvable IDs),
    freezes the snapshot + hash, and never touches the base Agent row or its
    ``agent_tools`` / ``agent_delegations`` relationships.
    """
    from src.models.orm.agent_evaluations import AgentCandidateSnapshot

    if base_agent.organization_id is not None and owner_org_id != base_agent.organization_id:
        raise CandidateError("Cross-tenant candidate creation is denied.")
    validate_overlay_tool_ids(overlays.tool_ids, resolvable_tool_ids)
    validate_overlay_delegate_ids(
        overlays.delegated_agent_ids, resolvable_delegate_ids
    )
    snapshot = build_candidate_snapshot(
        base_agent_id=base_agent.id,
        base_agent_name=base_agent.name,
        base_agent_updated_at=(
            base_agent.updated_at.isoformat()
            if getattr(base_agent, "updated_at", None) is not None
            else None
        ),
        base_system_prompt=base_agent.system_prompt,
        base_model=dict((base_execution_snapshot or {}).get("model") or {
            "profile_id": str(base_agent.llm_profile_id) if base_agent.llm_profile_id else None,
            "llm_max_tokens": base_agent.llm_max_tokens,
        }),
        base_tools=list((base_execution_snapshot or {}).get("tools") or [
            {"name": t.name, "target_id": str(t.id)} for t in (base_agent.tools or [])
        ]),
        base_delegated_agents=[
            {"id": str(d.id), "name": d.name}
            for d in (base_agent.delegated_agents or [])
        ],
        base_system_tools=list((base_execution_snapshot or {}).get("system_tools") or base_agent.system_tools or []),
        base_limits=dict((base_execution_snapshot or {}).get("limits") or {
            "max_iterations": base_agent.max_iterations,
            "max_token_budget": base_agent.max_token_budget,
            "max_run_timeout": base_agent.max_run_timeout,
        }),
        overlays=overlays,
        overlay_tool_definitions=overlay_tool_definitions,
        base_tool_definitions=base_tool_definitions,
        overlay_delegated_agents=overlay_delegated_agents,
        overlay_model=overlay_model,
    )
    candidate = AgentCandidateSnapshot(
        # Global base Agents still yield tenant-private candidate material.
        org_id=owner_org_id,
        base_agent_id=base_agent.id,
        base_agent_updated_at=getattr(base_agent, "updated_at", None),
        name=name,
        overlays=overlays.model_dump(mode="json"),
        snapshot=snapshot,
        snapshot_hash=snapshot["snapshot_hash"],
        evaluation_only=True,
        created_by=created_by,
    )
    session.add(candidate)
    return candidate
