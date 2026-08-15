"""Run summarizer end-to-end with mocked LLM client.

Validates the Task 12 implementation: ``summarize_run`` loads a completed
``AgentRun``, asks the configured summarization model for a structured
extraction, and persists the parsed result onto the run record + an
``AIUsage`` row.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import openai
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.services.llm import LLMResponse
from src.services.execution.run_summarizer import (
    _clamp_confidence,
    _extract_json_object,
    _retry_delay_from_exception,
    summarize_run,
)


def _build_rate_limit_error(retry_after: str | None = None) -> openai.RateLimitError:
    """Build a realistic-looking openai.RateLimitError for retry tests.

    The SDK's exception requires an httpx.Response so ``exc.response.headers``
    works; retry-after honoring depends on that attribute chain.
    """
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    response = httpx.Response(
        status_code=429,
        headers=headers,
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    return openai.RateLimitError(
        "429 rate limit exceeded (test)",
        response=response,
        body=None,
    )


@pytest_asyncio.fixture
async def seed_completed_run(db_session, seed_agent):
    """Insert a completed AgentRun with input/output set, committed so a
    fresh session inside ``summarize_run`` can read it.

    Cleans up via the session_factory after the test (the row is committed
    past the ``db_session`` rollback boundary).
    """
    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=2,
        tokens_used=300,
        input={"message": "Reset my password"},
        output={"text": "Routed to Support"},
        summary_status="pending",
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    # Manual cleanup since we committed past the rollback boundary.
    await db_session.execute(
        delete(AIUsage).where(AIUsage.agent_run_id == run.id)
    )
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


def _build_mock_llm_response(
    content: str,
    input_tokens: int = 200,
    output_tokens: int = 40,
    model: str = "claude-haiku-4-5",
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
async def test_summarize_run_populates_asked_did_confidence(
    async_session_factory, seed_completed_run
):
    """Happy path: LLM returns valid JSON; run gets fields populated, AIUsage row inserted."""
    from src.services.execution import run_summarizer as mod

    mock_resp = _build_mock_llm_response(
        '{"asked": "reset my password", "did": "routed to Support", '
        '"answered": "Sent password-reset link", '
        '"confidence": 0.9, "confidence_reason": "clear intent", '
        '"metadata": {"intent": "password_reset"}}'
    )
    mock_client = _build_mock_client(mock_resp)

    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.asked == "reset my password"
        assert run.did == "routed to Support"
        assert run.answered == "Sent password-reset link"
        assert run.confidence == 0.9
        assert run.confidence_reason == "clear intent"
        assert run.summary_status == "completed"
        assert run.summary_generated_at is not None
        # Prompt version stamped so backfill roll-forward can target old runs.
        from src.services.execution.run_summarizer import (
            SUMMARIZE_PROMPT_VERSION,
        )
        assert run.summary_prompt_version == SUMMARIZE_PROMPT_VERSION
        # Metadata merged (LLM-extracted intent)
        assert run.run_metadata.get("intent") == "password_reset"
        usages = (
            (
                await db.execute(
                    select(AIUsage).where(AIUsage.agent_run_id == run.id)
                )
            )
            .scalars()
            .all()
        )
        assert any(u.model == "claude-haiku-4-5" for u in usages)


@pytest.mark.asyncio
async def test_summarize_run_includes_agent_context_in_prompt(
    async_session_factory, seed_completed_run, seed_agent
):
    """The summarizer must pass the agent's name + system prompt to the LLM so
    the model can describe *what this specific run did* instead of paraphrasing
    the agent's role. This is the v2 prompt contract — regressing to a
    context-free payload would bring back generic 'did' outputs.
    """
    from src.services.execution import run_summarizer as mod

    mock_resp = _build_mock_llm_response(
        '{"asked": "x", "did": "y", "confidence": 0.5, '
        '"confidence_reason": "z", "metadata": {}}'
    )
    mock_client = _build_mock_client(mock_resp)

    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    # Inspect what the LLM received. The user message is a JSON-serialized
    # payload carrying agent_name + agent_system_prompt + input + output.
    assert mock_client.complete.await_count >= 1
    call_kwargs = mock_client.complete.await_args.kwargs
    messages = call_kwargs["messages"]
    user_msg = next(m for m in messages if m.role == "user")
    assert seed_agent.name in user_msg.content
    assert "You are a test agent." in user_msg.content


@pytest.mark.asyncio
async def test_summarize_run_invalid_json_marks_failed(
    async_session_factory, seed_completed_run
):
    """LLM returns garbage; run.summary_status = 'failed', summary_error stored."""
    from src.services.execution import run_summarizer as mod

    mock_client = _build_mock_client(_build_mock_llm_response("not json at all"))

    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.summary_status == "failed"
        assert run.summary_error is not None
        assert "JSON" in run.summary_error or "json" in run.summary_error


@pytest.mark.asyncio
async def test_summarize_run_empty_content_marks_failed_with_actionable_error(
    async_session_factory, seed_completed_run
):
    """OpenAI-family models sometimes return empty content when output is
    filtered or reasoning tokens consume the budget. Error message should be
    actionable — 'check model output filtering' not 'Expecting value line 1'.
    """
    from src.services.execution import run_summarizer as mod

    mock_client = _build_mock_client(_build_mock_llm_response(""))

    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "gpt-4o-mini")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.summary_status == "failed"
        assert run.summary_error is not None
        assert "empty content" in run.summary_error.lower()


@pytest.mark.asyncio
async def test_summarize_run_truncated_json_marks_failed_with_budget_hint(
    async_session_factory, seed_completed_run
):
    """If the model runs out of max_tokens mid-object, the error should tell
    the admin that's what happened, not 'Expecting value line 1'."""
    from src.services.execution import run_summarizer as mod

    mock_client = _build_mock_client(
        _build_mock_llm_response('{"asked": "reset my password", "did": "rout')
    )

    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "gpt-4o-mini")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.summary_status == "failed"
        assert run.summary_error is not None
        assert "truncated" in run.summary_error.lower()


@pytest.mark.asyncio
async def test_summarize_run_idempotent_when_completed(
    async_session_factory, seed_completed_run
):
    """Already-summarized run returns immediately; no LLM call."""
    from src.services.execution import run_summarizer as mod

    # Pre-mark as completed
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        run.summary_status = "completed"
        await db.commit()

    mock_client = _build_mock_client(_build_mock_llm_response("{}"))
    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    mock_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_summarize_run_skipped_when_run_not_completed(
    async_session_factory, db_session, seed_agent
):
    """If the run's status is not 'completed', summarize_run returns early without calling LLM."""
    from src.services.execution import run_summarizer as mod

    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="failed",
        iterations_used=0,
        tokens_used=0,
        summary_status="pending",
    )
    db_session.add(run)
    await db_session.commit()

    try:
        mock_client = _build_mock_client(_build_mock_llm_response("{}"))
        with patch.object(
            mod,
            "get_summarization_client",
            new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
        ):
            await summarize_run(run.id, async_session_factory)
        mock_client.complete.assert_not_called()
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.commit()


def test_clamp_confidence():
    assert _clamp_confidence(0.5) == 0.5
    assert _clamp_confidence(0.0) == 0.0
    assert _clamp_confidence(1.0) == 1.0
    assert _clamp_confidence(1.5) == 1.0  # clamped
    assert _clamp_confidence(-0.2) == 0.0
    assert _clamp_confidence(None) is None
    assert _clamp_confidence("not a number") is None
    assert _clamp_confidence("0.7") == 0.7  # numeric string parsed


def test_retry_delay_honors_retry_after_header():
    """429 with Retry-After should override the exponential backoff."""
    exc = _build_rate_limit_error(retry_after="3")
    # Attempt index shouldn't matter when the header is present.
    assert _retry_delay_from_exception(exc, attempt=0) == 3.0
    assert _retry_delay_from_exception(exc, attempt=4) == 3.0


def test_retry_delay_falls_back_to_exponential_with_jitter():
    """Without Retry-After, delay grows with attempt and has jitter."""
    exc = _build_rate_limit_error(retry_after=None)
    # Jitter is in [0.5, 1.0) of base — we just bound it.
    d0 = _retry_delay_from_exception(exc, attempt=0)
    d2 = _retry_delay_from_exception(exc, attempt=2)
    assert 1.0 <= d0 <= 2.0  # base=2, jitter half-to-full
    assert 4.0 <= d2 <= 8.0  # base=8
    # Cap enforced.
    huge = _retry_delay_from_exception(exc, attempt=10)
    assert huge <= 30.0


@pytest.mark.asyncio
async def test_summarize_run_retries_429_then_succeeds(
    async_session_factory, seed_completed_run
):
    """Transient 429 on the first attempt should be retried, not persisted as
    a terminal failure. Regression: prior to the fix, OpenRouter 429s during
    a backfill permanently failed ~525 runs because the summarizer's generic
    `except Exception` treated rate limits the same as invalid JSON."""
    from src.services.execution import run_summarizer as mod

    ok_resp = _build_mock_llm_response(
        '{"asked": "reset", "did": "routed", "confidence": 0.8, '
        '"confidence_reason": "clear", "metadata": {}}'
    )
    mock_client = MagicMock()
    # First call: 429. Second call: success.
    mock_client.complete = AsyncMock(
        side_effect=[_build_rate_limit_error(retry_after="0"), ok_resp]
    )
    mock_client.provider_name = "openai"

    # Patch asyncio.sleep so the test doesn't actually wait.
    with (
        patch.object(
            mod,
            "get_summarization_client",
            new=AsyncMock(return_value=(mock_client, "gemini-3-flash")),
        ),
        patch.object(mod.asyncio, "sleep", new=AsyncMock()),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    assert mock_client.complete.await_count == 2
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.summary_status == "completed"
        assert run.asked == "reset"


@pytest.mark.asyncio
async def test_summarize_run_429_exhausts_retries_marks_failed(
    async_session_factory, seed_completed_run
):
    """After the retry budget is exhausted, the run is marked failed with a
    message that identifies the transient error type — admins should not have
    to grep worker logs to tell 'provider was rate-limiting us' from 'model
    returned bad JSON'."""
    from src.services.execution import run_summarizer as mod

    mock_client = MagicMock()
    mock_client.complete = AsyncMock(
        side_effect=_build_rate_limit_error(retry_after="0")
    )
    mock_client.provider_name = "openai"

    with (
        patch.object(
            mod,
            "get_summarization_client",
            new=AsyncMock(return_value=(mock_client, "gemini-3-flash")),
        ),
        patch.object(mod.asyncio, "sleep", new=AsyncMock()),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    # _MAX_RETRIES=5 retries after the initial attempt → 6 total calls.
    assert mock_client.complete.await_count == mod._MAX_RETRIES + 1
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.summary_status == "failed"
        assert run.summary_error is not None
        assert "RateLimitError" in run.summary_error
        assert "after retries" in run.summary_error


class TestExtractJsonObject:
    """Guards against the docker-log regression: every backfilled run's
    summarizer call returned content that json.loads rejected because the LLM
    either wrapped the object in markdown fences or added prose around it.
    _extract_json_object must tolerate both."""

    def test_plain_json_roundtrips(self):
        raw = '{"asked": "foo", "did": "bar"}'
        assert _extract_json_object(raw) == raw

    def test_strips_markdown_code_fence_with_lang(self):
        raw = '```json\n{"asked": "foo"}\n```'
        import json
        assert json.loads(_extract_json_object(raw)) == {"asked": "foo"}

    def test_strips_markdown_code_fence_without_lang(self):
        raw = '```\n{"asked": "foo"}\n```'
        import json
        assert json.loads(_extract_json_object(raw)) == {"asked": "foo"}

    def test_strips_prose_preamble(self):
        raw = 'Here is the summary:\n{"asked": "foo", "did": "bar"}'
        import json
        assert json.loads(_extract_json_object(raw)) == {
            "asked": "foo",
            "did": "bar",
        }

    def test_strips_trailing_prose(self):
        raw = '{"asked": "foo"}\n\nLet me know if you need anything else.'
        import json
        assert json.loads(_extract_json_object(raw)) == {"asked": "foo"}

    def test_tolerates_braces_in_quoted_strings(self):
        raw = '{"url": "https://x.com/{id}", "did": "routed"}'
        import json
        assert json.loads(_extract_json_object(raw)) == {
            "url": "https://x.com/{id}",
            "did": "routed",
        }

    def test_handles_escaped_quotes_inside_strings(self):
        raw = '{"did": "said \\"hello\\"", "asked": "x"}'
        import json
        parsed = json.loads(_extract_json_object(raw))
        assert parsed["did"] == 'said "hello"'

    def test_nested_object_closes_at_outer_brace(self):
        raw = '{"metadata": {"ticket_id": "4821"}, "asked": "x"}'
        import json
        parsed = json.loads(_extract_json_object(raw))
        assert parsed["metadata"] == {"ticket_id": "4821"}
        assert parsed["asked"] == "x"

    def test_empty_input_returns_empty(self):
        assert _extract_json_object("") == ""
        assert _extract_json_object("   ") == ""

    def test_no_object_returns_stripped_input(self):
        # Caller will still get a JSONDecodeError — we don't try to synthesise.
        result = _extract_json_object("totally not json")
        assert "{" not in result
