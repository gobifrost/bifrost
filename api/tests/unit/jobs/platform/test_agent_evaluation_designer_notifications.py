"""Designer materialization notification boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from src.jobs.platform.agent_evaluation import reconcile_agent_evaluation_jobs
from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent


def _proposal() -> dict:
    return {
        "name": "designer notification case",
        "input": {"task": "look up ticket-0001"},
        "fixture": {
            "entities": {"ticket": {"ticket-0001": {"id": "ticket-0001"}}},
            "allowed_tools": ["get_ticket"],
            "rules": [],
        },
        "simulator_policy": {},
        "assertions": [{"type": "tool_called", "params": {"tool": "get_ticket"}}],
        "expected_tools": ["get_ticket"],
        "forbidden_tools": [],
        "coverage": "success",
    }


async def _designer_fixture(db_session, *, output: dict, with_duplicate: bool = False):
    agent = Agent(
        name=f"Designer notification {uuid4().hex}",
        system_prompt="Be deterministic.",
        created_by="test@example.com",
    )
    suite = AgentEvaluationSuite(
        name=f"designer-notification-{uuid4().hex}",
        status="draft",
        version=1,
    )
    db_session.add(agent)
    await db_session.flush()
    suite.agent_id = agent.id
    db_session.add(suite)
    await db_session.flush()
    if with_duplicate:
        db_session.add(
            AgentEvaluationCase(
                suite_id=suite.id,
                name="existing duplicate",
                input=_proposal()["input"],
                fixture=_proposal()["fixture"],
                assertions=_proposal()["assertions"],
                tags=["success"],
            )
        )
    run = AgentRun(
        agent_id=agent.id,
        trigger_type="evaluation_synthetic",
        status="completed",
        output=output,
        correlation={
            "evaluation_designer": True,
            "designer_suite_id": str(suite.id),
            "designer_tool_schemas": {
                "get_ticket": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                }
            },
        },
    )
    db_session.add(run)
    await db_session.commit()
    return agent, suite, run


async def _cleanup(db_session, agent: Agent, suite: AgentEvaluationSuite, run: AgentRun):
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.execute(
        delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id)
    )
    await db_session.execute(delete(Agent).where(Agent.id == agent.id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_designer_notification_observes_committed_drafts_and_is_not_repeated(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """The UI hint is sent after another session can read the materialization."""
    from src.core.database import get_db_context

    agent, suite, run = await _designer_fixture(
        db_session, output={"proposals": [_proposal()]}
    )
    observed: list[tuple[bool, int]] = []

    async def observe_commit(published_run, _agent_name):
        async with get_db_context() as observer:
            observed_run = await observer.get(AgentRun, published_run.id)
            assert observed_run is not None
            draft_count = await observer.scalar(
                select(func.count())
                .select_from(AgentEvaluationCase)
                .where(AgentEvaluationCase.suite_id == suite.id)
            )
            observed.append((observed_run.correlation["designer_materialized"], draft_count))

    monkeypatch.setattr("src.core.pubsub.publish_agent_run_update", observe_commit)
    try:
        assert await reconcile_agent_evaluation_jobs() == 1
        assert observed == [(True, 1)]
        assert await reconcile_agent_evaluation_jobs() == 0
        assert observed == [(True, 1)]
    finally:
        await _cleanup(db_session, agent, suite, run)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "with_duplicate", "error_fragment", "suite_status"),
    [
        ({"proposals": [_proposal()]}, True, None, "draft"),
        ({"not_proposals": []}, False, "failed validation", "draft"),
        ({"proposals": [_proposal()]}, False, "already published", "published"),
    ],
)
async def test_zero_draft_designer_terminal_transitions_notify_once(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    output: dict,
    with_duplicate: bool,
    error_fragment: str | None,
    suite_status: str,
):
    """A zero return can be deduplication or a terminal validation error."""
    agent, suite, run = await _designer_fixture(
        db_session, output=output, with_duplicate=with_duplicate
    )
    suite.status = suite_status
    await db_session.commit()
    publish = AsyncMock()
    monkeypatch.setattr("src.core.pubsub.publish_agent_run_update", publish)
    try:
        assert await reconcile_agent_evaluation_jobs() == 0
        await db_session.refresh(run)
        assert run.correlation["designer_materialized"] is True
        if error_fragment is None:
            assert "designer_error" not in run.correlation
        else:
            assert error_fragment in run.correlation["designer_error"]
        publish.assert_awaited_once()
        assert await reconcile_agent_evaluation_jobs() == 0
        publish.assert_awaited_once()
    finally:
        await _cleanup(db_session, agent, suite, run)


@pytest.mark.asyncio
async def test_designer_materialization_rollback_does_not_publish(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """A failed materialization rolls back its marker and produces no UI hint."""
    from src.services.agent_evaluations import test_designer

    agent, suite, run = await _designer_fixture(
        db_session, output={"proposals": [_proposal()]}
    )
    publish = AsyncMock()

    async def fail_after_mutation(_session, locked_run):
        correlation = dict(locked_run.correlation)
        correlation["designer_materialized"] = True
        locked_run.correlation = correlation
        raise RuntimeError("materialization failed")

    monkeypatch.setattr(test_designer, "materialize_designer_drafts", fail_after_mutation)
    monkeypatch.setattr("src.core.pubsub.publish_agent_run_update", publish)
    try:
        assert await reconcile_agent_evaluation_jobs() == 0
        await db_session.refresh(run)
        assert "designer_materialized" not in run.correlation
        publish.assert_not_awaited()
    finally:
        await _cleanup(db_session, agent, suite, run)


@pytest.mark.asyncio
async def test_designer_notification_outage_preserves_committed_drafts(db_session, monkeypatch):
    from src.core import pubsub

    agent, suite, run = await _designer_fixture(db_session, output={"proposals": [_proposal()]})
    publish = AsyncMock(side_effect=RuntimeError("pubsub unavailable"))
    monkeypatch.setattr(pubsub, "publish_agent_run_update", publish)
    try:
        assert await reconcile_agent_evaluation_jobs() == 1
        await db_session.refresh(run)
        assert run.correlation["designer_materialized"] is True
        assert await db_session.scalar(select(func.count()).select_from(AgentEvaluationCase).where(
            AgentEvaluationCase.suite_id == suite.id
        )) == 1
        assert await reconcile_agent_evaluation_jobs() == 0
        publish.assert_awaited_once()
    finally:
        await _cleanup(db_session, agent, suite, run)


@pytest.mark.asyncio
async def test_designer_commit_failure_rolls_back_without_notification(db_session, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from src.core import pubsub

    agent, suite, run = await _designer_fixture(db_session, output={"proposals": [_proposal()]})
    original_commit = AsyncSession.commit
    failed = False

    async def fail_one_commit(session):
        nonlocal failed
        if session is not db_session and not failed:
            failed = True
            await session.flush()
            raise RuntimeError("commit failed")
        await original_commit(session)

    publish = AsyncMock()
    monkeypatch.setattr(pubsub, "publish_agent_run_update", publish)
    monkeypatch.setattr(AsyncSession, "commit", fail_one_commit)
    try:
        assert await reconcile_agent_evaluation_jobs() == 0
        assert failed
        await db_session.refresh(run)
        assert not run.correlation.get("designer_materialized")
        assert await db_session.scalar(select(func.count()).select_from(AgentEvaluationCase).where(
            AgentEvaluationCase.suite_id == suite.id
        )) == 0
        publish.assert_not_awaited()
    finally:
        await _cleanup(db_session, agent, suite, run)
