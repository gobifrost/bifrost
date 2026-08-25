"""Focused contracts for Bifrost's native Pydantic AI retry transport."""

from __future__ import annotations

import logging

import httpx2
import pytest

from src.services.agent_runtime.retry_transport import (
    MAX_ATTEMPTS,
    _create_retry_transport,
    ai_retry_context,
)


def _client(handler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=_create_retry_transport(httpx2.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_retries_429_with_retry_after_and_logs_context(caplog) -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"})
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
    assert record.retry_after_seconds == 0


@pytest.mark.asyncio
async def test_exhaustion_is_bounded_to_three_total_attempts() -> None:
    attempts = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(503, headers={"Retry-After": "0"})

    async with _client(handler) as client:
        with pytest.raises(httpx2.HTTPStatusError):
            await client.get("https://api.example.test/model")

    assert attempts == MAX_ATTEMPTS


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
        with pytest.raises(httpx2.HTTPStatusError):
            await client.get("https://api.example.test/model")

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
        with pytest.raises(httpx2.HTTPStatusError):
            await client.get("https://api.example.test/model")

    assert attempts == MAX_ATTEMPTS
