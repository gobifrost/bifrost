"""Unit tests for external-harness Builder workspace execution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.orm.agents import Agent
from src.models.orm.solution_builder import SolutionBuilderSession
from src.services.builder.mcp_harness import (
    BuilderMCPHarness,
    BuilderMCPHarnessForbidden,
)
from src.services.builder.scaffold import builder_agent_id
from src.services.solutions.access import SolutionAction


def _agent(solution_id):
    agent = MagicMock(spec=Agent)
    agent.id = builder_agent_id(solution_id)
    agent.solution_id = solution_id
    agent.bundle_path = "skills/bifrost-build"
    return agent


def _harness(db):
    return BuilderMCPHarness(
        db,
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=False,
        is_external=False,
        user_email="builder@example.com",
        user_name="Builder",
        can_build=True,
    )


@pytest.mark.asyncio
async def test_authorize_requires_solutions_build_scope():
    solution_id = uuid4()
    session = SolutionBuilderSession(
        id=uuid4(),
        solution_id=solution_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=session)
    harness = _harness(db)
    harness.can_build = False

    with pytest.raises(BuilderMCPHarnessForbidden, match="solutions.build"):
        await harness._authorize(_agent(solution_id), session.id, "read_file")


@pytest.mark.asyncio
async def test_authorize_requires_deterministic_builder_agent(monkeypatch):
    solution_id = uuid4()
    session = SolutionBuilderSession(
        id=uuid4(),
        solution_id=solution_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=session)
    wrong_agent = _agent(solution_id)
    wrong_agent.id = uuid4()

    async def accessible(*args, **kwargs):
        return (SimpleNamespace(), SimpleNamespace())

    monkeypatch.setattr(
        "src.services.builder.private_solutions.load_accessible_private_solution",
        accessible,
    )

    with pytest.raises(BuilderMCPHarnessForbidden):
        await _harness(db)._authorize(wrong_agent, session.id, "list_files")


@pytest.mark.asyncio
async def test_authorize_uses_view_for_reads_and_edit_for_mutations(monkeypatch):
    solution_id = uuid4()
    session = SolutionBuilderSession(
        id=uuid4(),
        solution_id=solution_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=session)
    seen_actions = []

    async def accessible(*args, **kwargs):
        seen_actions.append(kwargs["action"])
        return (SimpleNamespace(), SimpleNamespace())

    monkeypatch.setattr(
        "src.services.builder.private_solutions.load_accessible_private_solution",
        accessible,
    )
    harness = _harness(db)
    agent = _agent(solution_id)
    db.scalar = AsyncMock(return_value=agent)

    await harness._authorize(agent, session.id, "read_file")
    await harness._authorize(agent, session.id, "write_file")

    assert seen_actions == [SolutionAction.VIEW, SolutionAction.EDIT]


@pytest.mark.asyncio
async def test_mutation_runs_through_turn_service_and_returns_revision_metadata(
    monkeypatch,
):
    solution_id = uuid4()
    base_revision_id = uuid4()
    output_revision_id = uuid4()
    turn_id = uuid4()
    session = SolutionBuilderSession(
        id=uuid4(),
        solution_id=solution_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
    )
    db = MagicMock()
    db.commit = AsyncMock()
    agent = _agent(solution_id)

    async def fake_call(**kwargs):
        return {
            "content": "Wrote app/main.tsx.",
            "structured_content": {"path": "app/main.tsx"},
            "builder_session_id": str(session.id),
            "revision_id": None,
            "revision_created": True,
        }

    captured = {}

    class FakeTurnService:
        def __init__(self, db, limits=None):
            captured["db"] = db
            captured["limits"] = limits

        async def run_turn(self, solution_id_arg, **kwargs):
            captured["solution_id"] = solution_id_arg
            captured.update(kwargs)
            await kwargs["mutate"](object())
            return SimpleNamespace(
                id=turn_id,
                base_revision_id=base_revision_id,
                output_revision_id=output_revision_id,
                deploy_job_id=None,
            )

    async def fake_enqueue(db_arg, solution_id_arg, **kwargs):
        captured["deploy"] = (db_arg, solution_id_arg, kwargs["revision_id"])
        kwargs["turn"].deploy_job_id = uuid4()

    monkeypatch.setattr(
        "src.services.builder.mcp_harness.BuilderTurnService",
        FakeTurnService,
    )
    monkeypatch.setattr(
        "src.services.builder.agent_turns.enqueue_builder_turn_deploy",
        fake_enqueue,
    )
    harness = _harness(db)
    monkeypatch.setattr(harness, "_call_workspace_tool", fake_call)

    result = await harness._mutate(
        session=session,
        agent=agent,
        tool_name="write_file",
        arguments={"path": "app/main.tsx", "content": "export default null"},
    )

    assert captured["solution_id"] == solution_id
    assert captured["session_id"] == session.id
    assert result["turn_id"] == str(turn_id)
    assert result["base_revision_id"] == str(base_revision_id)
    assert result["revision_id"] == str(output_revision_id)
    assert result["revision_created"] is True
    assert result["deploy_job_id"] is not None
