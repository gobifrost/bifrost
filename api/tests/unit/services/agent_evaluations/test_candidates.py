"""Candidate snapshot tests: overlays freeze, tenants isolate, base is untouched."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.contracts.agent_evaluations import CandidateOverlay
from src.services.agent_evaluations.candidates import (
    CandidateError,
    assert_production_snapshot,
    build_candidate_snapshot,
    create_candidate,
    is_evaluation_snapshot,
    snapshot_hash,
    validate_overlay_delegate_ids,
    validate_overlay_tool_ids,
)


_TOOL_ID = uuid4()
_AGENT_ID = uuid4()


def _base_kwargs(**over):
    agent_id = _AGENT_ID
    kwargs = dict(
        base_agent_id=agent_id,
        base_agent_name="support",
        base_agent_updated_at="2026-09-18T00:00:00+00:00",
        base_system_prompt="Be helpful.",
        base_model={"profile_id": None, "llm_max_tokens": 1000},
        base_tools=[{"name": "get_ticket", "target_id": str(_TOOL_ID)}],
        base_delegated_agents=[],
        base_system_tools=[],
        base_limits={
            "max_iterations": 10,
            "max_token_budget": 5000,
            "max_run_timeout": 600,
        },
        overlays=CandidateOverlay(),
    )
    kwargs.update(over)
    return kwargs


def test_prompt_only_overlay_freezes_and_hashes():
    snap = build_candidate_snapshot(
        **_base_kwargs(overlays=CandidateOverlay(system_prompt="Be brief."))
    )
    assert snap["system_prompt"] == "Be brief."
    assert snap["evaluation"]["evaluation_only"] is True
    assert snap["snapshot_hash"] == snapshot_hash(
        {k: v for k, v in snap.items() if k != "snapshot_hash"}
    )
    assert is_evaluation_snapshot(snap) is True


def test_tool_only_overlay_replaces_tool_set():
    tool_id = uuid4()
    snap = build_candidate_snapshot(
        **_base_kwargs(
            overlays=CandidateOverlay(tool_ids=[tool_id]),
            overlay_tool_definitions=[{"name": "list_tickets", "target_id": str(tool_id)}],
        )
    )
    assert [t["name"] for t in snap["tools"]] == ["list_tickets"]


def test_empty_tool_overlay_removes_base_tool_grants():
    snap = build_candidate_snapshot(
        **_base_kwargs(
            overlays=CandidateOverlay(tool_ids=[]),
            overlay_tool_definitions=[],
        )
    )
    assert snap["tools"] == []


def test_model_only_overlay_keeps_prompt_and_tools():
    profile_id = uuid4()
    snap = build_candidate_snapshot(
        **_base_kwargs(overlays=CandidateOverlay(llm_profile_id=profile_id))
    )
    assert snap["model"]["profile_id"] == str(profile_id)
    assert snap["system_prompt"] == "Be helpful."
    assert [t["name"] for t in snap["tools"]] == ["get_ticket"]


def test_candidate_preserves_resolved_tool_and_model_snapshot():
    snap = build_candidate_snapshot(
        **_base_kwargs(
            base_model={"profile_id": "base", "provider": "openai", "model": "gpt"},
            base_tool_definitions=[
                {
                    "name": "get_ticket",
                    "description": "Fetch a ticket.",
                    "parameters": {"type": "object"},
                    "target_id": str(_TOOL_ID),
                }
            ],
            overlay_model={"profile_id": "judge", "provider": "openai", "model": "gpt-next"},
            overlays=CandidateOverlay(llm_profile_id=uuid4()),
        )
    )
    assert snap["model"]["model"] == "gpt-next"
    assert snap["tools"][0]["parameters"] == {"type": "object"}


def test_combined_overlays_apply_limits_and_schema():
    snap = build_candidate_snapshot(
        **_base_kwargs(
            overlays=CandidateOverlay(
                system_prompt="Short.",
                max_iterations=3,
                max_token_budget=100,
                output_schema={"type": "object"},
            )
        )
    )
    assert snap["limits"]["max_iterations"] == 3
    assert snap["output_schema"] == {"type": "object"}


def test_snapshot_is_stable_and_frozen():
    first = build_candidate_snapshot(**_base_kwargs())
    second = build_candidate_snapshot(**_base_kwargs())
    assert first["snapshot_hash"] == second["snapshot_hash"]


def test_unknown_tool_overlay_rejected():
    with pytest.raises(CandidateError, match="inaccessible"):
        validate_overlay_tool_ids([uuid4()], resolvable_tool_ids=set())


def test_delegate_overlay_requires_a_resolved_grant():
    delegate_id = uuid4()
    validate_overlay_delegate_ids(
        [delegate_id], resolvable_delegate_ids={str(delegate_id)}
    )
    with pytest.raises(CandidateError, match="inaccessible"):
        validate_overlay_delegate_ids([delegate_id], resolvable_delegate_ids=set())


def test_production_enqueue_rejects_candidates():
    snap = build_candidate_snapshot(**_base_kwargs())
    with pytest.raises(CandidateError, match="cannot run in production"):
        assert_production_snapshot(snap)
    assert_production_snapshot(None)
    assert_production_snapshot({"format_version": 1})


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def _agent():
    return SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        name="support",
        updated_at=None,
        system_prompt="Be helpful.",
        llm_profile_id=None,
        llm_max_tokens=1000,
        tools=[],
        delegated_agents=[],
        system_tools=[],
        max_iterations=10,
        max_token_budget=5000,
        max_run_timeout=600,
    )


async def _noop():
    return None


def test_create_candidate_does_not_mutate_base_agent():
    import asyncio

    agent = _agent()
    before = dict(agent.__dict__)
    session = _FakeSession()
    candidate = asyncio.get_event_loop().run_until_complete(
        create_candidate(
            session,
            base_agent=agent,
            overlays=CandidateOverlay(system_prompt="Be brief."),
            resolvable_tool_ids=set(),
            resolvable_delegate_ids=set(),
            owner_org_id=agent.organization_id,
            name="brief",
            created_by="tester",
        )
    )
    assert dict(agent.__dict__) == before
    assert candidate.evaluation_only is True
    assert candidate.snapshot["system_prompt"] == "Be brief."
    assert session.added == [candidate]


def test_create_candidate_cross_tenant_denied():
    import asyncio

    agent = _agent()
    session = _FakeSession()
    session._candidate_org_id = uuid4()
    with pytest.raises(CandidateError, match="Cross-tenant"):
        asyncio.get_event_loop().run_until_complete(
            create_candidate(
                session,
                base_agent=agent,
                overlays=CandidateOverlay(),
                resolvable_tool_ids=set(),
                resolvable_delegate_ids=set(),
            )
        )


def test_create_candidate_persists_authorized_owner_org():
    import asyncio

    agent = _agent()
    session = _FakeSession()
    candidate = asyncio.get_event_loop().run_until_complete(
        create_candidate(
            session,
            base_agent=agent,
            overlays=CandidateOverlay(),
            resolvable_tool_ids=set(),
            resolvable_delegate_ids=set(),
            owner_org_id=agent.organization_id,
        )
    )
    assert candidate.org_id == agent.organization_id
