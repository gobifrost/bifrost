"""Tests for releasing allocator arenas after large table requests."""
from __future__ import annotations

import asyncio

from src.core import allocation_trim


def _scope(path: str, content_length: int = 0) -> dict:
    headers = [] if not content_length else [(b"content-length", str(content_length).encode())]
    return {"type": "http", "path": path, "headers": headers}


def test_trims_after_large_table_request(monkeypatch):
    calls: list[str] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    monkeypatch.setattr(allocation_trim.gc, "collect", lambda: calls.append("gc"))
    monkeypatch.setattr(allocation_trim, "trim_malloc", lambda: calls.append("trim"))

    asyncio.run(
        allocation_trim.AllocationTrimMiddleware(app)(
            _scope("/api/tables/id/documents/batch", 64 * 1024), receive, send
        )
    )

    assert calls == ["gc", "trim"]


def test_trims_after_large_table_response(monkeypatch):
    calls: list[str] = []

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"65536")],
            }
        )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    monkeypatch.setattr(allocation_trim.gc, "collect", lambda: calls.append("gc"))
    monkeypatch.setattr(allocation_trim, "trim_malloc", lambda: calls.append("trim"))

    asyncio.run(
        allocation_trim.AllocationTrimMiddleware(app)(
            _scope("/api/tables/id/documents/query"), receive, send
        )
    )

    assert calls == ["gc", "trim"]


def test_skips_small_or_non_table_requests(monkeypatch):
    calls: list[str] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    monkeypatch.setattr(allocation_trim.gc, "collect", lambda: calls.append("gc"))
    monkeypatch.setattr(allocation_trim, "trim_malloc", lambda: calls.append("trim"))
    middleware = allocation_trim.AllocationTrimMiddleware(app)

    asyncio.run(middleware(_scope("/api/tables/id/documents/batch", 1024), receive, send))
    asyncio.run(middleware(_scope("/api/agents/id", 64 * 1024), receive, send))

    assert calls == []
