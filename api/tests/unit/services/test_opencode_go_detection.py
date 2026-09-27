"""Probe-order, fallback, and header contract for Go wire-surface detection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from anthropic import NotFoundError as AnthropicNotFoundError
from openai import BadRequestError, NotFoundError, PermissionDeniedError, RateLimitError

from src.services.opencode_go_detection import detect_opencode_go_wire_api

ENDPOINT = "https://opencode.ai/zen/go/v1"


def _status_error(error_type, status: int, message: str):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://opencode.ai/zen/go/v1/chat/completions"),
    )
    return error_type(message, response=response, body={"message": message})


def _openai_client(*, chat_result=None, chat_error=None, responses_result=None):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=chat_result, side_effect=chat_error)
            )
        ),
        responses=SimpleNamespace(create=AsyncMock(return_value=responses_result)),
    )


def _anthropic_client(*, messages_result=None, messages_error=None):
    return SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(return_value=messages_result, side_effect=messages_error)
        )
    )


def _detect_kwargs(**overrides):
    kwargs = {"api_key": "test-key", "endpoint": ENDPOINT, "model": "omen-alpha"}
    kwargs.update(overrides)
    return kwargs


@pytest.mark.asyncio
async def test_detect_prefers_chat_completions_without_further_probes() -> None:
    openai_client = _openai_client(chat_result=MagicMock())
    anthropic_client = _anthropic_client()

    with (
        patch(
            "src.services.opencode_go_detection.AsyncOpenAI",
            return_value=openai_client,
        ),
        patch(
            "src.services.opencode_go_detection.AsyncAnthropic",
            return_value=anthropic_client,
        ),
    ):
        assert await detect_opencode_go_wire_api(**_detect_kwargs()) == "chat_completions"

    openai_client.responses.create.assert_not_awaited()
    anthropic_client.messages.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_falls_back_to_responses_for_unsupported_chat_model() -> None:
    openai_client = _openai_client(
        chat_error=_status_error(NotFoundError, 404, "No endpoints found for model"),
        responses_result=MagicMock(),
    )

    with (
        patch(
            "src.services.opencode_go_detection.AsyncOpenAI",
            return_value=openai_client,
        ),
        patch(
            "src.services.opencode_go_detection.AsyncAnthropic",
            return_value=_anthropic_client(),
        ),
    ):
        assert await detect_opencode_go_wire_api(**_detect_kwargs()) == "responses"

    openai_client.chat.completions.create.assert_awaited_once()
    openai_client.responses.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_falls_back_to_messages_when_openai_surfaces_reject() -> None:
    openai_client = _openai_client(
        chat_error=_status_error(NotFoundError, 404, "No endpoints found for model"),
        responses_result=None,
    )
    openai_client.responses.create.side_effect = _status_error(
        BadRequestError, 400, "Model not supported for the Responses API"
    )
    anthropic_client = _anthropic_client(messages_result=MagicMock())

    with (
        patch(
            "src.services.opencode_go_detection.AsyncOpenAI",
            return_value=openai_client,
        ),
        patch(
            "src.services.opencode_go_detection.AsyncAnthropic",
            return_value=anthropic_client,
        ),
    ):
        assert await detect_opencode_go_wire_api(**_detect_kwargs()) == "messages"

    anthropic_client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_sends_identity_headers_on_every_probe() -> None:
    openai_client = _openai_client(
        chat_error=_status_error(NotFoundError, 404, "No endpoints found for model"),
        responses_result=None,
    )
    openai_client.responses.create.side_effect = _status_error(
        BadRequestError, 400, "Model not supported for the Responses API"
    )
    anthropic_client = _anthropic_client(messages_result=MagicMock())

    with (
        patch(
            "src.services.opencode_go_detection.AsyncOpenAI",
            return_value=openai_client,
        ),
        patch(
            "src.services.opencode_go_detection.AsyncAnthropic",
            return_value=anthropic_client,
        ),
    ):
        await detect_opencode_go_wire_api(**_detect_kwargs())

    for call in (
        openai_client.chat.completions.create.await_args,
        openai_client.responses.create.await_args,
        anthropic_client.messages.create.await_args,
    ):
        headers = call.kwargs["extra_headers"]
        assert headers["User-Agent"].startswith("Bifrost/")
        assert headers["x-opencode-session"].startswith("probe-")


@pytest.mark.asyncio
async def test_detect_stops_on_rejected_api_key() -> None:
    openai_client = _openai_client(
        chat_error=_status_error(PermissionDeniedError, 401, "Invalid API key"),
    )
    anthropic_client = _anthropic_client()

    with (
        patch(
            "src.services.opencode_go_detection.AsyncOpenAI",
            return_value=openai_client,
        ),
        patch(
            "src.services.opencode_go_detection.AsyncAnthropic",
            return_value=anthropic_client,
        ),
        pytest.raises(ValueError, match="rejected the API key"),
    ):
        await detect_opencode_go_wire_api(**_detect_kwargs())

    openai_client.responses.create.assert_not_awaited()
    anthropic_client.messages.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_fails_when_no_surface_serves_the_model() -> None:
    openai_client = _openai_client(
        chat_error=_status_error(NotFoundError, 404, "No endpoints found for model"),
    )
    openai_client.responses.create.side_effect = _status_error(
        BadRequestError, 400, "Model not supported for the Responses API"
    )
    anthropic_client = _anthropic_client(
        messages_error=_status_error(
            AnthropicNotFoundError, 404, "Model not found"
        )
    )

    with (
        patch(
            "src.services.opencode_go_detection.AsyncOpenAI",
            return_value=openai_client,
        ),
        patch(
            "src.services.opencode_go_detection.AsyncAnthropic",
            return_value=anthropic_client,
        ),
        pytest.raises(ValueError, match="unavailable through Chat Completions"),
    ):
        await detect_opencode_go_wire_api(**_detect_kwargs())


@pytest.mark.asyncio
async def test_detect_does_not_fallback_for_rate_limits() -> None:
    openai_client = _openai_client(
        chat_error=_status_error(RateLimitError, 429, "Rate limit exceeded"),
    )
    anthropic_client = _anthropic_client()

    with (
        patch(
            "src.services.opencode_go_detection.AsyncOpenAI",
            return_value=openai_client,
        ),
        patch(
            "src.services.opencode_go_detection.AsyncAnthropic",
            return_value=anthropic_client,
        ),
        pytest.raises(ValueError, match="wire surface was not changed"),
    ):
        await detect_opencode_go_wire_api(**_detect_kwargs())

    openai_client.responses.create.assert_not_awaited()
    anthropic_client.messages.create.assert_not_awaited()
