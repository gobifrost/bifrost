"""Process-scoped Pydantic AI transport using the shared AI HTTP retry policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx2
from pydantic_ai.models import get_user_agent
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig
from tenacity import AsyncRetrying, retry_if_exception

from shared.ai_http_retry import (
    _http_retry_budget_available,
    _log_before_sleep,
    _log_terminal,
    _should_retry,
    _stop_retrying,
    _validate_response,
    _wait_for_retry,
    ai_retry_context as ai_retry_context,
)

_retry_http_client: httpx2.AsyncClient | None = None


class AIRetryTransport(AsyncHTTPX2TenacityTransport):
    """Retry transient failures while returning the final HTTP response.

    Provider SDKs classify returned error responses themselves. Letting the
    validation ``HTTPStatusError`` escape this lower transport boundary makes
    OpenAI-compatible clients misclassify an exhausted 429 as a connection
    failure instead of a rate-limit response.
    """

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        retrying = AsyncRetrying(**self.config)
        try:
            async for attempt in retrying:
                with attempt:
                    response = await self.wrapped.handle_async_request(request)
                    response.request = request
                    if self.validate_response is None:
                        return response
                    try:
                        self.validate_response(response)
                    except httpx2.HTTPStatusError as exc:
                        attempt_number = attempt.retry_state.attempt_number
                        if (
                            not _should_retry(exc)
                            or not _http_retry_budget_available(
                                exc,
                                attempt.retry_state,
                            )
                        ):
                            _log_terminal(
                                exc,
                                attempt=attempt_number,
                                total_sleep_seconds=attempt.retry_state.idle_for,
                            )
                            return response
                        await response.aclose()
                        raise
                    return response
        except Exception as exc:
            _log_terminal(
                exc,
                attempt=int(retrying.statistics.get("attempt_number", 1)),
                total_sleep_seconds=float(retrying.statistics.get("idle_for", 0.0)),
            )
            raise
        raise RuntimeError("AI retry transport made no request attempts")


def _create_retry_transport(
    wrapped: httpx2.AsyncBaseTransport | None = None,
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> AIRetryTransport:
    config = RetryConfig(
        retry=retry_if_exception(_should_retry),
        wait=_wait_for_retry,
        stop=_stop_retrying,
        before_sleep=_log_before_sleep,
        reraise=True,
    )
    if sleep is not None:
        config["sleep"] = sleep
    return AIRetryTransport(
        config=config,
        wrapped=wrapped,
        validate_response=_validate_response,
    )


def _create_retry_http_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=_create_retry_transport(),
        timeout=httpx2.Timeout(timeout=600, connect=5),
        headers={"User-Agent": get_user_agent()},
    )


def get_ai_retry_http_client() -> httpx2.AsyncClient:
    """Return the single retrying provider client owned by this process."""

    global _retry_http_client
    if _retry_http_client is None or _retry_http_client.is_closed:
        _retry_http_client = _create_retry_http_client()
    return _retry_http_client


async def close_ai_retry_http_client() -> None:
    """Close the process-scoped retry client during service shutdown."""

    global _retry_http_client
    client, _retry_http_client = _retry_http_client, None
    if client is not None and not client.is_closed:
        await client.aclose()
