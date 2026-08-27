"""Process-scoped Pydantic AI HTTP retry transport.

Transport retries are deliberately the only retry layer for model requests.
Agent ``retries`` remain reserved for malformed tool/output correction.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx2
from pydantic_ai.models import get_user_agent
from pydantic_ai.retries import (
    AsyncHTTPX2TenacityTransport,
    RetryConfig,
)
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
MAX_RETRY_AFTER_SECONDS = 60.0
MAX_RETRY_WINDOW_SECONDS = 60.0
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
FALLBACK_WAIT_INITIAL_SECONDS = 2.0
FALLBACK_WAIT_MAX_SECONDS = 10.0


@dataclass(frozen=True)
class AIRetryContext:
    provider: str
    model: str
    surface: str


_retry_context: ContextVar[AIRetryContext | None] = ContextVar(
    "bifrost_ai_retry_context",
    default=None,
)
_retry_http_client: httpx2.AsyncClient | None = None


@contextmanager
def ai_retry_context(*, provider: str, model: str, surface: str) -> Iterator[None]:
    """Attach privacy-safe request identity to transport retry log records."""

    token = _retry_context.set(AIRetryContext(provider, model, surface))
    try:
        yield
    finally:
        _retry_context.reset(token)


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    retry_after_ms = response.headers.get("Retry-After-Ms")
    if retry_after_ms:
        try:
            return max(0.0, float(retry_after_ms) / 1000)
        except ValueError:
            pass

    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _validate_response(response: httpx2.Response) -> None:
    provider_retry = response.headers.get("x-should-retry", "").lower()
    if provider_retry == "false":
        return
    if provider_retry == "true":
        response.raise_for_status()
    if response.status_code in RETRYABLE_STATUS_CODES:
        response.raise_for_status()


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx2.HTTPStatusError):
        response = exc.response
        provider_retry = response.headers.get("x-should-retry", "").lower()
        if provider_retry == "false":
            return False
        if provider_retry == "true":
            retry_after = _retry_after_seconds(response)
            return retry_after is None or retry_after <= MAX_RETRY_AFTER_SECONDS
        if response.status_code not in RETRYABLE_STATUS_CODES:
            return False
        retry_after = _retry_after_seconds(response)
        if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
            return False
        return True
    return isinstance(exc, httpx2.TransportError)


def _retry_elapsed_seconds(state: RetryCallState) -> float:
    return max(state.seconds_since_start or 0.0, state.idle_for)


def _fallback_wait(state: RetryCallState) -> float:
    exponential_cap = min(
        FALLBACK_WAIT_INITIAL_SECONDS * (2 ** (state.attempt_number - 1)),
        FALLBACK_WAIT_MAX_SECONDS,
    )
    return random.uniform(exponential_cap / 2, exponential_cap)


def _wait_for_retry(state: RetryCallState) -> float:
    """Honor a useful Retry-After value, otherwise use bounded jitter.

    A zero delta or an already-expired HTTP date is not a useful recovery
    window. Treating either as authoritative synchronizes concurrent workers
    into an immediate replay, which is precisely the burst pattern this
    transport is intended to absorb.
    """

    exc = state.outcome.exception() if state.outcome is not None else None
    response = getattr(exc, "response", None)
    delay: float | None = None
    if response is not None:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None and retry_after > 0:
            delay = retry_after
    if delay is None:
        delay = _fallback_wait(state)
    remaining = max(0.0, MAX_RETRY_WINDOW_SECONDS - _retry_elapsed_seconds(state))
    return min(delay, remaining)


def _stop_retrying(state: RetryCallState) -> bool:
    if state.attempt_number >= MAX_ATTEMPTS:
        return True
    elapsed = _retry_elapsed_seconds(state)
    if elapsed >= MAX_RETRY_WINDOW_SECONDS:
        return True
    return elapsed + state.upcoming_sleep > MAX_RETRY_WINDOW_SECONDS


def _http_retry_budget_available(
    exc: httpx2.HTTPStatusError,
    state: RetryCallState,
) -> bool:
    if state.attempt_number >= MAX_ATTEMPTS:
        return False
    elapsed = _retry_elapsed_seconds(state)
    if elapsed >= MAX_RETRY_WINDOW_SECONDS:
        return False
    retry_after = _retry_after_seconds(exc.response)
    return retry_after is None or elapsed + retry_after <= MAX_RETRY_WINDOW_SECONDS


def _log_before_sleep(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome is not None else None
    response = getattr(exc, "response", None)
    context = _retry_context.get()
    logger.warning(
        "ai_provider_request_retry",
        extra={
            "provider": context.provider if context else None,
            "model": context.model if context else None,
            "surface": context.surface if context else None,
            "attempt": state.attempt_number,
            "max_attempts": MAX_ATTEMPTS,
            "status_code": getattr(response, "status_code", None),
            "retry_after_seconds": (
                _retry_after_seconds(response) if response is not None else None
            ),
            "sleep_seconds": (
                state.next_action.sleep if state.next_action is not None else None
            ),
            "error_type": type(exc).__name__ if exc is not None else None,
        },
    )


def _log_terminal(
    exc: BaseException,
    *,
    attempt: int,
    total_sleep_seconds: float,
) -> None:
    response = getattr(exc, "response", None)
    context = _retry_context.get()
    logger.warning(
        "ai_provider_request_terminal",
        extra={
            "provider": context.provider if context else None,
            "model": context.model if context else None,
            "surface": context.surface if context else None,
            "attempt": attempt,
            "max_attempts": MAX_ATTEMPTS,
            "status_code": getattr(response, "status_code", None),
            "retry_after_seconds": (
                _retry_after_seconds(response) if response is not None else None
            ),
            "sleep_seconds": None,
            "total_sleep_seconds": total_sleep_seconds,
            "error_type": type(exc).__name__,
        },
    )


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
