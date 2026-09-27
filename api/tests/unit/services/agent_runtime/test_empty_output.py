"""Unit tests for the blank / repetitive no-tool output circuit breaker."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.usage import RequestUsage

from src.services.agent_runtime.empty_output import (
    EMPTY_OUTPUT_FALLBACK_MAX_TOKENS,
    EmptyOutputCircuitBreaker,
    empty_output_handoff_text,
    is_blank_text,
    is_empty_no_tool_response,
    is_internally_repetitive,
    response_fingerprint,
)


def _response(
    *parts,
    output_tokens: int = 0,
    finish_reason: str | None = "stop",
) -> ModelResponse:
    return ModelResponse(
        parts=list(parts),
        usage=RequestUsage(input_tokens=10, output_tokens=output_tokens),
        model_name="test-model",
        finish_reason=finish_reason,  # type: ignore[arg-type]
    )


def _ctx():
    return MagicMock()


def _request_context():
    return MagicMock()


def _tool_call() -> ToolCallPart:
    return ToolCallPart(tool_name="my_tool", args={}, tool_call_id="tc1")


@pytest.mark.parametrize(
    ("text", "expected"),
    [(None, True), ("", True), ("   \n\t  ", True), ("hello", False)],
)
def test_is_blank_text(text, expected):
    assert is_blank_text(text) is expected


def test_empty_no_tool_detection():
    # Mirrors the incident shape: length stop, billed output, no visible text.
    assert is_empty_no_tool_response(
        _response(output_tokens=131072, finish_reason="length")
    ) is True
    assert is_empty_no_tool_response(_response(TextPart(content="   "))) is True
    assert is_empty_no_tool_response(_response(TextPart(content="answer"))) is False
    assert is_empty_no_tool_response(_response(_tool_call())) is False
    assert (
        is_empty_no_tool_response(
            _response(TextPart(content="progress"), _tool_call())
        )
        is False
    )


def test_fingerprint_ignores_formatting_case():
    assert response_fingerprint(_response(TextPart(content="Do  X"))) == (
        response_fingerprint(_response(TextPart(content="  do x\n")))
    )
    assert response_fingerprint(_response(TextPart(content="Do X"))) != (
        response_fingerprint(_response(TextPart(content="Do Y")))
    )


@pytest.mark.asyncio
async def test_first_empty_raises_single_model_retry():
    guard = EmptyOutputCircuitBreaker()

    with pytest.raises(ModelRetry):
        await guard.after_model_request(
            _ctx(),
            request_context=_request_context(),
            response=_response(output_tokens=131072, finish_reason="length"),
        )

    assert guard.fallbacks_used == 1
    assert guard.handoff_triggered is False


@pytest.mark.asyncio
async def test_second_empty_hands_off_and_preserves_usage():
    guard = EmptyOutputCircuitBreaker()
    usage = RequestUsage(input_tokens=10, output_tokens=131072)

    with pytest.raises(ModelRetry):
        await guard.after_model_request(
            _ctx(),
            request_context=_request_context(),
            response=_response(output_tokens=131072, finish_reason="length"),
        )
    handoff = await guard.after_model_request(
        _ctx(),
        request_context=_request_context(),
        response=ModelResponse(
            parts=[],
            usage=usage,
            model_name="test-model",
            finish_reason="length",  # type: ignore[arg-type]
        ),
    )

    assert guard.handoff_triggered is True
    assert guard.handoff_reason == "empty_model_output"
    assert guard.fallbacks_used == 1  # single fallback, never a second retry
    assert handoff.finish_reason == "stop"
    assert not handoff.tool_calls
    assert handoff.text and "human" in handoff.text
    # The shared UsageLimits ledger still sees the billed attempt.
    assert handoff.usage is usage


@pytest.mark.asyncio
async def test_repetitive_no_tool_output_retries_once_then_hands_off():
    guard = EmptyOutputCircuitBreaker()
    echo = "Contact the ticket owner for next steps. " * 200  # ~8K echo
    assert is_internally_repetitive(echo) is True
    assert is_internally_repetitive("A short answer.") is False
    varied = " ".join(f"Step {index} covers a new area." for index in range(200))
    assert is_internally_repetitive(varied) is False

    with pytest.raises(ModelRetry):
        await guard.after_model_request(
            _ctx(),
            request_context=_request_context(),
            response=_response(TextPart(content=echo)),
        )
    handoff = await guard.after_model_request(
        _ctx(),
        request_context=_request_context(),
        response=_response(TextPart(content=echo)),
    )

    assert guard.handoff_triggered is True
    assert guard.fallbacks_used == 1
    assert "repeated" in (handoff.text or "")
    assert empty_output_handoff_text(repeated=True) != empty_output_handoff_text(
        repeated=False
    )


@pytest.mark.asyncio
async def test_duplicate_no_tool_answer_counts_as_repetition():
    guard = EmptyOutputCircuitBreaker()

    first = await guard.after_model_request(
        _ctx(),
        request_context=_request_context(),
        response=_response(TextPart(content="Contact the owner")),
    )
    assert first.text == "Contact the owner"

    with pytest.raises(ModelRetry):
        await guard.after_model_request(
            _ctx(),
            request_context=_request_context(),
            response=_response(TextPart(content="Contact the owner")),
        )
    handoff_text_check = await guard.after_model_request(
        _ctx(),
        request_context=_request_context(),
        response=_response(TextPart(content="Contact the owner")),
    )
    assert guard.handoff_triggered is True
    assert handoff_text_check.text is not None


@pytest.mark.asyncio
async def test_novel_text_resets_consecutive_count_but_keeps_history():
    guard = EmptyOutputCircuitBreaker()

    await guard.after_model_request(
        _ctx(),
        request_context=_request_context(),
        response=_response(TextPart(content="Step one done")),
    )
    response = await guard.after_model_request(
        _ctx(),
        request_context=_request_context(),
        response=_response(TextPart(content="Step two done")),
    )
    assert response.text == "Step two done"
    assert guard.handoff_triggered is False
    assert guard.fallbacks_used == 0


@pytest.mark.asyncio
async def test_fallback_request_is_capped_below_provider_default():
    from pydantic_ai.models import ModelRequestContext

    guard = EmptyOutputCircuitBreaker()
    base = ModelRequestContext(
        model=MagicMock(),
        messages=[],
        model_settings=None,
        model_request_parameters=MagicMock(),
    )

    # Unarmed: the first request passes through untouched.
    untouched = await guard.before_model_request(MagicMock(), base)
    assert (untouched.model_settings or {}).get("max_tokens") is None

    with pytest.raises(ModelRetry):
        await guard.after_model_request(
            _ctx(),
            request_context=_request_context(),
            response=_response(finish_reason="length"),
        )

    capped = await guard.before_model_request(
        MagicMock(), replace(base, model_settings={"max_tokens": 131072})
    )
    assert capped.model_settings is not None
    assert capped.model_settings.get("max_tokens") == EMPTY_OUTPUT_FALLBACK_MAX_TOKENS
    assert EMPTY_OUTPUT_FALLBACK_MAX_TOKENS < 131072
