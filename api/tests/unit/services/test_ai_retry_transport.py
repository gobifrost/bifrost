"""Focused contracts for Bifrost's native Pydantic AI retry transport."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.services.agent_runtime.retry_transport import (
    FALLBACK_WAIT_INITIAL_SECONDS,
    FALLBACK_WAIT_JITTER_SECONDS,
    FALLBACK_WAIT_MAX_SECONDS,
    MAX_ATTEMPTS,
    _create_retry_transport,
    ai_retry_context,
)


async def _no_sleep(_delay: float) -> None:
    pass


def _client(
    handler: Callable[[httpx2.Request], Awaitable[httpx2.Response]],
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=_create_retry_transport(
            httpx2.MockTransport(handler),
            sleep=_no_sleep,
        )
    )


@pytest.mark.asyncio
async def test_retries_429_with_retry_after_and_logs_context(caplog) -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(429, headers={"Retry-After": "5"})
        return httpx2.Response(200, json={"ok": True})

    async with _client(handler) as client:
        with (
            caplog.at_level(logging.WARNING),
            ai_retry_context(
                provider="openrouter",
                model="test-model",
                surface="test",
            ),
        ):
            response = await client.get("https://openrouter.ai/test")

    assert response.status_code == 200
    assert attempts == 2
    record = next(r for r in caplog.records if r.message == "ai_provider_request_retry")
    assert record.provider == "openrouter"
    assert record.model == "test-model"
    assert record.surface == "test"
    assert record.status_code == 429
    assert record.retry_after_seconds == 5
    assert record.sleep_seconds == 5


@pytest.mark.asyncio
async def test_exhaustion_returns_final_response_and_logs_terminal_context(
    caplog,
) -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(503, headers={"Retry-After": "0"})

    async with _client(handler) as client:
        with (
            caplog.at_level(logging.WARNING),
            ai_retry_context(
                provider="openrouter",
                model="test-model",
                surface="test",
            ),
        ):
            response = await client.get("https://api.example.test/model")

    assert response.status_code == 503
    assert attempts == MAX_ATTEMPTS
    record = next(
        r for r in caplog.records if r.message == "ai_provider_request_terminal"
    )
    assert record.provider == "openrouter"
    assert record.model == "test-model"
    assert record.surface == "test"
    assert record.attempt == MAX_ATTEMPTS
    assert record.status_code == 503
    assert record.total_sleep_seconds >= 0
    assert record.error_type == "HTTPStatusError"


@pytest.mark.asyncio
async def test_non_retryable_400_is_returned_once() -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(400)

    async with _client(handler) as client:
        response = await client.get("https://api.example.test/model")

    assert response.status_code == 400
    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_after_over_limit_is_terminal() -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(429, headers={"Retry-After": "61"})

    async with _client(handler) as client:
        response = await client.get("https://api.example.test/model")

    assert response.status_code == 429
    assert attempts == 1


@pytest.mark.asyncio
async def test_transport_errors_use_the_same_bounded_budget() -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        raise httpx2.ConnectError("provider unavailable", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx2.ConnectError):
            await client.get("https://api.example.test/model")

    assert attempts == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_invalid_retry_after_falls_back_to_bounded_retry() -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(429, headers={"Retry-After": "not-a-delay"})

    async with _client(handler) as client:
        response = await client.get("https://api.example.test/model")

    assert response.status_code == 429
    assert attempts == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_exhausted_429_remains_rate_limit_error_through_pydantic_adapter() -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            429,
            headers={"Retry-After": "0"},
            json={
                "error": {
                    "message": "provider rate limit reached",
                    "type": "rate_limit_error",
                    "code": "rate_limit",
                }
            },
        )

    async with _client(handler) as http_client:
        openai_client = AsyncOpenAI(
            api_key="test-key",
            base_url="https://openrouter.example.test/v1",
            http_client=http_client,
            max_retries=0,
        )
        model = OpenAIChatModel(
            "test-model",
            provider=OpenAIProvider(openai_client=openai_client),
        )

        with pytest.raises(ModelHTTPError) as exc_info:
            await model.request(
                [ModelRequest(parts=[UserPromptPart(content="hello")])],
                None,
                ModelRequestParameters(),
            )

    assert attempts == MAX_ATTEMPTS
    assert exc_info.value.status_code == 429
    assert "provider rate limit reached" in str(exc_info.value)
    assert "Connection error" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_concurrent_429_bursts_use_divergent_bounded_fallback_schedules(
    caplog,
    monkeypatch,
) -> None:
    jitter_values = iter((0.0, 0.5, 1.0, 0.0, 0.5, 1.0))
    monkeypatch.setattr(
        "tenacity.wait.random.uniform",
        lambda _start, _end: next(jitter_values),
    )

    async def recover_after_burst(worker: int) -> int:
        attempts = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            if attempts < MAX_ATTEMPTS:
                return httpx2.Response(429)
            return httpx2.Response(200, json={"ok": True})

        async with _client(handler) as client:
            with ai_retry_context(
                provider="openrouter",
                model="test-model",
                surface=f"burst-{worker}",
            ):
                response = await client.get(
                    f"https://openrouter.example.test/workers/{worker}"
                )
        assert response.status_code == 200
        return attempts

    with caplog.at_level(logging.WARNING):
        attempts = await asyncio.gather(*(recover_after_burst(i) for i in range(3)))

    assert attempts == [MAX_ATTEMPTS] * 3
    schedules: dict[str, list[float]] = {}
    for record in caplog.records:
        if record.message == "ai_provider_request_retry":
            schedules.setdefault(record.surface, []).append(record.sleep_seconds)

    assert set(schedules) == {"burst-0", "burst-1", "burst-2"}
    assert all(len(delays) == MAX_ATTEMPTS - 1 for delays in schedules.values())
    totals = [sum(delays) for delays in schedules.values()]
    minimum_total = FALLBACK_WAIT_INITIAL_SECONDS * 3
    maximum_total = min(
        FALLBACK_WAIT_INITIAL_SECONDS + FALLBACK_WAIT_JITTER_SECONDS,
        FALLBACK_WAIT_MAX_SECONDS,
    ) + FALLBACK_WAIT_MAX_SECONDS
    assert all(minimum_total <= total <= maximum_total for total in totals)
    assert len(set(totals)) == len(totals)
