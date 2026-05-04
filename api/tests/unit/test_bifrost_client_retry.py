"""Unit tests for SDK transient-5xx retry behavior in ``BifrostClient``.

Workstream E of issue #171: idempotent SDK calls retry on 502/503/504 during
rolling API deploys. Non-idempotent methods (POST/PATCH) never auto-retry.

Uses ``httpx.MockTransport`` to count transport calls without hitting the
network. ``asyncio.sleep`` and ``time.sleep`` are patched so the 36s retry
budget elapses instantly.
"""

from __future__ import annotations

from typing import Callable
from unittest.mock import patch

import httpx
import pytest

from bifrost.client import BifrostClient


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> BifrostClient:
    """Build a BifrostClient whose async + sync httpx clients use a mock transport.

    Bypasses ``__init__`` so we don't open real httpx clients and so we can
    inject the mock transport into both ``_http`` and ``_sync_http``.
    """
    client = BifrostClient.__new__(BifrostClient)
    client.api_url = "http://test.local"
    client._access_token = "test-token"
    client._http = httpx.AsyncClient(
        base_url=client.api_url,
        headers={"Authorization": f"Bearer {client._access_token}"},
        transport=httpx.MockTransport(handler),
    )
    client._http_loop = None
    client._sync_http = httpx.Client(
        base_url=client.api_url,
        headers={"Authorization": f"Bearer {client._access_token}"},
        transport=httpx.MockTransport(handler),
    )
    client._context = None
    return client


def _seq_handler(statuses: list[int]) -> tuple[Callable[[httpx.Request], httpx.Response], list[int]]:
    """Return a handler that yields the given status codes in order, plus a call counter list."""
    counter: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        idx = len(counter)
        counter.append(1)
        # If we run past the end of the list, the test queued too few responses.
        if idx >= len(statuses):
            raise AssertionError(
                f"Mock transport received unexpected request #{idx + 1} "
                f"to {request.method} {request.url}"
            )
        return httpx.Response(statuses[idx], json={"ok": True})

    return handler, counter


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """Skip real backoff so tests run instantly (~36s budget would otherwise dominate)."""
    async def _async_noop(_delay: float) -> None:
        return None

    with patch("bifrost.client.asyncio.sleep", side_effect=_async_noop), \
         patch("bifrost.client.time.sleep", return_value=None):
        yield


@pytest.fixture
def force_no_refresh():
    """Patch ``_refresh_and_update`` to always return False so 401 paths are inert.

    The 5xx retry tests don't exercise 401, but the inner closure inspects
    ``response.status_code == 401``; ensuring refresh returns False keeps the
    test focused on 5xx behavior.
    """
    async def _no_refresh(_self) -> bool:
        return False

    with patch.object(BifrostClient, "_refresh_and_update", _no_refresh):
        yield


class _FixedLoopClient(BifrostClient):
    """Test subclass: always return our pre-built transport-backed _http client.

    The default ``_get_async_client`` rebuilds the client when it detects an
    event-loop change, which would discard our mock transport.
    """

    def _get_async_client(self) -> httpx.AsyncClient:  # type: ignore[override]
        assert self._http is not None
        return self._http


def _make_fixed_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> BifrostClient:
    client = _FixedLoopClient.__new__(_FixedLoopClient)
    client.api_url = "http://test.local"
    client._access_token = "test-token"
    client._http = httpx.AsyncClient(
        base_url=client.api_url,
        headers={"Authorization": f"Bearer {client._access_token}"},
        transport=httpx.MockTransport(handler),
    )
    client._http_loop = None
    client._sync_http = httpx.Client(
        base_url=client.api_url,
        headers={"Authorization": f"Bearer {client._access_token}"},
        transport=httpx.MockTransport(handler),
    )
    client._context = None
    return client


# ------------------------- async tests -------------------------


@pytest.mark.asyncio
async def test_async_get_retries_until_success(force_no_refresh):
    """GET 503,503,200 → succeeds, 3 transport calls."""
    handler, calls = _seq_handler([503, 503, 200])
    client = _make_fixed_client(handler)
    try:
        response = await client.get("/api/things")
        assert response.status_code == 200
        assert len(calls) == 3
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_post_does_not_retry_on_503(force_no_refresh):
    """POST 503 → returns 503 with 1 transport call (no retry on non-idempotent)."""
    handler, calls = _seq_handler([503])
    client = _make_fixed_client(handler)
    try:
        response = await client.post("/api/things")
        assert response.status_code == 503
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_put_exhausts_retry_budget(force_no_refresh):
    """PUT 503 x6 → returns the 6th (final) 503 after 1 + 5 retries = 6 transport calls."""
    handler, calls = _seq_handler([503] * 6)
    client = _make_fixed_client(handler)
    try:
        response = await client.put("/api/things/1")
        assert response.status_code == 503
        assert len(calls) == 6
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_get_success_first_try(force_no_refresh):
    """GET 200 → 1 transport call."""
    handler, calls = _seq_handler([200])
    client = _make_fixed_client(handler)
    try:
        response = await client.get("/api/things")
        assert response.status_code == 200
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_delete_504_then_200(force_no_refresh):
    """DELETE 504,200 → succeeds, 2 transport calls."""
    handler, calls = _seq_handler([504, 200])
    client = _make_fixed_client(handler)
    try:
        response = await client.delete("/api/things/1")
        assert response.status_code == 200
        assert len(calls) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_get_500_not_retried(force_no_refresh):
    """GET 500 → returns 500, 1 transport call (500 is not in transient set)."""
    handler, calls = _seq_handler([500])
    client = _make_fixed_client(handler)
    try:
        response = await client.get("/api/things")
        assert response.status_code == 500
        assert len(calls) == 1
    finally:
        await client.close()


# ------------------------- sync tests -------------------------


def test_sync_get_retries_on_503(force_no_refresh):
    """Sync GET retries on 503."""
    handler, calls = _seq_handler([503, 200])
    client = _make_fixed_client(handler)
    try:
        response = client.get_sync("/api/things")
        assert response.status_code == 200
        assert len(calls) == 2
    finally:
        client._sync_http.close()


def test_sync_post_does_not_retry_on_503(force_no_refresh):
    """Sync POST does NOT retry on 503."""
    handler, calls = _seq_handler([503])
    client = _make_fixed_client(handler)
    try:
        response = client.post_sync("/api/things")
        assert response.status_code == 503
        assert len(calls) == 1
    finally:
        client._sync_http.close()
