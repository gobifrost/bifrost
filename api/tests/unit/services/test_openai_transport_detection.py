from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import BadRequestError, RateLimitError

from src.services.openai_transport_detection import detect_openai_transport


def _status_error(error_type, status: int, message: str):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://models.example.test/v1/responses"),
    )
    return error_type(message, response=response, body={"message": message})


def _client(*, responses_result=None, responses_error=None, chat_error=None):
    return SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=responses_result,
                side_effect=responses_error,
            )
        ),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=MagicMock(), side_effect=chat_error)
            )
        ),
    )


@pytest.mark.asyncio
async def test_detect_openai_transport_prefers_responses() -> None:
    client = _client(responses_result=MagicMock())

    with patch(
        "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
    ):
        transport = await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
        )

    assert transport == "responses"
    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_openai_transport_falls_back_for_unsupported_model() -> None:
    client = _client(
        responses_error=_status_error(
            BadRequestError, 400, "Model not supported for the Responses API"
        )
    )

    with patch(
        "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
    ):
        transport = await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
        )

    assert transport == "chat_completions"
    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_openai_transport_fails_when_both_transports_are_unavailable() -> (
    None
):
    client = _client(
        responses_error=_status_error(
            BadRequestError, 400, "Model not supported for the Responses API"
        ),
        chat_error=_status_error(BadRequestError, 400, "Unknown deployment"),
    )

    with (
        patch(
            "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
        ),
        pytest.raises(ValueError, match="unavailable through both"),
    ):
        await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
        )

    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_openai_transport_does_not_fallback_for_rate_limits() -> None:
    client = _client(
        responses_error=_status_error(RateLimitError, 429, "Rate limit exceeded")
    )

    with (
        patch(
            "src.services.openai_transport_detection.AsyncOpenAI", return_value=client
        ),
        pytest.raises(ValueError, match="transport was not changed"),
    ):
        await detect_openai_transport(
            api_key="test-key",
            endpoint="https://models.example.test/v1",
            model="test-model",
        )

    client.chat.completions.create.assert_not_awaited()
