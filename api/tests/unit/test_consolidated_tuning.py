"""Consolidated tuning session service: propose / dry-run / apply.

Validates the Task 17 implementation:

- ``propose_consolidated_tuning`` returns one prompt proposal informed by
  every flagged run.
- A LookupError is raised when there are no flagged runs (router maps to 404).
- ``apply_consolidated_tuning`` updates the agent prompt, writes an
  ``AgentPromptHistory`` row, and clears verdicts on the affected runs.
- ``dry_run_consolidated`` calls the per-run dry-run for at most
  ``CONSOLIDATED_DRY_RUN_LIMIT`` runs even when more are flagged.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.core.principal import UserPrincipal
from src.models.orm.agent_prompt_history import AgentPromptHistory
from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.services.execution.tuning_service import (
    CONSOLIDATED_DRY_RUN_LIMIT,
    apply_consolidated_tuning,
    dry_run_consolidated,
    propose_consolidated_tuning,
)


def _build_mock_llm_response(content: str, model: str = "claude-sonnet-4-6"):
    """Construct a mock that quacks like an LLMResponse."""
    response = MagicMock()
    response.content = content
    response.input_tokens = 500
    response.output_tokens = 300
    response.model = model
    return response


def _build_mock_client(response):
    """Construct a mock LLM client whose ``complete`` returns ``response``."""
    client = MagicMock()
    client.complete = AsyncMock(return_value=response)
    client.provider_name = "anthropic"
    return client


def _superuser_principal(user) -> UserPrincipal:  # type: ignore[no-untyped-def]
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        name=user.name or "",
        organization_id=user.organization_id,
        is_superuser=True,
    )


@pytest_asyncio.fixture
async def seed_agent_with_flagged_runs(db_session, seed_agent):
    """Seed three thumbs-down completed runs for ``seed_agent``."""
    runs: list[AgentRun] = []
    for i in range(3):
        run = AgentRun(
            id=uuid4(),
            agent_id=seed_agent.id,
            trigger_type="test",
            status="completed",
            iterations_used=1,
            tokens_used=100,
            input={"message": f"question {i}"},
            output={"text": f"answer {i}"},
            verdict="down",
            verdict_note=f"wrong because {i}",
            verdict_set_at=datetime.now(timezone.utc),
        )
        db_session.add(run)
        runs.append(run)
    await db_session.commit()

    yield seed_agent, runs

    for run in runs:
        await db_session.execute(
            delete(AgentRunFlagConversation).where(
                AgentRunFlagConversation.run_id == run.id
            )
        )
        await db_session.execute(
            delete(AIUsage).where(AIUsage.agent_run_id == run.id)
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.execute(
        delete(AgentPromptHistory).where(
            AgentPromptHistory.agent_id == seed_agent.id
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_propose_returns_proposal_with_flagged_runs(
    db_session, seed_agent_with_flagged_runs, seed_user
):
    """LLM is called once; response parsed into proposal containing all flagged runs."""
    from src.services.execution import tuning_service as mod

    agent, runs = seed_agent_with_flagged_runs
    mock_client = _build_mock_client(
        _build_mock_llm_response(
            '{"summary": "Routing is too eager.", '
            '"proposed_prompt": "You are a careful agent. Never route without confirmation."}'
        )
    )

    with patch.object(
        mod,
        "get_tuning_client",
        new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
    ):
        proposal = await propose_consolidated_tuning(
            agent.id,
            db_session,
            _superuser_principal(seed_user),
        )

    assert proposal.summary == "Routing is too eager."
    assert "careful agent" in proposal.proposed_prompt
    assert set(proposal.affected_run_ids) == {r.id for r in runs}
    mock_client.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_propose_no_flagged_runs_raises(db_session, seed_agent, seed_user):
    """No flagged runs -> LookupError (router maps to 404)."""
    with pytest.raises(LookupError):
        await propose_consolidated_tuning(
            seed_agent.id,
            db_session,
            _superuser_principal(seed_user),
        )


@pytest.mark.asyncio
async def test_apply_updates_prompt_creates_history_resets_verdicts(
    db_session, seed_agent_with_flagged_runs, seed_user
):
    """Apply updates Agent.system_prompt, inserts history, and clears verdicts."""
    agent, runs = seed_agent_with_flagged_runs
    original_prompt = agent.system_prompt
    new_prompt = "Be more careful in routing decisions."

    applied = await apply_consolidated_tuning(
        agent_id=agent.id,
        new_prompt=new_prompt,
        reason="Consolidated tuning from 3 flagged runs.",
        user_id=seed_user.id,
        db=db_session,
        user=_superuser_principal(seed_user),
    )
    await db_session.commit()

    assert applied.agent_id == agent.id
    assert set(applied.affected_run_ids) == {r.id for r in runs}

    # Verify Agent.system_prompt updated
    refreshed_agent = (
        await db_session.execute(
            select(type(agent)).where(type(agent).id == agent.id)
        )
    ).scalar_one()
    assert refreshed_agent.system_prompt == new_prompt

    # Verify history row
    histories = (
        (
            await db_session.execute(
                select(AgentPromptHistory).where(
                    AgentPromptHistory.agent_id == agent.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(histories) == 1
    assert histories[0].previous_prompt == original_prompt
    assert histories[0].new_prompt == new_prompt
    assert histories[0].changed_by == seed_user.id
    assert histories[0].reason == "Consolidated tuning from 3 flagged runs."
    assert histories[0].id == applied.history_id

    # Verify verdicts cleared
    for run in runs:
        refreshed_run = (
            await db_session.execute(
                select(AgentRun).where(AgentRun.id == run.id)
            )
        ).scalar_one()
        assert refreshed_run.verdict is None
        assert refreshed_run.verdict_note is None


@pytest.mark.asyncio
async def test_dry_run_caps_at_limit(db_session, seed_agent, seed_user):
    """Even with 12 flagged runs, dry-run only evaluates the cap."""
    from src.services.execution import tuning_service as mod

    # Seed CONSOLIDATED_DRY_RUN_LIMIT + 2 flagged runs
    extra = CONSOLIDATED_DRY_RUN_LIMIT + 2
    runs: list[AgentRun] = []
    for i in range(extra):
        run = AgentRun(
            id=uuid4(),
            agent_id=seed_agent.id,
            trigger_type="test",
            status="completed",
            iterations_used=1,
            tokens_used=100,
            input={"message": f"q{i}"},
            output={"text": f"a{i}"},
            verdict="down",
        )
        db_session.add(run)
        runs.append(run)
    await db_session.commit()

    try:
        # Mock evaluate_against_prompt directly to avoid LLM calls
        async def fake_evaluate(*, run_id, proposed_prompt, session_factory):
            from src.services.execution.dry_run import DryRunResult

            return DryRunResult(
                would_still_decide_same=True,
                reasoning="cap test",
                alternative_action=None,
                confidence=0.9,
            )

        with patch.object(mod, "evaluate_against_prompt", new=fake_evaluate):
            results = await dry_run_consolidated(
                agent_id=seed_agent.id,
                proposed_prompt="Stricter prompt.",
                db=db_session,
                session_factory=MagicMock(),
                user=_superuser_principal(seed_user),
            )

        assert len(results) == CONSOLIDATED_DRY_RUN_LIMIT
    finally:
        for run in runs:
            await db_session.execute(
                delete(AgentRun).where(AgentRun.id == run.id)
            )
        await db_session.commit()
