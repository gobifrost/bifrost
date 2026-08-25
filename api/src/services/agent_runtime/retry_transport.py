"""Process-scoped Pydantic AI HTTP retry transport.

Transport retries are deliberately the only retry layer for model requests.
Agent ``retries`` remain reserved for malformed tool/output correction.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator

import httpx2
from pydantic_ai.models import get_user_agent
from pydantic_ai.retries import (
    AsyncHTTPX2TenacityTransport,
    RetryConfig,
    wait_retry_after,
)
from tenacity import (
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 60.0
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


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
    if response.status_code in RETRYABLE_STATUS_CODES:
        response.raise_for_status()


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx2.HTTPStatusError):
        response = exc.response
        if response.status_code not in RETRYABLE_STATUS_CODES:
            return False
        retry_after = _retry_after_seconds(response)
        if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
            return False
        return True
    return isinstance(exc, httpx2.TransportError)


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


def _create_retry_transport(
    wrapped: httpx2.AsyncBaseTransport | None = None,
) -> AsyncHTTPX2TenacityTransport:
    return AsyncHTTPX2TenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception(_should_retry),
            wait=wait_retry_after(
                fallback_strategy=wait_random_exponential(multiplier=1, max=4),
                max_wait=MAX_RETRY_AFTER_SECONDS,
            ),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            before_sleep=_log_before_sleep,
            reraise=True,
        ),
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
