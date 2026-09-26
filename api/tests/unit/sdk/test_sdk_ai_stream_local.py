"""AI stream facade tests: ``bifrost.ai.stream`` via ``engine_stream`` + SSE parser."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest


# =============================================================================
# SDK facade (no DB): ``ai.stream`` rides ``engine_stream`` + the SSE parser
# =============================================================================


class _SSEResponse:
    """Minimal httpx-like streaming response with scripted SSE lines."""

    def __init__(self, lines, *, delay: float = 0.0):
        self._lines = list(lines)
        self._delay = delay
        self.is_success = True
        self.status_code = 200
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.exited = True
        return False

    def aiter_lines(self):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        line = self._lines.pop(0)
        if self._delay:
            await asyncio.sleep(self._delay)
        return line


class _EngineStreamClient:
    """Records ``engine_stream`` calls and returns one scripted response."""

    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def engine_stream(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.response


def _sse(*payloads: str, delay: float = 0.0) -> _SSEResponse:
    return _SSEResponse([f"data: {payload}" for payload in payloads], delay=delay)


@pytest.mark.asyncio
async def test_facade_stream_chunks_match_http_shape():
    """``engine_stream`` carries the exact body; the SSE parser maps it."""
    from bifrost.ai import ai as ai_facade
    from bifrost.models import AIStreamChunk

    client = _EngineStreamClient(
        _sse(
            '{"content": "hello"}',
            '{"content": " world"}',
            '{"done": true, "input_tokens": 4, "output_tokens": 6}',
            "[DONE]",
        )
    )
    with patch("bifrost.ai.get_client", return_value=client):
        chunks = [chunk async for chunk in ai_facade.stream("Hi")]
    assert chunks == [
        AIStreamChunk(content="hello", done=False),
        AIStreamChunk(content=" world", done=False),
        AIStreamChunk(content="", done=True, input_tokens=4, output_tokens=6),
    ]
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/api/sdk/ai/stream")
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "Hi"}]
    assert kwargs["json"]["input_files"] == []
    assert client.response.exited is True


@pytest.mark.asyncio
async def test_facade_stream_error_event_becomes_empty_chunk_then_eof():
    """Provider-error parity: one empty non-done chunk, then EOF."""
    from bifrost.ai import ai as ai_facade

    client = _EngineStreamClient(_sse('{"content": "a"}', '{"error": "boom"}'))
    with patch("bifrost.ai.get_client", return_value=client):
        chunks = [chunk async for chunk in ai_facade.stream("Hi")]
    assert len(chunks) == 2
    assert chunks[0].content == "a" and chunks[0].done is False
    assert chunks[1].content == "" and chunks[1].done is False


@pytest.mark.asyncio
async def test_facade_stream_knowledge_and_files_composed_on_child():
    """knowledge= pre-search and file encoding stay on the child."""
    from bifrost import knowledge
    from bifrost.ai import ai as ai_facade
    from bifrost.models import AIInputFile, KnowledgeDocument

    doc = KnowledgeDocument(
        id=str(uuid4()),
        namespace="policies",
        content="Refunds within 30 days.",
        metadata={},
        score=0.9,
        organization_id=None,
        key="refund",
        created_at=None,
    )
    client = _EngineStreamClient(_sse('{"done": true}'))
    with (
        patch("bifrost.ai.get_client", return_value=client),
        patch.object(
            knowledge, "search", new=AsyncMock(return_value=[doc])
        ) as search,
    ):
        chunks = [
            chunk
            async for chunk in ai_facade.stream(
                "Refund policy?",
                knowledge=["policies"],
                files=[
                    AIInputFile(
                        filename="a.txt", content_type="text/plain", data=b"x"
                    )
                ],
            )
        ]
    assert chunks[-1].done is True
    search.assert_awaited_once()
    params = client.calls[0][2]["json"]
    assert any(
        "Refunds within 30 days" in m.get("content", "")
        for m in params["messages"]
    )
    assert params["input_files"][0]["filename"] == "a.txt"


@pytest.mark.asyncio
async def test_facade_close_exits_stream_and_second_stream_works():
    """Closing the generator exits the response; a later stream is independent."""
    from bifrost.ai import ai as ai_facade

    first_client = _EngineStreamClient(
        _sse('{"content": "a"}', '{"content": "b"}')
    )
    stream = ai_facade.stream("first")
    with patch("bifrost.ai.get_client", return_value=first_client):
        assert (await stream.__anext__()).content == "a"
        await stream.aclose()
    assert first_client.response.exited is True

    second_client = _EngineStreamClient(
        _sse(
            '{"content": "z"}',
            '{"done": true, "input_tokens": 1, "output_tokens": 2}',
            "[DONE]",
        )
    )
    with patch("bifrost.ai.get_client", return_value=second_client):
        second = [chunk async for chunk in ai_facade.stream("second")]
    assert [chunk.content for chunk in second] == ["z", ""]
    assert second[-1].done is True
    assert second[-1].output_tokens == 2


@pytest.mark.asyncio
async def test_facade_stream_cancellation_exits_response():
    """Cancelling the consuming task propagates and closes the response."""
    from bifrost.ai import ai as ai_facade

    class _Blocking(_SSEResponse):
        def __init__(self):
            super().__init__(["data: {\"content\": \"a\"}"])
            self.unblock = asyncio.Event()

        async def __anext__(self):
            if self._lines:
                return self._lines.pop(0)
            await self.unblock.wait()
            raise StopAsyncIteration

    response = _Blocking()
    client = _EngineStreamClient(response)
    started = asyncio.Event()

    async def _consume() -> None:
        async for _chunk in ai_facade.stream("Hi"):
            started.set()

    with patch("bifrost.ai.get_client", return_value=client):
        task = asyncio.create_task(_consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert response.exited is True


@pytest.mark.asyncio
async def test_facade_slow_gap_between_events_is_tolerated():
    """No SDK-level deadline: a slow provider gap under the read bound is fine.

    HTTPX's 30s per-read timeout (preserved on both clients, see
    ``BifrostClient.engine_stream``) is the only gap bound; there is no
    separate channel/stream deadline. A sub-second stall between events must
    still deliver every chunk.
    """
    from bifrost.ai import ai as ai_facade

    client = _EngineStreamClient(
        _sse(
            '{"content": "slow"}',
            '{"done": true, "input_tokens": 2, "output_tokens": 3}',
            delay=0.3,
        )
    )
    with patch("bifrost.ai.get_client", return_value=client):
        chunks = [chunk async for chunk in ai_facade.stream("Hi")]
    assert [(chunk.content, chunk.done) for chunk in chunks] == [
        ("slow", False),
        ("", True),
    ]


@pytest.mark.asyncio
async def test_external_network_stream_uses_same_parser_and_route():
    """External callers keep network SSE: same route, same chunk mapping."""
    from bifrost.ai import ai as ai_facade
    from bifrost.client import BifrostClient, get_engine_socket_path

    body = (
        b'data: {"content": "hi"}\n\n'
        b'data: {"done": true, "input_tokens": 1, "output_tokens": 2}\n\n'
        b"data: [DONE]\n\n"
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    assert get_engine_socket_path() is None
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as http:
        client = BifrostClient("https://example.test", "token")
        client._http = http
        client._http_loop = asyncio.get_running_loop()
        with patch("bifrost.ai.get_client", return_value=client):
            chunks = [chunk async for chunk in ai_facade.stream("Hi")]

    assert [chunk.content for chunk in chunks] == ["hi", ""]
    assert chunks[-1].done is True
    assert chunks[-1].input_tokens == 1
    assert chunks[-1].output_tokens == 2
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/sdk/ai/stream"
    assert seen["body"]["messages"] == [{"role": "user", "content": "Hi"}]
    assert seen["body"]["input_files"] == []


@pytest.mark.asyncio
async def test_external_network_stream_preflight_error_matches_local():
    """A rejected stream request raises the same public SDK error as a socket."""
    from bifrost.ai import ai as ai_facade
    from bifrost.client import BifrostAuthorizationError, BifrostClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "denied"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    ) as http:
        client = BifrostClient("https://example.test", "token")
        client._http = http
        client._http_loop = asyncio.get_running_loop()
        with (
            patch("bifrost.ai.get_client", return_value=client),
            pytest.raises(BifrostAuthorizationError, match="denied"),
        ):
            [chunk async for chunk in ai_facade.stream("Hi")]
