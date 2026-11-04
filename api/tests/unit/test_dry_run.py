"""Dry-run evaluation: ``evaluate_against_prompt`` with mocked LLM client.

Validates the Task 16 implementation: a single LLM call against the tuning
model, structured JSON verdict parsed back to ``DryRunResult``, and an
``AIUsage`` row recorded against the original run with ``sequence=8000``.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import delete, select

from src.models.contracts.agent_runs import DryRunRequest, DryRunResponse
from src.models.contracts.agent_tuning import (
    ApplyTuningRequest,
    ConsolidatedDryRunRequest,
    ConsolidatedDryRunResponse,
    DryRunPerRun,
)
from src.models.contracts.agents import AGENT_PROMPT_MAX_LENGTH, AgentCreate, AgentUpdate
from src.models.contracts.agent_run_flag_conversations import DryRunTurn

from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.services.execution.dry_run import evaluate_against_prompt
from src.services.llm import LLMResponse


@pytest_asyncio.fixture
async def seed_completed_run(db_session, seed_agent):
    """Insert a completed AgentRun ready for dry-run tests.

    Committed past the ``db_session`` rollback boundary so a fresh session
    inside ``evaluate_against_prompt`` can read it.
    """
    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=100,
        input={"message": "Reset my password"},
        output={"text": "Routed to Support"},
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    # Manual cleanup since we committed past the rollback boundary.
    await db_session.execute(delete(AIUsage).where(AIUsage.agent_run_id == run.id))
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


def _build_mock_llm_response(
    content: str,
    input_tokens: int = 300,
    output_tokens: int = 60,
    model: str = "claude-sonnet-4-6",
):
    """Construct the real stable response contract used by all providers."""
    return LLMResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    )


def _build_mock_client(response):
    """Construct a mock LLM client whose ``complete`` returns ``response``."""
    client = MagicMock()
    client.complete = AsyncMock(return_value=response)
    client.provider_name = "anthropic"
    return client


@pytest.mark.asyncio
async def test_dry_run_returns_structured_verdict(
    async_session_factory, seed_completed_run
):
    """Happy path: model returns valid JSON; result fields parsed correctly."""
    from src.services.execution import dry_run as mod

    mock_client = _build_mock_client(
        _build_mock_llm_response(
            '{"would_still_decide_same": false, '
            '"reasoning": "New prompt explicitly forbids routing to Support.", '
            '"alternative_action": "Reply directly with reset link.", '
            '"confidence": 0.85}'
        )
    )

    with patch.object(
        mod,
        "get_tuning_client",
        new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
    ):
        result = await evaluate_against_prompt(
            run_id=seed_completed_run.id,
            proposed_prompt="Always reply directly; never delegate.",
            session_factory=async_session_factory,
        )

    assert result.would_still_decide_same is False
    assert "Support" in result.reasoning
    assert result.alternative_action == "Reply directly with reset link."
    assert result.confidence == 0.85


@pytest.mark.asyncio
async def test_dry_run_records_ai_usage_on_original_run(
    async_session_factory, seed_completed_run
):
    """An ``AIUsage`` row with ``sequence=8000`` is recorded against the run."""
    from src.services.execution import dry_run as mod

    mock_client = _build_mock_client(
        _build_mock_llm_response(
            '{"would_still_decide_same": true, '
            '"reasoning": "No relevant change.", '
            '"alternative_action": null, '
            '"confidence": 0.7}'
        )
    )

    with patch.object(
        mod,
        "get_tuning_client",
        new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
    ):
        await evaluate_against_prompt(
            run_id=seed_completed_run.id,
            proposed_prompt="Be more polite.",
            session_factory=async_session_factory,
        )

    async with async_session_factory() as db:
        usages = (
            (
                await db.execute(
                    select(AIUsage).where(
                        AIUsage.agent_run_id == seed_completed_run.id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(usages) == 1
    usage = usages[0]
    assert usage.sequence == 8000
    assert usage.model == "claude-sonnet-4-6"
    assert usage.provider == "anthropic"
    assert usage.input_tokens == 300
    assert usage.output_tokens == 60


@pytest.mark.asyncio
async def test_dry_run_handles_invalid_json(
    async_session_factory, seed_completed_run
):
    """LLM returns garbage; result is an explicit ``evaluation_failed`` failure.

    Fail-closed: never reports ``would_still_decide_same=True`` when no
    verdict could be parsed.
    """
    from src.services.execution import dry_run as mod

    mock_client = _build_mock_client(_build_mock_llm_response("not json at all"))

    with patch.object(
        mod,
        "get_tuning_client",
        new=AsyncMock(return_value=(mock_client, "claude-sonnet-4-6")),
    ):
        result = await evaluate_against_prompt(
            run_id=seed_completed_run.id,
            proposed_prompt="Anything.",
            session_factory=async_session_factory,
        )

    assert result.evaluation_failed is True
    assert result.would_still_decide_same is False
    assert result.confidence == 0.0
    assert result.alternative_action is None
    assert "Unable to evaluate" in result.reasoning


def test_parse_evaluator_output_accepts_fenced_prefixed_json():
    """Fences and leading prose are stripped before strict parsing."""
    from src.services.execution.dry_run import parse_evaluator_output

    raw = (
        "Here is my verdict:\n```json\n"
        '{"would_still_decide_same": false, '
        '"reasoning": "New prompt forbids routing.", '
        '"alternative_action": "Reply directly.", '
        '"confidence": 0.9}\n```'
    )
    result = parse_evaluator_output(raw)
    assert result.evaluation_failed is False
    assert result.would_still_decide_same is False
    assert result.reasoning == "New prompt forbids routing."
    assert result.alternative_action == "Reply directly."
    assert result.confidence == 0.9


def test_parse_evaluator_output_accepts_trailing_prose():
    """Trailing chatter after the JSON object does not break parsing."""
    from src.services.execution.dry_run import parse_evaluator_output

    raw = (
        '{"would_still_decide_same": true, "reasoning": "No relevant change.", '
        '"alternative_action": null, "confidence": 0.7} hope this helps'
    )
    result = parse_evaluator_output(raw)
    assert result.evaluation_failed is False
    assert result.would_still_decide_same is True
    assert result.confidence == 0.7


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        "not json at all",
        "```\nno object here\n```",
        '{"a": 1}',
        # Wrong field types must fail, not coerce.
        '{"would_still_decide_same": "yes", "reasoning": "r", '
        + '"alternative_action": null, "confidence": 0.5}',
        '{"would_still_decide_same": 1, "reasoning": "r", '
        + '"alternative_action": null, "confidence": 0.5}',
        '{"would_still_decide_same": true, "reasoning": "r", '
        + '"alternative_action": null, "confidence": "high"}',
        '{"would_still_decide_same": true, "reasoning": "r", '
        + '"alternative_action": null, "confidence": true}',
        '{"would_still_decide_same": true, "reasoning": 42, '
        + '"alternative_action": null, "confidence": 0.5}',
        '{"would_still_decide_same": true, "reasoning": "r", '
        + '"alternative_action": 5, "confidence": 0.5}',
        "[1, 2, 3]",
        '{"would_still_decide_same": true, "reasoning": "r", "confidence": NaN}',
        '{"would_still_decide_same": true, "reasoning": "r", "confidence": Infinity}',

    ],
)
def test_parse_evaluator_output_failures_are_explicit(bad):
    """Irreparable output -> ``evaluation_failed``, fail-closed, never True."""
    from src.services.execution.dry_run import parse_evaluator_output

    result = parse_evaluator_output(bad)
    assert result.evaluation_failed is True
    assert result.would_still_decide_same is False
    assert result.confidence == 0.0
    assert result.alternative_action is None


def test_prompt_dtos_share_platform_cap():
    """All prompt DTOs accept >20k and reject above the 50k platform cap."""
    assert AGENT_PROMPT_MAX_LENGTH == 50000
    assert AgentCreate(name="Long prompt", system_prompt="x" * AGENT_PROMPT_MAX_LENGTH)
    assert AgentUpdate(system_prompt="x" * AGENT_PROMPT_MAX_LENGTH)
    long_prompt = "x" * 25000
    assert DryRunRequest(proposed_prompt=long_prompt).proposed_prompt == long_prompt
    assert (
        ConsolidatedDryRunRequest(proposed_prompt=long_prompt).proposed_prompt
        == long_prompt
    )
    assert ApplyTuningRequest(new_prompt=long_prompt).new_prompt == long_prompt
    too_long = "x" * (AGENT_PROMPT_MAX_LENGTH + 1)
    with pytest.raises(ValidationError):
        DryRunRequest(proposed_prompt=too_long)
    with pytest.raises(ValidationError):
        ConsolidatedDryRunRequest(proposed_prompt=too_long)
    with pytest.raises(ValidationError):
        ApplyTuningRequest(new_prompt=too_long)


def test_dry_run_responses_carry_evaluation_failed():
    """Both response contracts expose the explicit failure flag (default False)."""
    run_id = uuid4()
    assert DryRunResponse(
        run_id=run_id,
        would_still_decide_same=True,
        reasoning="ok",
        confidence=0.7,
    ).evaluation_failed is False
    per = DryRunPerRun(
        run_id=run_id,
        would_still_decide_same=False,
        reasoning="unparseable",
        confidence=0.0,
        evaluation_failed=True,
    )
    assert ConsolidatedDryRunResponse(results=[per]).results[0].evaluation_failed is True


def test_individual_review_turn_preserves_evaluation_failure():
    turn = DryRunTurn(before="Old answer", after="Unable to evaluate", predicted="down", evaluation_failed=True)
    assert DryRunTurn.model_validate(turn.model_dump()).evaluation_failed is True
